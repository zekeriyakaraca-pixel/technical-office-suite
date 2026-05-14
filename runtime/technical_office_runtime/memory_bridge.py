from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS extraction_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_fingerprint TEXT NOT NULL,
    job_id TEXT NOT NULL,
    plate_specs TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    source TEXT NOT NULL DEFAULT 'codex',
    created_at TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_fingerprint ON extraction_patterns(pdf_fingerprint);
CREATE INDEX IF NOT EXISTS idx_confidence ON extraction_patterns(confidence DESC);
CREATE TABLE IF NOT EXISTS page_classification_hints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_sha256 TEXT NOT NULL,
    job_id TEXT NOT NULL,
    excluded_pages TEXT NOT NULL,
    note TEXT,
    source TEXT NOT NULL DEFAULT 'manager_decision',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_page_hints_sha256 ON page_classification_hints(pdf_sha256);
"""


class MemoryBridge:
    """
    Lightweight SQLite-based cross-job memory for extraction patterns.

    Inspired by Ruflo's Auto Memory Bridge. Uses PDF fingerprinting
    instead of full vector embeddings — sufficient for this project's scale.

    Confidence scoring:
      - Access boost: +0.03 per lookup hit
      - Staleness: not applied (use access_count + created_at instead)
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DB_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def fingerprint(pdf_text_sample: str, page_count: int, file_size_bytes: int) -> str:
        """Deterministic fingerprint from PDF content features."""
        raw = f"{pdf_text_sample[:512]}|{page_count}|{file_size_bytes}"
        return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()

    def record_extraction(
        self,
        pdf_fingerprint: str,
        job_id: str,
        plate_specs: list[dict[str, Any]],
        confidence: float,
        source: str = "codex",
    ) -> None:
        """Save a successful extraction pattern to memory."""
        now = datetime.now().astimezone().isoformat()
        specs_json = json.dumps(plate_specs, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO extraction_patterns
                  (pdf_fingerprint, job_id, plate_specs, confidence, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (pdf_fingerprint, job_id, specs_json, confidence, source, now),
            )
        log.info("memory_bridge.recorded", job_id=job_id, fingerprint=pdf_fingerprint[:12], confidence=confidence)

    def find_similar(
        self,
        pdf_fingerprint: str,
        threshold: float = 0.85,
    ) -> list[dict[str, Any]] | None:
        """
        Look up stored plate_specs for a matching fingerprint.
        Only returns results with confidence >= threshold.
        On hit, boosts confidence by +0.03 (capped at 1.0).
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, plate_specs, confidence, job_id, source
                FROM extraction_patterns
                WHERE pdf_fingerprint = ?
                  AND confidence >= ?
                ORDER BY confidence DESC, access_count DESC
                LIMIT 1
                """,
                (pdf_fingerprint, threshold),
            ).fetchone()

            if row is None:
                return None

            row_id = row["id"]
            new_confidence = min(1.0, row["confidence"] + 0.03)
            now = datetime.now().astimezone().isoformat()
            conn.execute(
                """
                UPDATE extraction_patterns
                SET access_count = access_count + 1,
                    last_accessed_at = ?,
                    confidence = ?
                WHERE id = ?
                """,
                (now, new_confidence, row_id),
            )

        try:
            specs = json.loads(row["plate_specs"])
        except json.JSONDecodeError:
            return None

        log.info(
            "memory_bridge.hit",
            fingerprint=pdf_fingerprint[:12],
            source_job=row["job_id"],
            confidence=row["confidence"],
        )
        return specs if isinstance(specs, list) else None

    def get_pattern_stats(self) -> dict[str, Any]:
        """Return aggregate statistics for the dashboard metrics panel."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM extraction_patterns").fetchone()[0]
            high_conf = conn.execute(
                "SELECT COUNT(*) FROM extraction_patterns WHERE confidence >= 0.85"
            ).fetchone()[0]
            avg_conf_row = conn.execute("SELECT AVG(confidence) FROM extraction_patterns").fetchone()
            avg_conf = round(float(avg_conf_row[0]), 3) if avg_conf_row[0] is not None else 0.0
            hits = conn.execute("SELECT SUM(access_count) FROM extraction_patterns").fetchone()[0] or 0
            recent = conn.execute(
                "SELECT job_id, confidence, source, created_at FROM extraction_patterns ORDER BY created_at DESC LIMIT 5"
            ).fetchall()

        return {
            "total_patterns": total,
            "high_confidence_patterns": high_conf,
            "avg_confidence": avg_conf,
            "total_hits": hits,
            "recent": [dict(r) for r in recent],
        }

    def list_patterns(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT pdf_fingerprint, job_id, confidence, source, created_at, access_count
                FROM extraction_patterns
                ORDER BY confidence DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_patterns(self, limit: int = 3) -> list[dict[str, Any]]:
        """Return the most recently created extraction patterns for manager context."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT job_id, confidence, source, created_at,
                       json_array_length(plate_specs) AS pattern_count
                FROM extraction_patterns
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_page_exclusion(
        self,
        pdf_sha256: str,
        job_id: str,
        excluded_pages: list[int],
        note: str = "",
    ) -> None:
        """Save a manager page-exclusion decision keyed by PDF SHA256 hash."""
        now = datetime.now().astimezone().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO page_classification_hints "
                "(pdf_sha256, job_id, excluded_pages, note, source, created_at) "
                "VALUES (?, ?, ?, ?, 'manager_decision', ?)",
                (pdf_sha256, job_id, json.dumps(excluded_pages), note[:500], now),
            )
        log.info(
            "memory_bridge.page_exclusion_recorded",
            sha256=pdf_sha256[:12],
            job_id=job_id,
            pages=excluded_pages,
        )

    def find_page_hints(self, pdf_sha256: str) -> list[int]:
        """Return excluded page numbers from the most recent matching manager decision."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT excluded_pages FROM page_classification_hints "
                "WHERE pdf_sha256 = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (pdf_sha256,),
            ).fetchone()
        if row is None:
            return []
        try:
            result = json.loads(row[0])
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, TypeError):
            return []


_bridge_instances: dict[str, MemoryBridge] = {}


def get_memory_bridge(workspace_root: Path) -> MemoryBridge:
    db_path = workspace_root / "memory" / "extraction_patterns.db"
    key = str(db_path)
    if key not in _bridge_instances:
        _bridge_instances[key] = MemoryBridge(db_path)
    return _bridge_instances[key]


def compute_pdf_fingerprint(pdf_path: Path) -> str:
    """Compute fingerprint from a PDF file on disk."""
    try:
        import fitz  # type: ignore[import-not-found]
        doc = fitz.open(pdf_path)
        try:
            first_page_text = doc[0].get_text() if len(doc) > 0 else ""
        finally:
            doc.close()
        page_count = len(doc)
    except Exception:
        first_page_text = ""
        page_count = 0
    file_size = pdf_path.stat().st_size if pdf_path.exists() else 0
    return MemoryBridge.fingerprint(first_page_text, page_count, file_size)
