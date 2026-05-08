from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


_JOB_ID_RE = re.compile(r"\b[A-Za-z]+-[0-9][A-Za-z0-9_.-]*\b")
_PDF_RE = re.compile(r"\b[A-Za-z0-9_. -]+\.pdf\b", re.IGNORECASE)
_POZ_RE = re.compile(r"\b[0-9]{2,6}\b")

_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    job_ids TEXT NOT NULL DEFAULT '[]',
    poz_nos TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_manager_events_session_id ON conversation_events(session_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_manager_events_created_at ON conversation_events(created_at DESC);

CREATE TABLE IF NOT EXISTS manager_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    fact_type TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    confidence REAL NOT NULL DEFAULT 0.7,
    source TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, fact_type, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_manager_facts_job_id ON manager_facts(job_id, status, updated_at DESC);
"""


@dataclass(frozen=True)
class ManagerMemoryRecall:
    primary_job_id: str | None
    facts: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]

    def to_payload(self) -> dict[str, Any]:
        return {
            "primary_job_id": self.primary_job_id,
            "facts": self.facts,
            "recent_events": self.recent_events,
        }

    def prompt_block(self) -> str:
        if not self.primary_job_id and not self.facts and not self.recent_events:
            return "(ilgili kalici hafiza kaydi yok)"

        lines: list[str] = []
        if self.primary_job_id:
            lines.append(f"Ilgili is: `{self.primary_job_id}`")
        if self.facts:
            lines.append("Acik/ilgili notlar:")
            for fact in self.facts[:8]:
                status = fact.get("status") or "open"
                fact_type = fact.get("fact_type") or "note"
                content = _shorten(str(fact.get("content") or ""), 260)
                lines.append(f"- [{status}] {fact_type}: {content}")
        if self.recent_events:
            lines.append("Son ilgili konusma:")
            for event in self.recent_events[:8]:
                role = event.get("role") or "user"
                content = _shorten(str(event.get("content") or ""), 220)
                lines.append(f"- {role}: {content}")
        return "\n".join(lines)


class ManagerMemory:
    """Persistent manager memory for conversations, job facts, and recall.

    This is intentionally deterministic. It is not a vector memory and it does
    not promote raw chat into agent skill files. The manager first records every
    turn, then retrieves job-scoped notes before answering future turns.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.db_path = workspace_root / "memory" / "manager_memory.db"
        self.vault_root = workspace_root / "manager_vault"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DB_SCHEMA)
            conn.execute(
                "DELETE FROM manager_facts WHERE fact_type = 'issue' AND content LIKE 'Mevcut isler:%'"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def record_turn(
        self,
        *,
        session_id: str,
        user_text: str,
        assistant_text: str,
        selected_job_id: str | None = None,
    ) -> None:
        user_context = extract_memory_context(user_text, selected_job_id=selected_job_id)
        assistant_context = extract_memory_context(assistant_text, selected_job_id=user_context.primary_job_id)
        if _looks_like_generic_job_listing(assistant_text):
            assistant_context = ExtractedMemoryContext(job_ids=[], poz_nos=[], pdf_names=[], tags=[])
        now = _now_iso()
        facts = _facts_from_message("user", user_text, user_context) + _facts_from_message(
            "assistant", assistant_text, assistant_context
        )

        with self._connect() as conn:
            self._insert_event(conn, session_id, "user", user_text, user_context, now)
            self._insert_event(conn, session_id, "assistant", assistant_text, assistant_context, _now_iso())
            for fact in facts:
                self._upsert_fact(conn, fact)

        for job_id in sorted({str(fact["job_id"]) for fact in facts}):
            self.export_job_markdown(job_id)

    def recall(
        self,
        *,
        session_id: str,
        message: str,
        selected_job_id: str | None = None,
        history: list[dict[str, Any]] | None = None,
        fact_limit: int = 8,
        event_limit: int = 8,
    ) -> ManagerMemoryRecall:
        context = extract_memory_context(message, selected_job_id=selected_job_id)
        job_id = context.primary_job_id
        if not job_id:
            job_id = _job_id_from_history(history or [])
        if not job_id:
            job_id = self.recent_job_id(session_id)

        facts = self.list_facts(job_id, limit=fact_limit) if job_id else []
        events = self.recent_events(session_id=session_id, job_id=job_id, limit=event_limit)
        return ManagerMemoryRecall(primary_job_id=job_id, facts=facts, recent_events=events)

    def recent_job_id(self, session_id: str) -> str | None:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, job_ids, tags
                FROM conversation_events
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 80
                """,
                (session_id,),
            ).fetchall()
        for row in rows:
            if _looks_like_generic_job_listing(str(row["content"] or "")):
                continue
            if row["role"] == "assistant" and not _loads_list(row["tags"]):
                continue
            job_ids = _loads_list(row["job_ids"])
            if job_ids:
                return str(job_ids[-1])
        return None

    def list_facts(self, job_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT job_id, fact_type, content, status, confidence, source, evidence, created_at, updated_at
                FROM manager_facts
                WHERE job_id = ?
                  AND NOT (fact_type = 'issue' AND content LIKE 'Mevcut isler:%')
                ORDER BY
                    CASE status WHEN 'open' THEN 0 WHEN 'planned' THEN 1 WHEN 'decision' THEN 2 ELSE 3 END,
                    updated_at DESC
                LIMIT ?
                """,
                (job_id, max(1, min(limit, 200))),
            ).fetchall()
        return [_row_to_fact(row) for row in rows]

    def recent_events(
        self,
        *,
        session_id: str | None = None,
        job_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        params: list[Any] = []
        where: list[str] = []
        if session_id:
            where.append("session_id = ?")
            params.append(session_id)
        if job_id:
            where.append("job_ids LIKE ?")
            params.append(f'%"{job_id}"%')
        sql = """
            SELECT session_id, role, content, job_ids, poz_nos, tags, created_at
            FROM conversation_events
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_event(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            event_count = conn.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0]
            fact_count = conn.execute("SELECT COUNT(*) FROM manager_facts").fetchone()[0]
            open_fact_count = conn.execute("SELECT COUNT(*) FROM manager_facts WHERE status = 'open'").fetchone()[0]
            job_count = conn.execute("SELECT COUNT(DISTINCT job_id) FROM manager_facts").fetchone()[0]
        return {
            "events": event_count,
            "facts": fact_count,
            "open_facts": open_fact_count,
            "jobs_with_facts": job_count,
            "db_path": str(self.db_path),
            "vault_path": str(self.vault_root),
        }

    def export_job_markdown(self, job_id: str) -> Path | None:
        facts = self.list_facts(job_id, limit=100)
        events = self.recent_events(job_id=job_id, limit=30)
        if not facts and not events:
            return None
        target = self.vault_root / "jobs" / f"{_safe_filename(job_id)}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# {job_id}",
            "",
            f"Updated: {_now_iso()}",
            "",
            "## Facts",
        ]
        if facts:
            for fact in facts:
                lines.append(
                    f"- [{fact.get('status')}] {fact.get('fact_type')}: {_shorten(str(fact.get('content') or ''), 500)}"
                )
        else:
            lines.append("- No recorded facts.")
        lines.extend(["", "## Recent Conversation"])
        if events:
            for event in reversed(events[-20:]):
                role = event.get("role") or "user"
                lines.append(f"- {event.get('created_at')} {role}: {_shorten(str(event.get('content') or ''), 500)}")
        else:
            lines.append("- No recorded events.")
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        role: str,
        content: str,
        context: "ExtractedMemoryContext",
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO conversation_events (session_id, role, content, job_ids, poz_nos, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                role,
                content.strip(),
                json.dumps(context.job_ids, ensure_ascii=False),
                json.dumps(context.poz_nos, ensure_ascii=False),
                json.dumps(context.tags, ensure_ascii=False),
                created_at,
            ),
        )

    def _upsert_fact(self, conn: sqlite3.Connection, fact: dict[str, Any]) -> None:
        now = _now_iso()
        content = str(fact["content"]).strip()
        content_hash = _hash_content(content)
        evidence = fact.get("evidence") if isinstance(fact.get("evidence"), dict) else {}
        conn.execute(
            """
            INSERT INTO manager_facts
              (job_id, fact_type, content, content_hash, status, confidence, source, evidence, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, fact_type, content_hash) DO UPDATE SET
                status = excluded.status,
                confidence = MAX(manager_facts.confidence, excluded.confidence),
                evidence = excluded.evidence,
                updated_at = excluded.updated_at
            """,
            (
                fact["job_id"],
                fact["fact_type"],
                content,
                content_hash,
                fact.get("status") or "open",
                float(fact.get("confidence") or 0.7),
                fact.get("source") or "manager_chat",
                json.dumps(evidence, ensure_ascii=False),
                now,
                now,
            ),
        )


@dataclass(frozen=True)
class ExtractedMemoryContext:
    job_ids: list[str]
    poz_nos: list[str]
    pdf_names: list[str]
    tags: list[str]

    @property
    def primary_job_id(self) -> str | None:
        return self.job_ids[0] if self.job_ids else None


def extract_memory_context(text: str, *, selected_job_id: str | None = None) -> ExtractedMemoryContext:
    visible = _visible_text(text)
    job_ids = _unique([selected_job_id] if selected_job_id else [])
    job_ids = _unique(job_ids + _JOB_ID_RE.findall(visible))
    pdf_names = _unique([match.strip(" .,;:") for match in _PDF_RE.findall(visible)])
    poz_source = _JOB_ID_RE.sub(" ", visible)
    poz_nos = _unique(_POZ_RE.findall(poz_source))
    tags = _tags_for_text(visible)
    return ExtractedMemoryContext(job_ids=job_ids, poz_nos=poz_nos, pdf_names=pdf_names, tags=tags)


def get_manager_memory(workspace_root: Path) -> ManagerMemory:
    key = str((workspace_root / "memory" / "manager_memory.db").resolve())
    if key not in _manager_memory_instances:
        _manager_memory_instances[key] = ManagerMemory(workspace_root)
    return _manager_memory_instances[key]


_manager_memory_instances: dict[str, ManagerMemory] = {}


def _facts_from_message(role: str, content: str, context: ExtractedMemoryContext) -> list[dict[str, Any]]:
    if not context.job_ids:
        return []
    lower = _normalize_turkish(_visible_text(content))
    fact_type: str | None = None
    status = "open"
    confidence = 0.72
    if role == "assistant" and "karar:" in lower:
        fact_type = "manager_decision"
        status = "decision"
        confidence = 0.78
    elif any(tag in context.tags for tag in ("eksik_sayfa_poz", "geometri", "pah_kose", "poligon_kontur")) or _has_qc_block_signal(lower):
        fact_type = "issue"
        status = "open"
        confidence = 0.82
    elif "temiz_baslat" in context.tags or "yeniden_baslama" in context.tags:
        fact_type = "restart_intent"
        status = "planned"
        confidence = 0.75
    if not fact_type:
        return []
    evidence = {
        "role": role,
        "tags": context.tags,
        "poz_nos": context.poz_nos,
        "pdf_names": context.pdf_names,
    }
    return [
        {
            "job_id": job_id,
            "fact_type": fact_type,
            "content": _visible_text(content),
            "status": status,
            "confidence": confidence,
            "source": "teknik-ofis-muduru-chat",
            "evidence": evidence,
        }
        for job_id in context.job_ids
    ]


def _tags_for_text(text: str) -> list[str]:
    lower = _normalize_turkish(text)
    tags: list[str] = []
    if any(word in lower for word in ("hata", "yanlis", "sorun", "sikinti", "eksik", "duzelt")):
        tags.append("issue")
    if any(word in lower for word in ("sayfa", "26 sayfa", "toplam")) and any(word in lower for word in ("uretti", "uretilen", "aday", "poz")):
        tags.append("eksik_sayfa_poz")
    if any(word in lower for word in ("pah", "kose", "kose", "chamfer")):
        tags.append("pah_kose")
        tags.append("geometri")
    if "poligon" in lower or "polygon" in lower:
        tags.append("poligon_kontur")
        tags.append("geometri")
    if "gorsel analiz" in lower or "gorselanaliz" in lower or "vision" in lower:
        tags.append("gorsel_analiz")
    if "qc" in lower or "teslim" in lower or ("partlist" in lower and _has_qc_block_signal(lower)):
        tags.append("qc_blokaj")
    if any(phrase in lower for phrase in ("temiz baslat", "resetle", "sifirla")):
        tags.append("temiz_baslat")
    if any(phrase in lower for phrase in ("bastan basla", "yeniden basla", "yeniden baslayacagiz", "bastan baslamak")):
        tags.append("yeniden_baslama")
    return _unique(tags)


def _has_qc_block_signal(lower: str) -> bool:
    return any(
        phrase in lower
        for phrase in (
            "blok",
            "acilmadi",
            "acilmamali",
            "kapanmadi",
            "kapali",
            "tamamlanmis kabul edilmemeli",
            "teslim kapisi",
            "partlist kapisi",
        )
    )


def _looks_like_generic_job_listing(text: str) -> bool:
    lower = _normalize_turkish(_visible_text(text))
    return lower.startswith("mevcut isler:") or "su an gordugum isler:" in lower


def _job_id_from_history(history: list[dict[str, Any]]) -> str | None:
    for item in reversed(history[-40:]):
        content = item.get("content")
        if not isinstance(content, str):
            continue
        if _looks_like_generic_job_listing(content):
            continue
        matches = _JOB_ID_RE.findall(_visible_text(content))
        if matches:
            return matches[-1]
    return None


def _row_to_fact(row: sqlite3.Row) -> dict[str, Any]:
    evidence = {}
    try:
        evidence = json.loads(row["evidence"])
    except Exception:
        evidence = {}
    return {
        "job_id": row["job_id"],
        "fact_type": row["fact_type"],
        "content": row["content"],
        "status": row["status"],
        "confidence": row["confidence"],
        "source": row["source"],
        "evidence": evidence if isinstance(evidence, dict) else {},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "job_ids": _loads_list(row["job_ids"]),
        "poz_nos": _loads_list(row["poz_nos"]),
        "tags": _loads_list(row["tags"]),
        "created_at": row["created_at"],
    }


def _visible_text(text: str) -> str:
    markers = ("\n\n[Secili is baglami:", "\n\n[Mudur hafiza baglami:")
    cut = len(text)
    for marker in markers:
        idx = text.find(marker)
        if idx >= 0:
            cut = min(cut, idx)
    return text[:cut].strip()


def _loads_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item is not None]


def _unique(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value:
            continue
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _normalize_turkish(text: str) -> str:
    lowered = text.lower().translate(
        str.maketrans(
            {
                "\u0131": "i",
                "\u0130": "i",
                "\u011f": "g",
                "\u011e": "g",
                "\u00fc": "u",
                "\u00dc": "u",
                "\u015f": "s",
                "\u015e": "s",
                "\u00f6": "o",
                "\u00d6": "o",
                "\u00e7": "c",
                "\u00c7": "c",
            }
        )
    )
    normalized = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:120] or "job"


def _shorten(value: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _hash_content(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()


def _now_iso() -> str:
    return datetime.fromtimestamp(time.time()).astimezone().isoformat()
