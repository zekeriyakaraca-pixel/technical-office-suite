from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class AuditLogger:
    """
    Immutable append-only audit trail for manager approvals, QC results, and deliveries.
    Stored as JSONL: workspace/audit/audit_trail.jsonl

    Inspired by Ruflo's zero-trust federation audit capabilities.
    """

    def __init__(self, audit_dir: Path) -> None:
        self._path = audit_dir / "audit_trail.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, entry: dict[str, Any]) -> None:
        entry.setdefault("timestamp", datetime.now().astimezone().isoformat())
        line = json.dumps(entry, ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        log.info("audit.appended", audit_event=entry.get("event"), job_id=entry.get("job_id"))

    def log_job_created(self, job_id: str, project_name: str, pdf_count: int) -> None:
        self._append({
            "event": "job_created",
            "job_id": job_id,
            "project_name": project_name,
            "pdf_count": pdf_count,
        })

    def log_run_started(self, job_id: str, autocad_policy: str) -> None:
        self._append({
            "event": "run_started",
            "job_id": job_id,
            "autocad_policy": autocad_policy,
        })

    def log_approval(self, job_id: str, agent: str, plate_count: int, specs_summary: list[str]) -> None:
        self._append({
            "event": "candidates_approved",
            "job_id": job_id,
            "approved_by": agent,
            "plate_count": plate_count,
            "poz_nos": specs_summary,
        })

    def log_qc_result(self, job_id: str, ok: bool, issues: list[dict[str, Any]]) -> None:
        self._append({
            "event": "qc_result",
            "job_id": job_id,
            "ok": ok,
            "issue_count": len(issues),
            "issues": issues[:10],  # cap to avoid huge entries
        })

    def log_delivery(self, job_id: str, files: list[str]) -> None:
        self._append({
            "event": "delivery",
            "job_id": job_id,
            "file_count": len(files),
            "files": files,
        })

    def log_state_transition(self, job_id: str, from_state: str, to_state: str, reason: str | None) -> None:
        self._append({
            "event": "state_transition",
            "job_id": job_id,
            "from_state": from_state,
            "to_state": to_state,
            "reason": reason,
        })

    def log_partlist_created(self, job_id: str, row_count: int, ok: bool) -> None:
        self._append({
            "event": "partlist_created",
            "job_id": job_id,
            "row_count": row_count,
            "ok": ok,
        })

    def read_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return last N audit entries (newest last)."""
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").splitlines()
        entries: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def read_for_job(self, job_id: str) -> list[dict[str, Any]]:
        """Return all audit entries for a specific job."""
        return [e for e in self.read_recent(limit=10_000) if e.get("job_id") == job_id]


_audit_instances: dict[str, AuditLogger] = {}


def get_audit_logger(workspace_root: Path) -> AuditLogger:
    audit_dir = workspace_root / "audit"
    key = str(audit_dir)
    if key not in _audit_instances:
        _audit_instances[key] = AuditLogger(audit_dir)
    return _audit_instances[key]
