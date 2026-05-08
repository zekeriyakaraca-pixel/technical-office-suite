from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import time
import unicodedata
import uuid
from collections.abc import Iterable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse

from .audit import get_audit_logger
from .auth import auth_status, require_auth
from .codex_bridge import CodexBridge, CodexRunRequest
from .config import RuntimePaths, ensure_autocad_import_path, get_paths
from .job_fsm import JobState, get_fsm
from .manager_memory import get_manager_memory
from .memory_bridge import get_memory_bridge
from .metrics import get_metrics_summary, prometheus_text, record_job_status
from .orchestrator import AgentOrchestrator
from .sla import get_sla_monitor
from .workers import get_workers
from .registry import MODEL_ID, registry_response, runtime_metadata, state_response
from .session_store import (
    ensure_main_session_entry,
    list_sessions,
    load_session,
    merge_history,
    preview_sessions,
    reset_session,
    runtime_session_status,
    sanitize_session_files,
    save_session,
)
from .tools import ToolRegistry


JOB_STATUS_VALUES = {
    "uploaded",
    "running_diagnostics",
    "codex_extracting",
    "needs_manager_approval",
    "producing",
    "completed",
    "failed",
}

VISUAL_CANDIDATE_MAX_PAGES = 80


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sanitize_session_files(get_paths().sessions_root)
    yield


