from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


FLOW_CORNER_RELIEF = "corner_relief_clarification"
FLOW_MANAGER_ACTION_CONFIRMATION = "manager_action_confirmation"
FLOW_OPEN = "open"
FLOW_RESOLVED = "resolved"
FLOW_CANCELLED = "cancelled"

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass
class GuidedFlowState:
    flow_type: str
    session_id: str
    job_id: str
    status: str = FLOW_OPEN
    pending_candidate_rows: list[dict[str, Any]] = field(default_factory=list)
    poz_nos: list[str] = field(default_factory=list)
    expected_fields: list[str] = field(default_factory=list)
    action_type: str = ""
    action_payload: dict[str, Any] = field(default_factory=dict)
    last_manager_prompt: str = ""
    suggested_resolution_text: str = ""
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())
    resolved_at: str | None = None
    cancelled_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "flow_type": self.flow_type,
            "session_id": self.session_id,
            "job_id": self.job_id,
            "status": self.status,
            "pending_candidate_rows": self.pending_candidate_rows,
            "poz_nos": self.poz_nos,
            "expected_fields": self.expected_fields,
            "action_type": self.action_type,
            "action_payload": self.action_payload,
            "last_manager_prompt": self.last_manager_prompt,
            "suggested_resolution_text": self.suggested_resolution_text,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "resolved_at": self.resolved_at,
            "cancelled_at": self.cancelled_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GuidedFlowState" | None:
        flow_type = payload.get("flow_type")
        session_id = payload.get("session_id")
        job_id = payload.get("job_id")
        if not all(isinstance(value, str) and value.strip() for value in (flow_type, session_id, job_id)):
            return None
        return cls(
            flow_type=flow_type.strip(),
            session_id=session_id.strip(),
            job_id=job_id.strip(),
            status=str(payload.get("status") or FLOW_OPEN),
            pending_candidate_rows=[
                dict(item)
                for item in payload.get("pending_candidate_rows", [])
                if isinstance(item, dict)
            ],
            poz_nos=[str(item) for item in payload.get("poz_nos", []) if item is not None],
            expected_fields=[str(item) for item in payload.get("expected_fields", []) if item is not None],
            action_type=str(payload.get("action_type") or ""),
            action_payload=dict(payload.get("action_payload") or {}) if isinstance(payload.get("action_payload"), dict) else {},
            last_manager_prompt=str(payload.get("last_manager_prompt") or ""),
            suggested_resolution_text=str(payload.get("suggested_resolution_text") or ""),
            created_at=str(payload.get("created_at") or _now_iso()),
            updated_at=str(payload.get("updated_at") or _now_iso()),
            resolved_at=str(payload.get("resolved_at")) if payload.get("resolved_at") else None,
            cancelled_at=str(payload.get("cancelled_at")) if payload.get("cancelled_at") else None,
        )


class GuidedFlowStore:
    def __init__(self, workspace_root: Path) -> None:
        self.root = workspace_root / "memory"
        self.path = self.root / "manager_guided_flows.json"

    def get_open(
        self,
        *,
        session_id: str,
        job_id: str | None = None,
        flow_type: str | None = None,
    ) -> GuidedFlowState | None:
        states = [
            state
            for state in self._load()
            if state.status == FLOW_OPEN
            and state.session_id == session_id
            and (job_id is None or state.job_id == job_id)
            and (flow_type is None or state.flow_type == flow_type)
        ]
        states.sort(key=lambda item: item.updated_at, reverse=True)
        return states[0] if states else None

    def upsert(self, state: GuidedFlowState) -> GuidedFlowState:
        state.updated_at = _now_iso()
        states = self._load()
        key = _state_key(state)
        replaced = False
        for index, existing in enumerate(states):
            if _state_key(existing) == key:
                states[index] = state
                replaced = True
                break
        if not replaced:
            states.append(state)
        self._save(states)
        return state

    def resolve(self, state: GuidedFlowState) -> GuidedFlowState:
        state.status = FLOW_RESOLVED
        state.resolved_at = _now_iso()
        return self.upsert(state)

    def cancel(self, state: GuidedFlowState) -> GuidedFlowState:
        state.status = FLOW_CANCELLED
        state.cancelled_at = _now_iso()
        return self.upsert(state)

    def replace_pending(
        self,
        state: GuidedFlowState,
        pending_candidate_rows: list[dict[str, Any]],
        *,
        last_manager_prompt: str | None = None,
        suggested_resolution_text: str | None = None,
    ) -> GuidedFlowState:
        state.pending_candidate_rows = [dict(item) for item in pending_candidate_rows]
        state.poz_nos = [
            str(item.get("poz_no"))
            for item in pending_candidate_rows
            if isinstance(item, dict) and item.get("poz_no")
        ]
        if last_manager_prompt is not None:
            state.last_manager_prompt = last_manager_prompt
        if suggested_resolution_text is not None:
            state.suggested_resolution_text = suggested_resolution_text
        return self.upsert(state)

    def cleanup_missing_files(self) -> int:
        if not self.path.exists():
            return 0
        states = self._load()
        self._save(states)
        return len(states)

    def _load(self) -> list[GuidedFlowState]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        raw_states = payload.get("flows") if isinstance(payload, dict) else payload
        if not isinstance(raw_states, list):
            return []
        states: list[GuidedFlowState] = []
        for item in raw_states:
            if not isinstance(item, dict):
                continue
            state = GuidedFlowState.from_dict(item)
            if state is not None:
                states.append(state)
        return states

    def _save(self, states: list[GuidedFlowState]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"flows": [state.to_dict() for state in states]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def corner_relief_state(
    *,
    session_id: str,
    job_id: str,
    pending_candidate_rows: list[dict[str, Any]],
    prompt: str,
) -> GuidedFlowState:
    return GuidedFlowState(
        flow_type=FLOW_CORNER_RELIEF,
        session_id=session_id,
        job_id=job_id,
        pending_candidate_rows=[dict(item) for item in pending_candidate_rows],
        poz_nos=[
            str(item.get("poz_no"))
            for item in pending_candidate_rows
            if isinstance(item, dict) and item.get("poz_no")
        ],
        expected_fields=["corner", "relief_type", "size_mm"],
        last_manager_prompt=prompt,
    )


def manager_action_state(
    *,
    session_id: str,
    job_id: str,
    action_type: str,
    action_payload: dict[str, Any],
    prompt: str,
) -> GuidedFlowState:
    return GuidedFlowState(
        flow_type=FLOW_MANAGER_ACTION_CONFIRMATION,
        session_id=session_id,
        job_id=job_id,
        expected_fields=["confirmation"],
        action_type=action_type,
        action_payload=dict(action_payload),
        last_manager_prompt=prompt,
    )


def get_guided_flow_store(workspace_root: Path) -> GuidedFlowStore:
    key = str((workspace_root / "memory" / "manager_guided_flows.json").resolve())
    if key not in _stores:
        _stores[key] = GuidedFlowStore(workspace_root)
    return _stores[key]


def _state_key(state: GuidedFlowState) -> tuple[str, str, str]:
    return (state.flow_type, state.session_id, state.job_id)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


_stores: dict[str, GuidedFlowStore] = {}
