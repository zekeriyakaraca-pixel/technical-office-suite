from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# SLA thresholds from SIRKET_STANDARTLARI.md
ASSIGNMENT_SLA_HOURS = 24       # Job must be assigned/running within 24h
COMPLETION_SLA_HOURS = 72       # Job must be completed within 72h (3 days)


@dataclass
class SLAResult:
    job_id: str
    ok: bool
    status: str
    elapsed_hours: float
    threshold_hours: float
    overdue_hours: float
    message: str


class SLAMonitor:
    """
    Enforces SLA thresholds from SIRKET_STANDARTLARI.md.

    - Assignment SLA: 24 hours from upload to first run
    - Completion SLA: 72 hours from upload to completed/partlist
    """

    def __init__(self, jobs_import_root: Path, jobs_output_root: Path) -> None:
        self._import_root = jobs_import_root
        self._output_root = jobs_output_root

    def check_assignment_sla(self, job: dict[str, Any]) -> SLAResult:
        """Check if job was started within 24 hours of upload."""
        job_id = job.get("job_id", "unknown")
        created_at = self._get_created_at(job_id)
        if not created_at:
            return SLAResult(job_id=job_id, ok=True, status="no_metadata", elapsed_hours=0, threshold_hours=ASSIGNMENT_SLA_HOURS, overdue_hours=0, message="Metadata bulunamadi.")

        elapsed = _hours_since(created_at)
        fsm_state = job.get("fsm_state", "uploaded")
        if fsm_state in ("uploaded",):
            # Not yet started
            if elapsed > ASSIGNMENT_SLA_HOURS:
                overdue = elapsed - ASSIGNMENT_SLA_HOURS
                return SLAResult(job_id=job_id, ok=False, status="assignment_overdue", elapsed_hours=round(elapsed, 1), threshold_hours=ASSIGNMENT_SLA_HOURS, overdue_hours=round(overdue, 1), message=f"Is {overdue:.1f} saat önce atama SLA'sını aştı. Müdür aksiyonu gerekli.")
        return SLAResult(job_id=job_id, ok=True, status="on_time", elapsed_hours=round(elapsed, 1), threshold_hours=ASSIGNMENT_SLA_HOURS, overdue_hours=0.0, message="SLA uyumlu.")

    def check_completion_sla(self, job: dict[str, Any]) -> SLAResult:
        """Check if job was completed within 72 hours of upload."""
        job_id = job.get("job_id", "unknown")
        created_at = self._get_created_at(job_id)
        if not created_at:
            return SLAResult(job_id=job_id, ok=True, status="no_metadata", elapsed_hours=0, threshold_hours=COMPLETION_SLA_HOURS, overdue_hours=0, message="Metadata bulunamadi.")

        elapsed = _hours_since(created_at)
        fsm_state = job.get("fsm_state", "uploaded")
        if fsm_state == "completed":
            return SLAResult(job_id=job_id, ok=True, status="completed", elapsed_hours=round(elapsed, 1), threshold_hours=COMPLETION_SLA_HOURS, overdue_hours=0.0, message=f"Tamamlandi ({elapsed:.1f} saat).")

        if elapsed > COMPLETION_SLA_HOURS:
            overdue = elapsed - COMPLETION_SLA_HOURS
            return SLAResult(job_id=job_id, ok=False, status="completion_overdue", elapsed_hours=round(elapsed, 1), threshold_hours=COMPLETION_SLA_HOURS, overdue_hours=round(overdue, 1), message=f"Is {overdue:.1f} saat önce tamamlanma SLA'sını aştı. Eskalasyon gerekli.")
        return SLAResult(job_id=job_id, ok=True, status="in_progress", elapsed_hours=round(elapsed, 1), threshold_hours=COMPLETION_SLA_HOURS, overdue_hours=0.0, message=f"Devam ediyor ({elapsed:.1f}/{COMPLETION_SLA_HOURS} saat).")

    def get_overdue_jobs(self, all_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return list of jobs that are overdue on either SLA."""
        overdue = []
        for job in all_jobs:
            assignment = self.check_assignment_sla(job)
            completion = self.check_completion_sla(job)
            if not assignment.ok or not completion.ok:
                overdue.append({
                    "job_id": job.get("job_id"),
                    "fsm_state": job.get("fsm_state"),
                    "assignment_sla": _sla_dict(assignment),
                    "completion_sla": _sla_dict(completion),
                })
        return overdue

    def report(self, all_jobs: list[dict[str, Any]]) -> dict[str, Any]:
        """Full SLA report for all jobs."""
        results = []
        for job in all_jobs:
            assignment = self.check_assignment_sla(job)
            completion = self.check_completion_sla(job)
            results.append({
                "job_id": job.get("job_id"),
                "fsm_state": job.get("fsm_state"),
                "project_name": job.get("project_name"),
                "assignment_sla": _sla_dict(assignment),
                "completion_sla": _sla_dict(completion),
                "any_overdue": not assignment.ok or not completion.ok,
            })
        overdue_count = sum(1 for r in results if r["any_overdue"])
        return {
            "generated_at": datetime.now().astimezone().isoformat(),
            "total_jobs": len(results),
            "overdue_count": overdue_count,
            "sla_compliance_rate": round((len(results) - overdue_count) / max(len(results), 1), 3),
            "jobs": results,
        }

    def _get_created_at(self, job_id: str) -> datetime | None:
        meta_path = self._import_root / job_id / "job.json"
        if not meta_path.exists():
            return None
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            ts = data.get("created_at")
            if not ts:
                return None
            return datetime.fromisoformat(ts)
        except (json.JSONDecodeError, ValueError):
            return None


def _hours_since(dt: datetime) -> float:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 3600


def _sla_dict(result: SLAResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "status": result.status,
        "elapsed_hours": result.elapsed_hours,
        "threshold_hours": result.threshold_hours,
        "overdue_hours": result.overdue_hours,
        "message": result.message,
    }


def get_sla_monitor(jobs_import_root: Path, jobs_output_root: Path) -> SLAMonitor:
    return SLAMonitor(jobs_import_root, jobs_output_root)