app = FastAPI(title="Technical Office Runtime", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    path = Path(__file__).resolve().parent / "static" / "index.html"
    if not path.exists():
        return HTMLResponse("<h1>Technical Office Runtime</h1><p>Dashboard asset missing.</p>")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict[str, Any]:
    paths = get_paths()
    job_snapshot = _job_status_snapshot(paths)
    return {
        "ok": True,
        "status": "ready",
        "runtime": runtime_metadata(),
        "jobs_active": sum(1 for j in job_snapshot if j.get("fsm_state") in ("classifying", "extracting", "producing", "qc_checking", "retrying")),
        "jobs_total": len(job_snapshot),
        "auth": auth_status(),
    }


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    return health()


@app.get("/metrics", response_class=PlainTextResponse)
def metrics_prometheus() -> PlainTextResponse:
    return PlainTextResponse(prometheus_text(), media_type="text/plain; version=0.0.4")


@app.get("/api/metrics")
def metrics_json() -> dict[str, Any]:
    return {"ok": True, "metrics": get_metrics_summary()}


@app.get("/api/audit")
def audit_log(limit: int = 100) -> dict[str, Any]:
    paths = get_paths()
    logger = get_audit_logger(paths.workspace_root)
    return {"ok": True, "entries": logger.read_recent(limit=min(limit, 500))}


@app.get("/api/audit/{job_id}")
def audit_log_for_job(job_id: str) -> dict[str, Any]:
    paths = get_paths()
    _require_job(paths, job_id)
    logger = get_audit_logger(paths.workspace_root)
    return {"ok": True, "job_id": job_id, "entries": logger.read_for_job(job_id)}


@app.get("/api/sla/report")
def sla_report() -> dict[str, Any]:
    paths = get_paths()
    monitor = get_sla_monitor(paths.jobs_import_root, paths.jobs_output_root)
    jobs = [_job_detail(paths, record["job_id"], shallow=True) for record in _list_job_records(paths)]
    return {"ok": True, "report": monitor.report(jobs)}


@app.get("/api/sla/overdue")
def sla_overdue() -> dict[str, Any]:
    paths = get_paths()
    monitor = get_sla_monitor(paths.jobs_import_root, paths.jobs_output_root)
    jobs = [_job_detail(paths, record["job_id"], shallow=True) for record in _list_job_records(paths)]
    return {"ok": True, "overdue": monitor.get_overdue_jobs(jobs)}


@app.get("/api/memory/stats")
def memory_stats() -> dict[str, Any]:
    paths = get_paths()
    bridge = get_memory_bridge(paths.workspace_root)
    return {"ok": True, "stats": bridge.get_pattern_stats()}


@app.get("/api/memory/patterns")
def memory_patterns(limit: int = 50) -> dict[str, Any]:
    paths = get_paths()
    bridge = get_memory_bridge(paths.workspace_root)
    return {"ok": True, "patterns": bridge.list_patterns(limit=min(limit, 200))}


@app.get("/api/manager/memory")
def manager_memory(job_id: str | None = None, session_id: str = "agent:teknik-ofis-muduru:dashboard", limit: int = 20) -> dict[str, Any]:
    paths = get_paths()
    memory = get_manager_memory(paths.workspace_root)
    bounded_limit = min(max(limit, 1), 200)
    facts = memory.list_facts(job_id, limit=bounded_limit) if job_id else []
    events = memory.recent_events(session_id=session_id, job_id=job_id, limit=bounded_limit)
    return {
        "ok": True,
        "job_id": job_id,
        "session_id": session_id,
        "stats": memory.stats(),
        "facts": facts,
        "recent_events": events,
    }


@app.get("/state")
def state() -> dict[str, Any]:
    return state_response(get_paths())


@app.get("/registry")
def registry() -> dict[str, Any]:
    return registry_response(get_paths())


@app.get("/status")
def runtime_status() -> dict[str, Any]:
    paths = get_paths()
    return {
        "ok": True,
        "runtime": runtime_metadata(),
        "sessions": runtime_session_status(paths.sessions_root),
        "agents": _agent_runtime_states(paths),
        "jobs": _job_status_snapshot(paths),
    }


@app.get("/sessions")
def sessions_list(
    agent_id: str | None = None,
    search: str | None = None,
    limit: int = 50,
    main_key: str = "main",
) -> dict[str, Any]:
    paths = get_paths()
    sessions = list_sessions(paths.sessions_root, agent_id=agent_id, search=search, limit=limit)
    if agent_id and not sessions:
        sessions = [
            ensure_main_session_entry(
                agent_id=agent_id,
                main_key=main_key,
                model=MODEL_ID,
                origin_label=runtime_metadata()["name"],
            )
        ]
    return {"sessions": [_session_response(entry) for entry in sessions]}


@app.get("/sessions/history")
def session_history(session_id: str, limit: int = 200) -> dict[str, Any]:
    messages = load_session(get_paths().sessions_root, session_id)[-max(1, min(limit, 500)):]
    return {
        "sessionKey": session_id,
        "messages": [
            {
                "role": message["role"],
                "content": message["content"],
                **({"timestamp": message["timestamp"]} if "timestamp" in message else {}),
            }
            for message in messages
        ],
    }


@app.post("/sessions/preview")
async def sessions_preview(request: Request) -> dict[str, Any]:
    payload = await request.json()
    keys_raw = payload.get("keys")
    keys = [key for key in keys_raw if isinstance(key, str)] if isinstance(keys_raw, list) else []
    return preview_sessions(
        get_paths().sessions_root,
        keys=keys,
        limit=_bounded_int(payload.get("limit"), default=8, low=1, high=50),
        max_chars=_bounded_int(payload.get("maxChars"), default=240, low=40, high=2000),
    )


@app.post("/sessions/reset")
async def sessions_reset(request: Request) -> dict[str, Any]:
    payload = await request.json()
    session_id = _optional_str(payload.get("session_id") or payload.get("key"))
    if not session_id:
        return {"ok": False, "error": "session_id is required"}
    removed = reset_session(get_paths().sessions_root, session_id)
    return {"ok": True, "removed": removed, "session_id": session_id}


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request) -> dict[str, Any] | StreamingResponse:
    payload = await request.json()
    model = _optional_str(payload.get("model")) or MODEL_ID
    messages = payload.get("messages")
    user_text = _last_user_text(messages)
    incoming_history = _history(messages)
    agent_id = _resolve_agent_id(payload)
    session_id = _optional_str(payload.get("session_id") or payload.get("conversation_id"))
    run_id = _optional_str(payload.get("idempotencyKey") or payload.get("run_id")) or f"run-{uuid.uuid4().hex}"

    paths = get_paths()
    server_history = load_session(paths.sessions_root, session_id) if session_id else []
    history = merge_history(server_history, incoming_history)

    if payload.get("stream") is True:
        return StreamingResponse(
            _stream_chat_completion(
                model=model,
                agent_id=agent_id,
                session_id=session_id,
                run_id=run_id,
                user_text=user_text,
                history=history,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    run_text = user_text
    remembered_job_id: str | None = None
    if agent_id == "teknik-ofis-muduru" and session_id:
        memory_recall = get_manager_memory(paths.workspace_root).recall(
            session_id=session_id,
            message=user_text,
            history=history,
        )
        remembered_job_id = memory_recall.primary_job_id
        run_text = _manager_message_with_context(paths, user_text, None, memory_context=memory_recall.to_payload())

    result = AgentOrchestrator(agent_id=agent_id, bridge=_app_codex_bridge()).run(run_text, history=history)
    if session_id:
        _save_turn(paths, session_id, history, user_text, result.content, selected_job_id=remembered_job_id)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": result.content}, "finish_reason": "stop"}],
    }


@app.post("/api/manager/chat")
async def manager_chat(request: Request) -> dict[str, Any]:
    payload = await _required_json_object(request)
    agent_id = _optional_str(payload.get("agent_id")) or "teknik-ofis-muduru"
    if agent_id != "teknik-ofis-muduru":
        raise HTTPException(status_code=403, detail="Only teknik-ofis-muduru chat is enabled.")
    message = _optional_str(payload.get("message") or payload.get("content"))
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    paths = get_paths()
    session_id = _optional_str(payload.get("session_id") or payload.get("conversation_id")) or "agent:teknik-ofis-muduru:dashboard"
    incoming_history = payload.get("history") if isinstance(payload.get("history"), list) else []
    server_history = load_session(paths.sessions_root, session_id)
    history = merge_history(server_history, incoming_history)
    selected_job_id = _optional_str(payload.get("selected_job_id") or payload.get("job_id"))
    memory_recall = get_manager_memory(paths.workspace_root).recall(
        session_id=session_id,
        message=message,
        selected_job_id=selected_job_id,
        history=history,
    )
    enriched_message = _manager_message_with_context(paths, message, selected_job_id, memory_context=memory_recall.to_payload())
    orchestrator = AgentOrchestrator(agent_id="teknik-ofis-muduru", bridge=_app_codex_bridge())
    result = await asyncio.to_thread(orchestrator.run, enriched_message, history=history)
    _save_turn(paths, session_id, history, message, result.content, selected_job_id=selected_job_id or memory_recall.primary_job_id)
    return {
        "ok": True,
        "agent_id": "teknik-ofis-muduru",
        "session_id": session_id,
        "message": result.content,
        "used_codex": result.used_llm,
        "fallback_reason": result.fallback_reason,
        "tool_results": result.tool_results,
    }


@app.post("/api/jobs", dependencies=[Depends(require_auth)])
async def create_job_from_upload(request: Request) -> dict[str, Any]:
    form = await request.form()
    project_name = str(form.get("project_name") or "").strip()
    if not project_name:
        raise HTTPException(status_code=400, detail="project_name is required")
    requested_job_id = str(form.get("job_id") or "").strip()
    job_id = _safe_job_id(requested_job_id or f"{_slugify(project_name)}-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    paths = get_paths()
    job_dir = paths.jobs_import_root / job_id
    if job_dir.exists():
        raise HTTPException(status_code=409, detail=f"Job already exists: {job_id}")
    job_dir.mkdir(parents=True)

    files = [item for item in form.getlist("pdf_files") if hasattr(item, "filename") and hasattr(item, "read")]
    if not files:
        raise HTTPException(status_code=400, detail="pdf_files[] is required")
    saved: list[dict[str, Any]] = []
    for item in files:
        filename = _safe_pdf_name(str(item.filename or "input.pdf"))
        data = await item.read()
        if not data:
            raise HTTPException(status_code=400, detail=f"Empty PDF upload: {filename}")
        target = job_dir / filename
        target.write_bytes(data)
        saved.append({"name": filename, "size_bytes": len(data)})

    metadata = {
        "job_id": job_id,
        "project_name": project_name,
        "manager_agent": "teknik-ofis-muduru",
        "created_at": datetime.now().astimezone().isoformat(),
    }
    (job_dir / "job.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    _append_job_event(paths, job_id, "started", {"message": "Job uploaded", "pdfs": saved})
    get_audit_logger(paths.workspace_root).log_job_created(job_id, project_name, len(saved))
    record_job_status("uploaded")
    return {"ok": True, "job": _job_detail(paths, job_id)}


@app.get("/api/jobs")
def list_jobs_api() -> dict[str, Any]:
    paths = get_paths()
    return {"ok": True, "jobs": [_job_detail(paths, record["job_id"], shallow=True) for record in _list_job_records(paths)]}


@app.get("/api/jobs/{job_id}")
def get_job_api(job_id: str) -> dict[str, Any]:
    paths = get_paths()
    _require_job(paths, job_id)
    return {"ok": True, "job": _job_detail(paths, job_id)}


@app.get("/api/jobs/{job_id}/files/{filename:path}")
def get_job_file(job_id: str, filename: str, inline: bool = False) -> FileResponse:
    paths = get_paths()
    _require_job(paths, job_id)
    path = _resolve_job_file(paths, job_id, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if inline:
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(path.name)}"},
        )
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.post("/api/jobs/{job_id}/run")
async def run_job_api(job_id: str, request: Request) -> dict[str, Any]:
    paths = get_paths()
    _require_job(paths, job_id)
    fsm = get_fsm(paths.jobs_output_root)

    # Idempotency: block re-run if already in progress
    if fsm.is_in_progress(job_id):
        current = fsm.get_state(job_id)
        return {"ok": False, "error": f"Is zaten isleniyor (durum: {current.value}). Tamamlanmasini bekleyin.", "job": _job_detail(paths, job_id)}

    payload = await _optional_json(request)
    autocad_policy = str(payload.get("autocad_live_policy") or payload.get("autocad") or "off")

    fsm.transition(job_id, JobState.CLASSIFYING, reason="run_api")
    _append_job_event(paths, job_id, "started", {"message": "Deterministic pipeline started", "fsm_state": "classifying"})
    tool = ToolRegistry(paths)
    result = tool.run("run_autocad_job", {"job_id": job_id, "autocad_live_policy": autocad_policy})
    if not result.get("ok"):
        fsm.transition(job_id, JobState.FAILED, reason=result.get("error", "pipeline_error"))
        _append_job_event(paths, job_id, "failed", {"error": result.get("error")})
        return {"ok": False, "error": result.get("error"), "job": _job_detail(paths, job_id)}

    fsm.transition(job_id, JobState.CLASSIFIED, reason="diagnostics_ok")
    summary = result.get("summary", {})
    if _summary_requires_visual_candidates(summary):
        fsm.transition(job_id, JobState.EXTRACTING, reason="visual_required")
        _append_job_event(paths, job_id, "codex_extracting", {"message": "Visual candidate extraction requested"})
        codex_result = _extract_codex_candidates(paths, job_id)
        if codex_result.get("ok"):
            fsm.transition(job_id, JobState.AWAITING_APPROVAL, reason="codex_candidates_ready")
            _append_job_event(paths, job_id, "candidate", {"count": codex_result.get("count", 0)})
            # Background workers: pre-validate + consensus check (fire-and-forget)
            asyncio.create_task(
                get_workers(paths).run_post_extraction_workers(job_id),
                name=f"post_extraction_{job_id}",
            )
        else:
            fsm.transition(job_id, JobState.FAILED, reason=codex_result.get("error", "codex_failed"))
            _append_job_event(paths, job_id, "failed", {"stage": "codex_extracting", "error": codex_result.get("error")})
        _append_job_event(paths, job_id, "completed", {"status": "needs_manager_approval"})
    else:
        if summary.get("ok"):
            fsm.transition(job_id, JobState.PRODUCING, reason="production_started")
            fsm.transition(job_id, JobState.QC_CHECKING, reason="qc_started")
            fsm.transition(job_id, JobState.COMPLETED, reason="pipeline_complete")
        else:
            fsm.transition(job_id, JobState.FAILED, reason="pipeline_failed")
        _append_job_event(paths, job_id, "completed", {"status": "completed" if summary.get("ok") else "failed"})
    return {"ok": True, "job": _job_detail(paths, job_id)}


@app.post("/api/jobs/{job_id}/approve-candidates", dependencies=[Depends(require_auth)])
async def approve_candidates(job_id: str, request: Request) -> dict[str, Any]:
    paths = get_paths()
    job_dir = _require_job(paths, job_id)
    fsm = get_fsm(paths.jobs_output_root)
    payload = await request.json()
    rows = _approval_rows(payload, paths, job_id)
    if not rows:
        raise HTTPException(status_code=400, detail="plates or candidate_ids are required")

    validated, errors = _validate_approved_rows(rows, paths, job_id)
    if errors:
        return {"ok": False, "validation_errors": errors, "job": _job_detail(paths, job_id)}

    approval = {
        "approved_by": "teknik-ofis-muduru",
        "approved_at": datetime.now().astimezone().isoformat(),
        "plates": validated,
    }
    (job_dir / "approved_plate_specs.json").write_text(json.dumps(approval, indent=2, ensure_ascii=False), encoding="utf-8")
    fsm.force_transition(job_id, JobState.PRODUCING, reason="manager_approved")
    _append_job_event(paths, job_id, "started", {"message": "Manager approved candidates", "count": len(validated)})

    tool = ToolRegistry(paths)
    result = tool.run("run_autocad_job", {"job_id": job_id, "autocad_live_policy": str(payload.get("autocad_live_policy") or "off")})
    if not result.get("ok"):
        fsm.transition(job_id, JobState.FAILED, reason=result.get("error", "production_failed"))
        _append_job_event(paths, job_id, "failed", {"error": result.get("error")})
        return {"ok": False, "error": result.get("error"), "job": _job_detail(paths, job_id)}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    if summary.get("ok") is not True:
        manual_reviews = summary.get("manual_reviews") if isinstance(summary.get("manual_reviews"), list) else []
        if manual_reviews:
            fsm.force_transition(job_id, JobState.AWAITING_APPROVAL, reason="production_manual_review_pending")
            _append_job_event(
                paths,
                job_id,
                "completed",
                {"status": "needs_manager_approval", "manual_review_count": len(manual_reviews)},
            )
            return {
                "ok": False,
                "error": "production_manual_review_pending",
                "manual_reviews": manual_reviews,
                "job": _job_detail(paths, job_id),
            }
        fsm.transition(job_id, JobState.FAILED, reason="qc_failed")
        _append_job_event(paths, job_id, "failed", {"error": "QC ok=false", "summary": summary})
        return {"ok": False, "error": "QC ok=false", "job": _job_detail(paths, job_id)}
    fsm.transition(job_id, JobState.QC_CHECKING, reason="qc_started")
    fsm.transition(job_id, JobState.COMPLETED, reason="production_complete")
    _append_job_event(paths, job_id, "completed", {"status": "completed", "approved_count": len(validated)})
    poz_nos = [str(p.get("poz_no", "")) for p in validated if isinstance(p, dict)]
    get_audit_logger(paths.workspace_root).log_approval(job_id, "teknik-ofis-muduru", len(validated), poz_nos)
    record_job_status("completed")
    return {"ok": True, "job": _job_detail(paths, job_id)}


@app.post("/api/jobs/{job_id}/partlist", dependencies=[Depends(require_auth)])
async def create_partlist_api(job_id: str) -> dict[str, Any]:
    paths = get_paths()
    job_dir = _require_job(paths, job_id)
    output_dir = paths.jobs_output_root / job_id
    ensure_autocad_import_path(paths)
    from autocad_mcp.technical_office.partlist import create_partlist

    _append_job_event(paths, job_id, "started", {"message": "Partlist generation requested"})
    result = create_partlist(job_dir, output_dir)
    _append_job_event(
        paths,
        job_id,
        "completed" if result.ok else "failed",
        {"stage": "partlist", "partlist": result.to_dict()},
    )
    partlist_dict = result.to_dict()
    get_audit_logger(paths.workspace_root).log_partlist_created(
        job_id,
        row_count=partlist_dict.get("rows", 0) if isinstance(partlist_dict, dict) else 0,
        ok=result.ok,
    )
    if result.ok:
        record_job_status("partlist_ok")
    return {"ok": result.ok, "partlist": partlist_dict, "job": _job_detail(paths, job_id)}


@app.get("/api/events/{job_id}")
def job_events(job_id: str) -> StreamingResponse:
    paths = get_paths()
    _require_job(paths, job_id)
    return StreamingResponse(
        _event_stream(paths, job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _last_user_text(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for item in reversed(messages):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(_content_part_text(part) for part in content)
    return ""


def _manager_message_with_context(
    paths: RuntimePaths,
    message: str,
    selected_job_id: str | None,
    *,
    memory_context: dict[str, Any] | None = None,
) -> str:
    blocks = [message]
    if selected_job_id:
        try:
            detail = _job_detail(paths, selected_job_id, shallow=True)
        except Exception:
            detail = {}
        context = {
            "selected_job_id": selected_job_id,
            "status": detail.get("status"),
            "project_name": detail.get("project_name"),
            "pdf_count": detail.get("pdf_count"),
            "manual_review_count": detail.get("manual_review_count"),
            "produced_count": detail.get("produced_count"),
            "qc_failed": detail.get("qc_failed"),
        }
        blocks.append(f"[Secili is baglami: {json.dumps(context, ensure_ascii=False)}]")
    if memory_context:
        blocks.append(f"[Mudur hafiza baglami: {json.dumps(memory_context, ensure_ascii=False)}]")
    return "\n\n".join(blocks)


def _history(messages: Any) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        return []
    history = []
    for item in messages[:-1]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            history.append({"role": role, "content": content})
    return history


def _content_part_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict) and isinstance(part.get("text"), str):
        return part["text"]
    return ""


def _optional_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


_GENERIC_AGENT_IDS = frozenset({"main", "custom", "assistant", "agent"})


def _resolve_agent_id(payload: dict[str, Any]) -> str:
    for key in ("agent_id", "lane", "role"):
        value = _optional_str(payload.get(key))
        if value and value not in _GENERIC_AGENT_IDS:
            return value
    return "teknik-ofis-muduru"


def _stream_chat_completion(
    *,
    model: str,
    agent_id: str,
    session_id: str | None,
    run_id: str,
    user_text: str,
    history: list[dict[str, Any]],
) -> Iterable[str]:
    seq = 1
    yield _sse_frame("agent", {"runId": run_id, "sessionKey": session_id, "stream": "lifecycle", "data": {"phase": "start", "agent_id": agent_id}, "timestamp": _now_ms()}, seq=seq)
    seq += 1
    try:
        paths = get_paths()
        run_text = user_text
        remembered_job_id: str | None = None
        if agent_id == "teknik-ofis-muduru" and session_id:
            memory_recall = get_manager_memory(paths.workspace_root).recall(
                session_id=session_id,
                message=user_text,
                history=history,
            )
            remembered_job_id = memory_recall.primary_job_id
            run_text = _manager_message_with_context(paths, user_text, None, memory_context=memory_recall.to_payload())
        result = AgentOrchestrator(agent_id=agent_id, bridge=_app_codex_bridge()).run(run_text, history=history)
        content = result.content.strip()
        if session_id:
            _save_turn(paths, session_id, history, user_text, content, selected_job_id=remembered_job_id)
        running = ""
        for chunk in _text_chunks(content):
            running += chunk
            yield _sse_frame("chat", {"runId": run_id, "sessionKey": session_id, "state": "delta", "text": running, "message": {"role": "assistant", "content": running, "timestamp": _now_ms()}}, seq=seq)
            seq += 1
        yield _sse_frame("chat", {"runId": run_id, "sessionKey": session_id, "state": "final", "text": content, "message": {"role": "assistant", "content": content, "timestamp": _now_ms()}}, seq=seq)
        seq += 1
        yield _sse_frame("agent", {"runId": run_id, "sessionKey": session_id, "stream": "lifecycle", "data": {"phase": "end", "agent_id": agent_id}, "timestamp": _now_ms()}, seq=seq)
    except Exception as exc:
        message = f"Runtime hatasi: {exc}"
        yield _sse_frame("chat", {"runId": run_id, "sessionKey": session_id, "state": "error", "text": message, "errorMessage": message, "message": {"role": "assistant", "content": message, "timestamp": _now_ms()}}, seq=seq)
        seq += 1
        yield _sse_frame("agent", {"runId": run_id, "sessionKey": session_id, "stream": "lifecycle", "data": {"phase": "error", "agent_id": agent_id}, "timestamp": _now_ms()}, seq=seq)


def _sse_frame(event: str, payload: dict[str, Any], *, seq: int) -> str:
    frame = {"type": "event", "event": event, "payload": payload, "seq": seq}
    return f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"


def _text_chunks(content: str) -> Iterable[str]:
    if not content:
        return []
    chunks: list[str] = []
    current = ""
    for part in re.split(r"(\s+)", content):
        if not part:
            continue
        current += part
        if len(current) >= 48 or "\n" in current:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def _session_response(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": entry.get("key") or entry.get("session_id"),
        "updatedAt": entry.get("updated_at"),
        "displayName": entry.get("display_name"),
        "origin": entry.get("origin") or {"label": runtime_metadata()["name"], "provider": "custom"},
        "modelProvider": entry.get("modelProvider") or "custom",
        "model": entry.get("model") or MODEL_ID,
        "messageCount": entry.get("message_count") or 0,
    }


def _agent_runtime_states(paths: RuntimePaths) -> list[dict[str, Any]]:
    agents = registry_response(paths).get("agents", [])
    job_snapshot = _job_status_snapshot(paths)
    has_manual_review = any(job.get("manual_review_count", 0) > 0 for job in job_snapshot)
    has_failed_qc = any(job.get("qc_failed") for job in job_snapshot)
    states = []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        role = agent.get("role")
        status = "idle"
        reason = "Hazir"
        if role == "quality-control" and has_failed_qc:
            status = "error"
            reason = "QC blokaji var"
        elif has_manual_review and role in {"manager", "quality-control"}:
            reason = "Mudur onayli manuel inceleme bekliyor"
        states.append({"agent_id": agent.get("id"), "session_key": agent.get("session_key"), "status": status, "reason": reason})
    return states


def _job_status_snapshot(paths: RuntimePaths) -> list[dict[str, Any]]:
    return [_job_detail(paths, record["job_id"], shallow=True) for record in _list_job_records(paths)]


def _list_job_records(paths: RuntimePaths) -> list[dict[str, Any]]:
    if not paths.jobs_import_root.exists():
        return []
    return [{"job_id": path.name} for path in sorted(paths.jobs_import_root.iterdir()) if path.is_dir()]


def _job_detail(paths: RuntimePaths, job_id: str, *, shallow: bool = False) -> dict[str, Any]:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    summary = _read_json(output_dir / "job_summary.json") or {}
    manual_reviews = summary.get("manual_reviews")
    produced = summary.get("produced")
    fsm_state = get_fsm(paths.jobs_output_root).get_state(job_id).value
    detail: dict[str, Any] = {
        "job_id": job_id,
        "status": _job_status(job_dir, output_dir, summary),
        "fsm_state": fsm_state,
        "input_dir": _relative(job_dir, paths),
        "output_dir": _relative(output_dir, paths),
        "project_name": (_read_json(job_dir / "job.json") or {}).get("project_name"),
        "pdf_count": len(list(job_dir.glob("*.pdf"))) if job_dir.exists() else 0,
        "manual_review_count": len(manual_reviews) if isinstance(manual_reviews, list) else 0,
        "produced_count": len(produced) if isinstance(produced, list) else 0,
        "qc_failed": _has_failed_qc(summary),
    }
    if shallow:
        return detail
    detail.update(
        {
            "metadata": _read_json(job_dir / "job.json"),
            "pdfs": [
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "download_url": f"/api/jobs/{quote(job_id)}/files/{quote(path.name)}",
                    "preview_url": f"/api/jobs/{quote(job_id)}/files/{quote(path.name)}?inline=true",
                }
                for path in sorted(job_dir.glob("*.pdf"))
            ],
            "summary": summary or None,
            "diagnostics": _read_json(output_dir / "pdf_diagnostics.json"),
            "extraction_candidates": _read_json(output_dir / "extraction_candidates.json"),
            "codex_candidates": _read_json(output_dir / "codex_candidates.json"),
            "approved_specs": _read_json(job_dir / "approved_plate_specs.json"),
            "partlist": _partlist_detail(paths, job_id),
            "files": _job_files(paths, job_id),
            "events": _read_events(_event_log_path(paths, job_id)),
        }
    )
    return detail


def _job_status(job_dir: Path, output_dir: Path, summary: dict[str, Any]) -> str:
    if not job_dir.exists():
        return "failed"
    manual_reviews = summary.get("manual_reviews")
    if isinstance(manual_reviews, list) and manual_reviews:
        return "needs_manager_approval"
    if summary.get("ok") is True:
        return "completed"
    if output_dir.exists():
        return "failed" if summary else "uploaded"
    return "uploaded"


def _has_failed_qc(summary: dict[str, Any]) -> bool:
    produced = summary.get("produced")
    return isinstance(produced, list) and any(isinstance(item, dict) and item.get("ok") is False for item in produced)


def _require_job(paths: RuntimePaths, job_id: str) -> Path:
    safe = _safe_job_id(job_id)
    if safe != job_id:
        raise HTTPException(status_code=400, detail="invalid job_id")
    job_dir = paths.jobs_import_root / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return job_dir


async def _optional_json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _required_json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Request body must be UTF-8 encoded JSON.") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object is required.")
    return payload


def _summary_requires_visual_candidates(summary: dict[str, Any]) -> bool:
    reviews = summary.get("manual_reviews")
    if not isinstance(reviews, list):
        return False
    return any(isinstance(item, dict) and item.get("reason") in {"visual_text_required", "text_layer_unreadable"} for item in reviews)


def _extract_codex_candidates(paths: RuntimePaths, job_id: str) -> dict[str, Any]:
    output_dir = paths.jobs_output_root / job_id
    diagnostics = _read_json(output_dir / "pdf_diagnostics.json") or {}
    pdf_names = [
        item.get("source_pdf")
        for item in diagnostics.get("pdfs", [])
        if isinstance(item, dict) and item.get("classification") in {"visual_text_required", "text_layer_unreadable"}
    ]
    try:
        images = _render_candidate_pages(paths, job_id, [str(name) for name in pdf_names if name])
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    if not images:
        return {"ok": False, "error": "No rendered PDF page images available for Codex extraction."}

    schema_path = _candidate_schema_path(paths)
    prompt = _candidate_prompt(job_id)
    result = _app_codex_bridge().run(
        CodexRunRequest(
            prompt=prompt,
            agent_id="pdf-visual-candidate",
            sandbox="read-only",
            timeout_seconds=120,
            images=images,
            output_schema=schema_path,
        ),
        job_id=job_id,
        on_event=lambda event: _append_job_event(paths, job_id, "delta", {"source": "codex_cli", "event": event}),
    )
    if not result.ok:
        return {"ok": False, "error": result.error or "Codex candidate extraction failed."}
    try:
        parsed = json.loads(result.content)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"Codex candidate JSON parse failed: {exc.msg}"}
    candidates = parsed.get("candidates") if isinstance(parsed, dict) else None
    if not isinstance(candidates, list):
        return {"ok": False, "error": "Codex response must contain a candidates list."}
    normalized = [
        _normalize_candidate(item, index, provider="codex_cli", allowed_pdf_names=pdf_names)
        for index, item in enumerate(candidates, start=1)
        if isinstance(item, dict)
    ]
    (output_dir / "codex_candidates.json").write_text(
        json.dumps({"schema_version": 1, "job_id": job_id, "candidates": normalized, "codex_run": result.record.to_dict()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"ok": True, "count": len(normalized)}


def _render_candidate_pages(paths: RuntimePaths, job_id: str, pdf_names: list[str]) -> list[Path]:
    try:
        import fitz  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError("PyMuPDF is required for Codex PDF page rendering. Install the runtime dependencies and retry.") from exc
    run_id = f"{job_id}-{uuid.uuid4().hex[:8]}"
    pages_dir = paths.suite_root / ".state" / "codex-runs" / run_id / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    images: list[Path] = []
    for pdf_name in pdf_names:
        pdf_path = paths.jobs_import_root / job_id / _safe_pdf_name(pdf_name)
        if not pdf_path.exists():
            continue
        doc = None
        try:
            doc = fitz.open(pdf_path)
            for page_index, page in enumerate(doc, start=1):
                if len(images) >= VISUAL_CANDIDATE_MAX_PAGES:
                    raise RuntimeError(
                        f"Visual analysis render limit exceeded ({VISUAL_CANDIDATE_MAX_PAGES} pages). "
                        "Split the PDF or approve a bounded page range before production."
                    )
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                target = pages_dir / f"{pdf_path.stem}-p{page_index}.png"
                pix.save(target)
                images.append(target)
        except RuntimeError:
            raise
        except Exception:
            continue
        finally:
            close = getattr(doc, "close", None)
            if callable(close):
                close()
    return images


def _candidate_prompt(job_id: str) -> str:
    return (
        "Bu teknik ofis PDF sayfalarindan plaka adaylarini oku. Yalnizca JSON dondur.\n"
        "Schema: {\"candidates\":[{\"source_pdf\":\"...pdf\",\"source_page\":1,\"poz_no\":\"1001\",\"width\":200,\"height\":100,\"thickness\":10,\"material\":\"S355\",\"quantity\":1,\"holes\":[{\"x\":50,\"y\":25,\"diameter\":18}],\"slots\":[],\"corner_reliefs\":[],\"contour_type\":\"rectangle|polygon|chamfered\",\"confidence\":0.45,\"evidence\":\"kisa kanit\"}]}\n"
        "Her render edilen sayfayi tek tek incele; yalnizca ilk sayfalari okuyup durma. Poz numarasi sayfa numarasi degildir.\n"
        "Plaka dis konturu dikdortgen degilse bunu `contour_type` alaninda belirt. Duz pah/chamfer varsa `corner_reliefs` icine ilgili koseleri `relief_type=chamfer` ve gorulen pah offseti mm olarak `radius` ile yaz. Yuvarlak/cugul koselerde `round` veya `cugul` kullan.\n"
        "Cizimde 30 mm, 10 mm gibi yan/kenar offsetleri poligon veya pah olusturuyorsa aday bos `corner_reliefs` ile onaylanabilir gorunmemeli; emin degilsen dusuk confidence ve acik evidence yaz.\n"
        "Emin olmadigin olcu veya deligi uydurma; eksikse alani bos birak veya aday verme. Tum adaylar mudur onayi gerektirir.\n"
        f"Job: {job_id}"
    )


def _candidate_schema_path(paths: RuntimePaths) -> Path:
    path = paths.suite_root / ".state" / "codex-runs" / "plate-candidates.v2.schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Codex/OpenAI structured outputs require every object in the schema to
    # explicitly reject extra keys. Use a versioned filename so stale or locked
    # local schemas from older runs cannot keep failing after an upgrade.
    path.write_text(
        json.dumps(_candidate_output_schema(), indent=2),
        encoding="utf-8",
    )
    return path


def _candidate_output_schema() -> dict[str, Any]:
    number_or_null = {"type": ["number", "null"]}
    string_or_null = {"type": ["string", "null"]}
    integer_or_null = {"type": ["integer", "null"]}
    hole_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
            "diameter": {"type": "number"},
        },
        "required": ["x", "y", "diameter"],
        "additionalProperties": False,
    }
    slot_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
            "length": {"type": "number"},
            "width": {"type": "number"},
            "rotation_deg": {"type": "number"},
        },
        "required": ["x", "y", "length", "width", "rotation_deg"],
        "additionalProperties": False,
    }
    corner_relief_schema = {
        "type": "object",
        "properties": {
            "corner": {"type": "string"},
            "radius": {"type": "number"},
            "relief_type": {"type": "string"},
        },
        "required": ["corner", "radius", "relief_type"],
        "additionalProperties": False,
    }
    candidate_schema = {
        "type": "object",
        "properties": {
            "source_pdf": {"type": "string"},
            "source_page": integer_or_null,
            "poz_no": string_or_null,
            "width": number_or_null,
            "height": number_or_null,
            "thickness": number_or_null,
            "material": string_or_null,
            "quantity": integer_or_null,
            "holes": {"type": "array", "items": hole_schema},
            "slots": {"type": "array", "items": slot_schema},
            "corner_reliefs": {"type": "array", "items": corner_relief_schema},
            "contour_type": string_or_null,
            "confidence": {"type": "number"},
            "evidence": string_or_null,
        },
        "required": [
            "source_pdf",
            "source_page",
            "poz_no",
            "width",
            "height",
            "thickness",
            "material",
            "quantity",
            "holes",
            "slots",
            "corner_reliefs",
            "contour_type",
            "confidence",
            "evidence",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"candidates": {"type": "array", "items": candidate_schema}},
        "required": ["candidates"],
        "additionalProperties": False,
    }


