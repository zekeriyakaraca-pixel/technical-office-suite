from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class JobState(str, Enum):
    UPLOADED = "uploaded"
    CLASSIFYING = "classifying"
    CLASSIFIED = "classified"
    EXTRACTING = "extracting"
    AWAITING_APPROVAL = "awaiting_approval"
    PRODUCING = "producing"
    QC_CHECKING = "qc_checking"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


VALID_TRANSITIONS: dict[JobState, list[JobState]] = {
    JobState.UPLOADED: [JobState.CLASSIFYING, JobState.FAILED],
    JobState.CLASSIFYING: [JobState.CLASSIFIED, JobState.FAILED, JobState.RETRYING],
    JobState.CLASSIFIED: [JobState.EXTRACTING, JobState.PRODUCING, JobState.FAILED],
    JobState.EXTRACTING: [JobState.AWAITING_APPROVAL, JobState.FAILED, JobState.RETRYING],
    JobState.AWAITING_APPROVAL: [JobState.PRODUCING, JobState.FAILED],
    JobState.PRODUCING: [JobState.QC_CHECKING, JobState.FAILED, JobState.RETRYING],
    JobState.QC_CHECKING: [JobState.COMPLETED, JobState.FAILED],
    JobState.COMPLETED: [],
    JobState.FAILED: [JobState.CLASSIFYING],  # allows retry from failed
    JobState.RETRYING: [JobState.CLASSIFYING, JobState.EXTRACTING, JobState.PRODUCING, JobState.FAILED],
}

# States that allow re-running (idempotency guard)
RERUNNABLE_STATES = {JobState.UPLOADED, JobState.CLASSIFIED, JobState.AWAITING_APPROVAL, JobState.FAILED, JobState.COMPLETED}

# States considered "in progress" — re-run should be blocked
IN_PROGRESS_STATES = {JobState.CLASSIFYING, JobState.EXTRACTING, JobState.PRODUCING, JobState.QC_CHECKING, JobState.RETRYING}


class JobFSM:
    """
    Manages explicit job state transitions and persists state to
    workspace/outputs/<job_id>/fsm_state.json.
    """

    def __init__(self, output_root: Path) -> None:
        self._output_root = output_root

    def _state_path(self, job_id: str) -> Path:
        return self._output_root / job_id / "fsm_state.json"

    def get_state(self, job_id: str) -> JobState:
        path = self._state_path(job_id)
        if not path.exists():
            return JobState.UPLOADED
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return JobState(data.get("state", JobState.UPLOADED))
        except (json.JSONDecodeError, ValueError):
            return JobState.UPLOADED

    def can_transition(self, current: JobState, target: JobState) -> bool:
        return target in VALID_TRANSITIONS.get(current, [])

    def transition(self, job_id: str, new_state: JobState, *, reason: str | None = None) -> dict[str, Any]:
        current = self.get_state(job_id)
        if not self.can_transition(current, new_state):
            return {
                "ok": False,
                "error": f"Invalid transition: {current.value} → {new_state.value}",
                "current_state": current.value,
                "target_state": new_state.value,
            }
        self._persist(job_id, new_state, previous=current, reason=reason)
        return {"ok": True, "previous_state": current.value, "state": new_state.value}

    def force_transition(self, job_id: str, new_state: JobState, *, reason: str | None = None) -> dict[str, Any]:
        """Bypass validation — use only for recovery / admin operations."""
        current = self.get_state(job_id)
        self._persist(job_id, new_state, previous=current, reason=reason or "forced")
        return {"ok": True, "previous_state": current.value, "state": new_state.value, "forced": True}

    def is_in_progress(self, job_id: str) -> bool:
        return self.get_state(job_id) in IN_PROGRESS_STATES

    def can_rerun(self, job_id: str) -> bool:
        return self.get_state(job_id) in RERUNNABLE_STATES

    def _persist(self, job_id: str, state: JobState, *, previous: JobState, reason: str | None) -> None:
        path = self._state_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "job_id": job_id,
            "state": state.value,
            "previous_state": previous.value,
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        if reason:
            data["reason"] = reason
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


_fsm_instances: dict[str, JobFSM] = {}


def get_fsm(output_root: Path) -> JobFSM:
    key = str(output_root)
    if key not in _fsm_instances:
        _fsm_instances[key] = JobFSM(output_root)
    return _fsm_instances[key]
