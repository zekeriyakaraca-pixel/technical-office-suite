"""Job metadata contract for technical office workflows."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JobMetadataError(ValueError):
    """Raised when job metadata is missing or invalid."""


@dataclass
class JobMetadata:
    job_id: str
    project_name: str
    manager_agent: str = "teknik-ofis-muduru"
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def metadata_path(job_dir: str | Path) -> Path:
    return Path(job_dir) / "job.json"


def load_job_metadata(job_dir: str | Path) -> JobMetadata:
    path = metadata_path(job_dir)
    if not path.exists():
        raise JobMetadataError(f"Missing job metadata: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JobMetadataError(f"Invalid job metadata JSON: {path}") from exc
    return _metadata_from_dict(raw, path)


def upsert_job_metadata(
    job_dir: str | Path,
    project_name: str,
    manager_agent: str = "teknik-ofis-muduru",
) -> JobMetadata:
    root = Path(job_dir)
    path = metadata_path(root)
    raw: dict[str, Any] = {}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))

    raw["job_id"] = raw.get("job_id") or root.name
    raw["project_name"] = project_name
    raw["manager_agent"] = manager_agent
    raw["created_at"] = raw.get("created_at") or datetime.now(timezone.utc).isoformat()

    metadata = _metadata_from_dict(raw, path)
    path.write_text(json.dumps(metadata.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return metadata


def _metadata_from_dict(raw: dict[str, Any], path: Path) -> JobMetadata:
    job_id = str(raw.get("job_id") or path.parent.name).strip()
    project_name = str(raw.get("project_name") or "").strip()
    manager_agent = str(raw.get("manager_agent") or "teknik-ofis-muduru").strip()
    created_at = str(raw.get("created_at") or "").strip()

    if not job_id:
        raise JobMetadataError(f"job_id is required in {path}")
    if not project_name:
        raise JobMetadataError(f"project_name is required in {path}")
    if not manager_agent:
        raise JobMetadataError(f"manager_agent is required in {path}")

    return JobMetadata(
        job_id=job_id,
        project_name=project_name,
        manager_agent=manager_agent,
        created_at=created_at,
    )