def _normalize_candidate(item: dict[str, Any], index: int, *, provider: str, allowed_pdf_names: list[str] | None = None) -> dict[str, Any]:
    source_pdf = str(item.get("source_pdf") or "").strip()
    allowed = [name for name in (allowed_pdf_names or []) if name]
    if source_pdf not in set(allowed) and len(allowed) == 1:
        source_pdf = allowed[0]
    return {
        "candidate_id": str(item.get("candidate_id") or f"{provider}-{index}"),
        "provider": provider,
        "source_pdf": source_pdf or item.get("source_pdf"),
        "source_page": item.get("source_page"),
        "poz_no": item.get("poz_no"),
        "width": item.get("width"),
        "height": item.get("height"),
        "thickness": item.get("thickness"),
        "material": item.get("material") or "UNKNOWN",
        "quantity": item.get("quantity") or 1,
        "holes": item.get("holes") if isinstance(item.get("holes"), list) else [],
        "slots": item.get("slots") if isinstance(item.get("slots"), list) else [],
        "corner_reliefs": item.get("corner_reliefs") if isinstance(item.get("corner_reliefs"), list) else [],
        "contour_type": item.get("contour_type"),
        "confidence": item.get("confidence") or 0.0,
        "evidence": item.get("evidence") or item.get("reason"),
        "approval_required": True,
        "validation_errors": [],
    }


