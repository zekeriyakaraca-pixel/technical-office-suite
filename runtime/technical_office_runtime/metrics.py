from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

_job_counts: dict[str, int] = {}
_extraction_durations: list[float] = []
_active_jobs: set[str] = set()
_start_time = time.time()


def record_job_status(status: str) -> None:
    _job_counts[status] = _job_counts.get(status, 0) + 1


def record_extraction_duration(seconds: float) -> None:
    _extraction_durations.append(seconds)
    if len(_extraction_durations) > 1000:
        del _extraction_durations[:500]


def set_job_active(job_id: str) -> None:
    _active_jobs.add(job_id)


def set_job_inactive(job_id: str) -> None:
    _active_jobs.discard(job_id)


@contextmanager
def time_extraction(job_id: str):
    """Context manager that records extraction wall-clock time."""
    set_job_active(job_id)
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        record_extraction_duration(elapsed)
        set_job_inactive(job_id)


def get_metrics_summary() -> dict[str, Any]:
    durations = _extraction_durations
    avg_duration = sum(durations) / max(len(durations), 1)
    p95 = sorted(durations)[int(len(durations) * 0.95)] if durations else 0.0
    return {
        "uptime_seconds": round(time.time() - _start_time, 1),
        "jobs_by_status": dict(_job_counts),
        "active_jobs": len(_active_jobs),
        "active_job_ids": list(_active_jobs),
        "extraction": {
            "total_runs": len(durations),
            "avg_seconds": round(avg_duration, 2),
            "p95_seconds": round(p95, 2),
            "max_seconds": round(max(durations, default=0.0), 2),
        },
    }


def prometheus_text() -> str:
    """
    Minimal Prometheus text exposition format.
    No external library required — sufficient for scraping with Prometheus or Grafana.
    """
    summary = get_metrics_summary()
    lines: list[str] = [
        "# HELP toffice_uptime_seconds Runtime uptime in seconds",
        "# TYPE toffice_uptime_seconds gauge",
        f'toffice_uptime_seconds {summary["uptime_seconds"]}',
        "",
        "# HELP toffice_active_jobs Number of currently active jobs",
        "# TYPE toffice_active_jobs gauge",
        f'toffice_active_jobs {summary["active_jobs"]}',
        "",
        "# HELP toffice_jobs_total Total jobs processed by status",
        "# TYPE toffice_jobs_total counter",
    ]
    for status, count in summary["jobs_by_status"].items():
        lines.append(f'toffice_jobs_total{{status="{status}"}} {count}')

    ext = summary["extraction"]
    lines += [
        "",
        "# HELP toffice_extraction_runs_total Total Codex extraction runs",
        "# TYPE toffice_extraction_runs_total counter",
        f'toffice_extraction_runs_total {ext["total_runs"]}',
        "",
        "# HELP toffice_extraction_duration_avg_seconds Average extraction duration",
        "# TYPE toffice_extraction_duration_avg_seconds gauge",
        f'toffice_extraction_duration_avg_seconds {ext["avg_seconds"]}',
        "",
        "# HELP toffice_extraction_duration_p95_seconds P95 extraction duration",
        "# TYPE toffice_extraction_duration_p95_seconds gauge",
        f'toffice_extraction_duration_p95_seconds {ext["p95_seconds"]}',
        "",
    ]
    return "\n".join(lines)
