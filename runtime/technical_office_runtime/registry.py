from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import RuntimePaths

MODEL_ID = "technical-office/codex-cli"


def load_registry(paths: RuntimePaths) -> dict[str, Any]:
    return json.loads(paths.registry_path.read_text(encoding="utf-8"))


def active_agents(registry: dict[str, Any]) -> list[dict[str, Any]]:
    agents = registry.get("agents", [])
    if not isinstance(agents, list):
        return []
    return [
        _normalize_agent(agent)
        for agent in agents
        if isinstance(agent, dict)
        and isinstance(agent.get("id"), str)
        and agent.get("status") == "active"
    ]


def runtime_metadata() -> dict[str, str]:
    return {
        "name": "Technical Office Runtime",
        "version": "0.2.0",
        "vendor": "Technical Office Suite",
        "status": "ready",
        "active_model": MODEL_ID,
        "governance": "codex-cli-local-isolated",
    }


def state_response(paths: RuntimePaths) -> dict[str, Any]:
    registry = load_registry(paths)
    agents = active_agents(registry)
    active = {agent["id"]: MODEL_ID for agent in agents}
    return {
        "profileName": registry.get("profile", "technical-office"),
        "registry_profile": registry.get("profile", "technical-office"),
        "active": active,
        "identity": {
            "name": "Technical Office Manager",
            "role": "teknik-ofis-muduru",
            "lane": "teknik-ofis-muduru",
            "model_id": MODEL_ID,
        },
        "agents": agents,
        "capabilities": [
            "agents",
            "sessions",
            "chat",
            "streaming",
            "runtime-agent-events",
            "models",
            "agent-roles",
        ],
        "runtime": runtime_metadata(),
    }


def registry_response(paths: RuntimePaths) -> dict[str, Any]:
    registry = load_registry(paths)
    return {
        "profile": registry.get("profile", "technical-office"),
        "models": {
            MODEL_ID: {
                "name": "Technical Office Codex CLI Runtime",
                "provider": "codex-cli",
                "context": "agents/registry.json",
            }
        },
        "agents": active_agents(registry),
        "sessionDefaults": {"scope": "custom", "mainKey": "main"},
        "capabilities": [
            "agents",
            "sessions",
            "chat",
            "streaming",
            "runtime-agent-events",
            "models",
            "agent-roles",
        ],
    }


def relative_to_suite(path: str | Path, paths: RuntimePaths) -> str:
    resolved = Path(path)
    try:
        return str(resolved.resolve().relative_to(paths.suite_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(resolved)


def _normalize_agent(agent: dict[str, Any]) -> dict[str, Any]:
    agent_id = str(agent.get("id") or "").strip()
    role = str(agent.get("role") or "specialist").strip()
    name = str(agent.get("name") or agent_id).strip()
    return {
        **agent,
        "id": agent_id,
        "name": name,
        "role": role,
        "model": MODEL_ID,
        "session_key": f"agent:{agent_id}:main",
        "runtime_status": "idle",
        "capabilities": _agent_capabilities(role),
    }


def _agent_capabilities(role: str) -> list[str]:
    base = ["chat", "sessions", "runtime-agent-events"]
    if role == "manager":
        return [*base, "job-routing", "manual-review-approval"]
    if role == "autocad":
        return [*base, "dxf", "nc1", "job-production"]
    if role == "quality-control":
        return [*base, "qc", "manual-review-escalation"]
    if role == "document-control":
        return [*base, "partlist", "document-control"]
    return base