def _approval_rows(payload: dict[str, Any], paths: RuntimePaths, job_id: str) -> list[dict[str, Any]]:
    plates = payload.get("plates")
    if isinstance(plates, list):
        return [item for item in plates if isinstance(item, dict)]
    candidate_ids = payload.get("candidate_ids")
    if not isinstance(candidate_ids, list):
        return []
    wanted = {str(item) for item in candidate_ids}
    codex = _read_json(paths.jobs_output_root / job_id / "codex_candidates.json") or {}
    rows = []
    for item in codex.get("candidates", []):
        if isinstance(item, dict) and str(item.get("candidate_id")) in wanted:
            rows.append(item)
    return rows


def _validate_approved_rows(rows: list[dict[str, Any]], paths: RuntimePaths, job_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ensure_autocad_import_path(paths)
    from autocad_mcp.technical_office.models import CornerReliefSpec, HoleSpec, PlateSpec, SlotSpec

    pdf_names = {path.name for path in (paths.jobs_import_root / job_id).glob("*.pdf")}
    validated: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        try:
            contour_error = _explicit_contour_detail_error(row)
            if contour_error:
                raise ValueError(contour_error)
            source_pdf = str(row.get("source_pdf") or "").strip()
            if source_pdf not in pdf_names and len(pdf_names) == 1:
                source_pdf = next(iter(pdf_names))
            if source_pdf not in pdf_names:
                raise ValueError(f"source_pdf must belong to job: {source_pdf}")
            spec = PlateSpec(
                poz_no=_required_text(row, "poz_no"),
                width=_required_float(row, "width"),
                height=_required_float(row, "height"),
                thickness=_required_float(row, "thickness"),
                material=str(row.get("material") or "UNKNOWN"),
                quantity=int(row.get("quantity") or 1),
                unit_surface_area_m2=_optional_float(row.get("unit_surface_area_m2")),
                unit_weight_kg=_optional_float(row.get("unit_weight_kg")),
                holes=[HoleSpec(x=_required_float(item, "x"), y=_required_float(item, "y"), diameter=_required_float(item, "diameter")) for item in row.get("holes", []) if isinstance(item, dict)],
                slots=[SlotSpec(x=_required_float(item, "x"), y=_required_float(item, "y"), length=_required_float(item, "length"), width=_required_float(item, "width"), rotation_deg=_optional_float(item.get("rotation_deg")) or 0.0) for item in row.get("slots", []) if isinstance(item, dict)],
                corner_reliefs=[CornerReliefSpec(corner=_required_text(item, "corner"), radius=_required_float(item, "radius"), relief_type=_normalize_relief_type(str(item.get("relief_type") or "round"))) for item in row.get("corner_reliefs", []) if isinstance(item, dict)],
                source_page=int(row["source_page"]) if row.get("source_page") not in (None, "") else None,
                confidence=_optional_float(row.get("confidence")) or 1.0,
                notes=["manager_approved_from_visual_candidate"],
            )
            validation = spec.validate()
            if validation:
                raise ValueError("; ".join(validation))
            data = spec.to_dict()
            data["source_pdf"] = source_pdf
            validated.append(data)
        except Exception as exc:
            errors.append({"row": index, "candidate_id": row.get("candidate_id"), "error": str(exc)})
    if not errors:
        coverage_errors = _approved_visual_page_coverage_errors(validated, paths, job_id)
        if coverage_errors:
            return [], coverage_errors
    return validated, errors


def _approved_visual_page_coverage_errors(validated: list[dict[str, Any]], paths: RuntimePaths, job_id: str) -> list[dict[str, Any]]:
    diagnostics = _read_json(paths.jobs_output_root / job_id / "pdf_diagnostics.json") or {}
    pdfs = diagnostics.get("pdfs") if isinstance(diagnostics, dict) else None
    if not isinstance(pdfs, list):
        return []
    errors: list[dict[str, Any]] = []
    for pdf in pdfs:
        if not isinstance(pdf, dict) or pdf.get("classification") not in {"visual_text_required", "text_layer_unreadable"}:
            continue
        source_pdf = str(pdf.get("source_pdf") or "")
        page_count = pdf.get("page_count")
        if not source_pdf or not isinstance(page_count, int) or page_count <= 1:
            continue
        covered_pages = {
            int(row["source_page"])
            for row in validated
            if row.get("source_pdf") == source_pdf and isinstance(row.get("source_page"), int)
        }
        missing_pages = [page for page in range(1, page_count + 1) if page not in covered_pages]
        if not missing_pages:
            continue
        preview = ", ".join(str(page) for page in missing_pages[:12])
        suffix = "" if len(missing_pages) <= 12 else f", ... +{len(missing_pages) - 12}"
        errors.append(
            {
                "row": "job",
                "candidate_id": None,
                "error": (
                    f"{source_pdf} requires visual review coverage for {page_count} pages; "
                    f"approved candidates cover {len(covered_pages)} pages. Missing pages: {preview}{suffix}"
                ),
            }
        )
    return errors


def _explicit_contour_detail_error(row: dict[str, Any]) -> str | None:
    if isinstance(row.get("corner_reliefs"), list) and row["corner_reliefs"]:
        return None
    contour_type = str(row.get("contour_type") or "").strip().lower()
    evidence = str(row.get("evidence") or row.get("reason") or "").strip().lower()
    text = f"{contour_type} {evidence}"
    strong_terms = ("pah", "chamfer", "poligon", "polygon", "polygonal", "chamfered")
    offset_terms = ("side offset", "edge offset", "kenar offset", "yan offset", "kose offset", "köşe offset")
    if any(term in text for term in strong_terms) or any(term in text for term in offset_terms):
        return (
            "candidate evidence indicates polygon/chamfer/edge-offset contour, but corner_reliefs is empty. "
            "Add explicit corner_reliefs entries with relief_type=chamfer/round/cugul or send this poz to manual review."
        )
    return None


def _normalize_relief_type(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"pah", "bevel", "beveled", "chamfered"}:
        return "chamfer"
    if normalized in {"rounded", "radius"}:
        return "round"
    return normalized or "round"


def _required_text(row: dict[str, Any], key: str) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _required_float(row: dict[str, Any], key: str) -> float:
    value = _optional_float(row.get(key))
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(str(value).replace(",", "."))


def _app_codex_bridge() -> CodexBridge:
    bridge = getattr(app.state, "codex_bridge", None)
    if isinstance(bridge, CodexBridge):
        return bridge
    if bridge is not None and hasattr(bridge, "run"):
        return bridge
    resolved = CodexBridge(get_paths())
    app.state.codex_bridge = resolved
    return resolved


def _save_turn(
    paths: RuntimePaths,
    session_id: str,
    history: list[dict[str, Any]],
    user_text: str,
    content: str,
    *,
    selected_job_id: str | None = None,
) -> None:
    now_ms = _now_ms()
    save_session(
        paths.sessions_root,
        session_id,
        history + [{"role": "user", "content": user_text, "timestamp": now_ms}, {"role": "assistant", "content": content, "timestamp": _now_ms()}],
    )
    if session_id.startswith("agent:teknik-ofis-muduru:"):
        try:
            get_manager_memory(paths.workspace_root).record_turn(
                session_id=session_id,
                user_text=user_text,
                assistant_text=content,
                selected_job_id=selected_job_id,
            )
        except Exception:
            pass


def _append_job_event(paths: RuntimePaths, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
    if event_type not in JOB_STATUS_VALUES and event_type not in {"started", "delta", "candidate", "failed", "completed", "codex_extracting"}:
        event_type = "delta"
    path = _event_log_path(paths, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"type": event_type, "timestamp": _now_ms(), "payload": payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _event_log_path(paths: RuntimePaths, job_id: str) -> Path:
    return paths.jobs_output_root / job_id / "events.jsonl"


def _event_stream(paths: RuntimePaths, job_id: str) -> Iterable[str]:
    path = _event_log_path(paths, job_id)
    emitted = 0
    terminal_seen = False
    last_heartbeat = time.monotonic()
    started = time.monotonic()
    while True:
        events = _read_events(path)
        for event in events[emitted:]:
            emitted += 1
            if event.get("type") in {"completed", "failed"}:
                terminal_seen = True
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        if terminal_seen:
            yield f"data: {json.dumps({'type': 'completed', 'timestamp': _now_ms(), 'payload': {'replay': True}}, ensure_ascii=False)}\n\n"
            return
        now = time.monotonic()
        if now - last_heartbeat >= 5:
            last_heartbeat = now
            yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': _now_ms(), 'payload': {'job_id': job_id}}, ensure_ascii=False)}\n\n"
        if now - started > 300:
            yield f"data: {json.dumps({'type': 'completed', 'timestamp': _now_ms(), 'payload': {'timeout': True}}, ensure_ascii=False)}\n\n"
            return
        time.sleep(0.25)


def _resolve_job_file(paths: RuntimePaths, job_id: str, filename: str) -> Path:
    requested = filename.replace("\\", "/").strip("/")
    if not requested or ".." in Path(requested).parts:
        raise HTTPException(status_code=400, detail="invalid file path")
    roots = [paths.jobs_import_root / job_id, paths.jobs_output_root / job_id]
    for root in roots:
        candidate = (root / requested).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.exists() and candidate.is_file():
            return candidate
    basename = Path(requested).name
    for root in roots:
        if root.exists():
            for candidate in root.rglob(basename):
                if candidate.is_file():
                    return candidate
    return roots[0] / requested


def _job_files(paths: RuntimePaths, job_id: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    roots = [
        ("input", paths.jobs_import_root / job_id),
        ("output", paths.jobs_output_root / job_id),
    ]
    for group, root in roots:
        if not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if path.name == "events.jsonl":
                continue
            rel = path.relative_to(root).as_posix()
            files.append(
                {
                    "group": group,
                    "name": path.name,
                    "path": rel,
                    "size_bytes": path.stat().st_size,
                    "download_url": f"/api/jobs/{quote(job_id)}/files/{quote(rel, safe='/')}",
                }
            )
    return files


def _partlist_detail(paths: RuntimePaths, job_id: str) -> dict[str, Any] | None:
    output_dir = paths.jobs_output_root / job_id
    review = _read_json(output_dir / "partlist_manual_review_required.json")
    workbooks = sorted(output_dir.glob("*_partlist.xlsx"))
    if workbooks:
        workbook = workbooks[-1]
        rel = workbook.relative_to(output_dir).as_posix()
        return {
            "ok": True,
            "path": rel,
            "download_url": f"/api/jobs/{quote(job_id)}/files/{quote(rel, safe='/')}",
            "manual_reviews": [],
        }
    if review is not None:
        return {"ok": False, "path": None, "manual_reviews": review}
    return None


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _relative(path: Path, paths: RuntimePaths) -> str:
    try:
        return str(path.resolve().relative_to(paths.suite_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _safe_job_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-")
    return safe[:80] or f"job-{uuid.uuid4().hex[:8]}"


def _safe_pdf_name(value: str) -> str:
    name = Path(value).name
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip(".-")
    if not safe.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail=f"Only PDF files are allowed: {value}")
    return safe


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return _safe_job_id(re.sub(r"[^A-Za-z0-9]+", "-", ascii_text.lower()).strip("-") or "job")


def _bounded_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(high, max(low, parsed))


def _now_ms() -> int:
    return int(time.time() * 1000)
