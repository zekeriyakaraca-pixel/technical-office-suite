from __future__ import annotations

import asyncio
import json
import mimetypes
import os
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

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse

from .audit import get_audit_logger
from .approval_validation import annotate_candidate_qualities, validate_approved_rows
from .auth import auth_status, auth_enabled, create_file_token, require_auth, require_file_auth
from .codex_bridge import CodexBridge, CodexRunRequest
from .config import RuntimePaths, ensure_autocad_import_path, get_paths
from .constants import AUTH_TOKEN_STORAGE_KEY, MANAGER_DASHBOARD_SESSION_ID
from .completion import (
    DEFAULT_MANAGER_SESSION_ID,
    append_job_event as append_completion_event,
    backfill_all_learning,
    backfill_job_learning,
    complete_approved_job,
    learning_health,
    notify_job_blocked,
)
from .job_fsm import IN_PROGRESS_STATES, JobState, get_fsm
from .manager_memory import get_manager_memory
from .memory_bridge import get_memory_bridge
from .metrics import get_metrics_summary, prometheus_text, record_job_status
from .orchestrator import AgentOrchestrator
from .rate_limit import rate_limit_middleware
from .relief_types import normalize_relief_type
from .sla import get_sla_monitor
from .state_io import atomic_write_json
from .workers import get_workers
from .registry import MODEL_ID, registry_response, runtime_metadata, state_response
from .session_store import (
    cleanup_dashboard_guided_flow_session,
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
from .visual_evidence import (
    evidence_for_page,
    load_microzoom_manifest,
    microzoom_manifest_is_valid_for_candidate,
    microzoom_manifest_path_for_images,
    render_microzoom_images,
    write_microzoom_manifest,
)


log = structlog.get_logger(__name__)

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


def _cors_origins() -> list[str]:
    configured = os.environ.get("TOFFICE_CORS_ORIGINS", "").strip()
    if not configured:
        return ["http://localhost:3000", "http://127.0.0.1:3000"]
    origins = [origin.strip() for origin in configured.split(",") if origin.strip()]
    if "*" in origins:
        return []
    return origins or ["http://localhost:3000", "http://127.0.0.1:3000"]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    paths = get_paths()
    sanitize_session_files(paths.sessions_root)
    cleanup_dashboard_guided_flow_session(paths.sessions_root)
    yield


app = FastAPI(title="Technical Office Runtime", version="0.2.0", lifespan=lifespan)

app.middleware("http")(rate_limit_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    path = Path(__file__).resolve().parent / "static" / "index.html"
    if not path.exists():
        return HTMLResponse("<h1>Technical Office Runtime</h1><p>Dashboard asset missing.</p>")
    html = path.read_text(encoding="utf-8").replace("__TOFFICE_TOKEN_STORAGE_KEY__", AUTH_TOKEN_STORAGE_KEY)
    return HTMLResponse(html)


@app.get("/health")
def health() -> dict[str, Any]:
    paths = get_paths()
    job_snapshot = _job_status_snapshot(paths)
    return {
        "ok": True,
        "status": "ready",
        "runtime": runtime_metadata(),
        "jobs_active": sum(1 for j in job_snapshot if j.get("fsm_state") in IN_PROGRESS_STATES),
        "jobs_total": len(job_snapshot),
        "auth": auth_status(),
    }


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    return health()


@app.get("/metrics", response_class=PlainTextResponse, dependencies=[Depends(require_auth)])
def metrics_prometheus() -> PlainTextResponse:
    return PlainTextResponse(prometheus_text(), media_type="text/plain; version=0.0.4")


@app.get("/api/metrics", dependencies=[Depends(require_auth)])
def metrics_json() -> dict[str, Any]:
    return {"ok": True, "metrics": get_metrics_summary()}


@app.get("/api/audit", dependencies=[Depends(require_auth)])
def audit_log(limit: int = 100) -> dict[str, Any]:
    paths = get_paths()
    logger = get_audit_logger(paths.workspace_root)
    return {"ok": True, "entries": logger.read_recent(limit=min(limit, 500))}


@app.get("/api/audit/{job_id}", dependencies=[Depends(require_auth)])
def audit_log_for_job(job_id: str) -> dict[str, Any]:
    paths = get_paths()
    _require_job(paths, job_id)
    logger = get_audit_logger(paths.workspace_root)
    return {"ok": True, "job_id": job_id, "entries": logger.read_for_job(job_id)}


@app.get("/api/sla/report", dependencies=[Depends(require_auth)])
def sla_report() -> dict[str, Any]:
    paths = get_paths()
    monitor = get_sla_monitor(paths.jobs_import_root, paths.jobs_output_root)
    jobs = [_job_detail(paths, record["job_id"], shallow=True) for record in _list_job_records(paths)]
    return {"ok": True, "report": monitor.report(jobs)}


@app.get("/api/sla/overdue", dependencies=[Depends(require_auth)])
def sla_overdue() -> dict[str, Any]:
    paths = get_paths()
    monitor = get_sla_monitor(paths.jobs_import_root, paths.jobs_output_root)
    jobs = [_job_detail(paths, record["job_id"], shallow=True) for record in _list_job_records(paths)]
    return {"ok": True, "overdue": monitor.get_overdue_jobs(jobs)}


@app.get("/api/memory/stats", dependencies=[Depends(require_auth)])
def memory_stats() -> dict[str, Any]:
    paths = get_paths()
    bridge = get_memory_bridge(paths.workspace_root)
    return {"ok": True, "stats": bridge.get_pattern_stats()}


@app.get("/api/memory/patterns", dependencies=[Depends(require_auth)])
def memory_patterns(limit: int = 50) -> dict[str, Any]:
    paths = get_paths()
    bridge = get_memory_bridge(paths.workspace_root)
    return {"ok": True, "patterns": bridge.list_patterns(limit=min(limit, 200))}


@app.get("/api/learning/health", dependencies=[Depends(require_auth)])
def learning_health_api() -> dict[str, Any]:
    return learning_health(get_paths())


@app.post("/api/jobs/{job_id}/learning/backfill", dependencies=[Depends(require_auth)])
async def backfill_job_learning_api(job_id: str, request: Request) -> dict[str, Any]:
    paths = get_paths()
    _require_job(paths, job_id)
    payload = await _optional_json(request)
    return backfill_job_learning(
        paths,
        job_id,
        dry_run=bool(payload.get("dry_run", False)),
        session_id=_optional_str(payload.get("session_id")) or DEFAULT_MANAGER_SESSION_ID,
    )


@app.post("/api/learning/backfill", dependencies=[Depends(require_auth)])
async def backfill_all_learning_api(request: Request) -> dict[str, Any]:
    payload = await _optional_json(request)
    return backfill_all_learning(get_paths(), dry_run=bool(payload.get("dry_run", True)))


@app.get("/api/manager/memory", dependencies=[Depends(require_auth)])
def manager_memory(job_id: str | None = None, session_id: str = MANAGER_DASHBOARD_SESSION_ID, limit: int = 20) -> dict[str, Any]:
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


@app.get("/state", dependencies=[Depends(require_auth)])
def state() -> dict[str, Any]:
    return state_response(get_paths())


@app.get("/registry", dependencies=[Depends(require_auth)])
def registry() -> dict[str, Any]:
    return registry_response(get_paths())


@app.get("/status", dependencies=[Depends(require_auth)])
def runtime_status() -> dict[str, Any]:
    paths = get_paths()
    return {
        "ok": True,
        "runtime": runtime_metadata(),
        "sessions": runtime_session_status(paths.sessions_root),
        "agents": _agent_runtime_states(paths),
        "jobs": _job_status_snapshot(paths),
    }


@app.get("/sessions", dependencies=[Depends(require_auth)])
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


@app.get("/sessions/history", dependencies=[Depends(require_auth)])
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


@app.post("/sessions/preview", dependencies=[Depends(require_auth)])
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


@app.post("/sessions/reset", dependencies=[Depends(require_auth)])
async def sessions_reset(request: Request) -> dict[str, Any]:
    payload = await request.json()
    session_id = _optional_str(payload.get("session_id") or payload.get("key"))
    if not session_id:
        return {"ok": False, "error": "session_id is required"}
    removed = reset_session(get_paths().sessions_root, session_id)
    return {"ok": True, "removed": removed, "session_id": session_id}


@app.post("/v1/chat/completions", response_model=None, dependencies=[Depends(require_auth)])
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


@app.post("/api/manager/chat", dependencies=[Depends(require_auth)])
async def manager_chat(request: Request) -> dict[str, Any]:
    payload = await _required_json_object(request)
    agent_id = _optional_str(payload.get("agent_id")) or "teknik-ofis-muduru"
    if agent_id != "teknik-ofis-muduru":
        raise HTTPException(status_code=403, detail="Only teknik-ofis-muduru chat is enabled.")
    message = _optional_str(payload.get("message") or payload.get("content"))
    trigger = _manager_structured_trigger(payload.get("trigger"))
    if not message and trigger is None:
        raise HTTPException(status_code=400, detail="message or trigger is required")
    paths = get_paths()
    session_id = _optional_str(payload.get("session_id") or payload.get("conversation_id")) or MANAGER_DASHBOARD_SESSION_ID
    incoming_history = _manager_incoming_history(payload.get("history"), message or "")
    server_history = load_session(paths.sessions_root, session_id)
    history = merge_history(server_history, incoming_history)
    selected_job_id = _optional_str(payload.get("selected_job_id") or payload.get("job_id"))
    memory_recall = get_manager_memory(paths.workspace_root).recall(
        session_id=session_id,
        message=message or "",
        selected_job_id=selected_job_id,
        history=history,
    )
    enriched_message = _manager_message_with_context(paths, message or "", selected_job_id, memory_context=memory_recall.to_payload())
    orchestrator = AgentOrchestrator(agent_id="teknik-ofis-muduru", bridge=_app_codex_bridge())
    result = await asyncio.to_thread(
        orchestrator.run,
        enriched_message,
        history,
        session_id=session_id,
        selected_job_id=selected_job_id or memory_recall.primary_job_id,
        trigger=trigger,
    )
    if message:
        _save_turn(
            paths,
            session_id,
            history,
            message,
            result.content,
            selected_job_id=selected_job_id or memory_recall.primary_job_id,
        )
    else:
        _save_assistant_turn(paths, session_id, history, result.content)
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

    files = [item for item in form.getlist("pdf_files") if hasattr(item, "filename") and hasattr(item, "read")]
    if not files:
        raise HTTPException(status_code=400, detail="pdf_files[] is required")
    saved: list[dict[str, Any]] = []
    max_file_bytes = _upload_limit_bytes("TOFFICE_MAX_UPLOAD_MB", 100)
    max_job_bytes = _upload_limit_bytes("TOFFICE_MAX_JOB_UPLOAD_MB", 300)
    total_bytes = 0
    pending_files: list[tuple[str, bytes]] = []
    for item in files:
        filename = _safe_pdf_name(str(item.filename or "input.pdf"))
        data = await item.read()
        if not data:
            raise HTTPException(status_code=400, detail=f"Empty PDF upload: {filename}")
        if len(data) > max_file_bytes:
            raise HTTPException(status_code=413, detail=f"PDF too large: {filename}")
        total_bytes += len(data)
        if total_bytes > max_job_bytes:
            raise HTTPException(status_code=413, detail="Total PDF upload size exceeds job limit")
        pending_files.append((filename, data))
        saved.append({"name": filename, "size_bytes": len(data)})

    job_dir.mkdir(parents=True)
    for filename, data in pending_files:
        (job_dir / filename).write_bytes(data)

    metadata = {
        "job_id": job_id,
        "project_name": project_name,
        "manager_agent": "teknik-ofis-muduru",
        "created_at": datetime.now().astimezone().isoformat(),
    }
    atomic_write_json(job_dir / "job.json", metadata)
    _append_job_event(paths, job_id, "started", {"message": "Job uploaded", "pdfs": saved})
    get_audit_logger(paths.workspace_root).log_job_created(job_id, project_name, len(saved))
    record_job_status("uploaded")
    return {"ok": True, "job": _job_detail(paths, job_id)}


@app.get("/api/jobs", dependencies=[Depends(require_auth)])
def list_jobs_api() -> dict[str, Any]:
    paths = get_paths()
    return {"ok": True, "jobs": [_job_detail(paths, record["job_id"], shallow=True) for record in _list_job_records(paths)]}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
def get_job_api(job_id: str) -> dict[str, Any]:
    paths = get_paths()
    _require_job(paths, job_id)
    return {"ok": True, "job": _job_detail(paths, job_id)}


@app.post("/api/jobs/{job_id}/file-ticket", dependencies=[Depends(require_auth)])
async def create_file_ticket(job_id: str, request: Request) -> dict[str, Any]:
    paths = get_paths()
    _require_job(paths, job_id)
    payload = await _required_json_object(request)
    requested_path = _optional_str(payload.get("path") or payload.get("filename"))
    if not requested_path:
        raise HTTPException(status_code=400, detail="path is required")
    inline = bool(payload.get("inline", False))
    resolved = _resolve_job_file(paths, job_id, requested_path)
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="file not found")
    relative_path = _relative_job_file_path(paths, job_id, resolved)
    url = f"/api/jobs/{quote(job_id)}/files/{quote(relative_path, safe='/')}"
    query = "inline=true" if inline else ""
    if auth_enabled():
        token = create_file_token(job_id, relative_path, inline=inline, expires_seconds=300)
        token_query = f"file_token={quote(token)}"
        query = f"{query}&{token_query}" if query else token_query
    if query:
        url = f"{url}?{query}"
    return {"ok": True, "url": url, "path": relative_path, "expires_seconds": 300 if auth_enabled() else None}


@app.get("/api/jobs/{job_id}/files/{filename:path}")
def get_job_file(request: Request, job_id: str, filename: str, inline: bool = False) -> FileResponse:
    paths = get_paths()
    _require_job(paths, job_id)
    path = _resolve_job_file(paths, job_id, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    require_file_auth(request, job_id=job_id, file_path=_relative_job_file_path(paths, job_id, path), inline=inline)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if inline:
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(path.name)}"},
        )
    return FileResponse(path, media_type=media_type, filename=path.name)


def _try_apply_memory_page_hints(paths: RuntimePaths, job_id: str) -> None:
    """Aynı PDF için geçmiş müdür kararı varsa page_exclusions.json yaz."""
    import hashlib as _hashlib
    job_dir = paths.jobs_import_root / job_id
    exclusions_path = job_dir / "page_exclusions.json"
    if exclusions_path.exists():
        return
    try:
        from .memory_bridge import get_memory_bridge
        bridge = get_memory_bridge(paths.workspace_root)
        for pdf in job_dir.glob("*.pdf"):
            sha256 = _hashlib.sha256(pdf.read_bytes()).hexdigest()
            hints = bridge.find_page_hints(sha256)
            if hints:
                atomic_write_json(
                    exclusions_path,
                        {
                            "schema_version": 1,
                            "source": "memory_bridge_hint",
                            "excluded_pages": [
                                {
                                    "page": p,
                                    "reason": "non_plate_page",
                                    "note": "hafıza tabanından öneri",
                                }
                                for p in hints
                            ],
                        },
                )
                _append_job_event(
                    paths,
                    job_id,
                    "memory_page_hint_applied",
                    {"pdf": pdf.name, "pages": hints, "source": "memory_bridge"},
                )
                break
    except Exception as exc:
        log.warning("memory_page_hint_failed", job_id=job_id, error=str(exc))


@app.post("/api/jobs/{job_id}/run", dependencies=[Depends(require_auth)])
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
    session_id = _optional_str(payload.get("session_id")) or DEFAULT_MANAGER_SESSION_ID

    fsm.transition(job_id, JobState.CLASSIFYING, reason="run_api")
    _append_job_event(paths, job_id, "started", {"message": "Deterministic pipeline started", "fsm_state": "classifying"})
    _try_apply_memory_page_hints(paths, job_id)
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
            if codex_result.get("error"):
                _append_job_event(
                    paths,
                    job_id,
                    "visual_extraction_failed",
                    {
                        "status": codex_result.get("status") or codex_result.get("extraction_status"),
                        "error": codex_result.get("error"),
                    },
                )
            # Background workers: pre-validate + consensus check (fire-and-forget)
            asyncio.create_task(
                get_workers(paths).run_post_extraction_workers(job_id),
                name=f"post_extraction_{job_id}",
            )
        else:
            fsm.transition(job_id, JobState.AWAITING_APPROVAL, reason="visual_extraction_failed")
            _append_job_event(paths, job_id, "visual_extraction_failed", {"stage": "codex_extracting", "error": codex_result.get("error")})
        _append_job_event(paths, job_id, "completed", {"status": "needs_manager_approval"})
    else:
        if summary.get("ok"):
            fsm.force_transition(job_id, JobState.PRODUCING, reason="production_started")
            append_completion_event(paths, job_id, "production_started", {"message": "Direct pipeline production completed"})
            completion = complete_approved_job(
                paths,
                job_id,
                summary,
                approved_count=None,
                session_id=session_id,
            )
            return {
                "ok": bool(completion.get("ok")),
                "message": completion.get("message"),
                "partlist": completion.get("partlist"),
                "retrospective": completion.get("retrospective"),
                "error": completion.get("error"),
                "job": _job_detail(paths, job_id),
            }
        else:
            manual_reviews = summary.get("manual_reviews") if isinstance(summary.get("manual_reviews"), list) else []
            if manual_reviews:
                blocked = notify_job_blocked(paths, job_id, reason="pipeline_manual_review_pending", session_id=session_id, summary=summary)
                return {"ok": False, "error": blocked.get("error"), "message": blocked.get("message"), "job": _job_detail(paths, job_id)}
            fsm.transition(job_id, JobState.FAILED, reason="pipeline_failed")
            _append_job_event(paths, job_id, "failed", {"status": "failed"})
    return {"ok": True, "job": _job_detail(paths, job_id)}


@app.post("/api/jobs/{job_id}/approve-candidates", dependencies=[Depends(require_auth)])
async def approve_candidates(job_id: str, request: Request) -> dict[str, Any]:
    paths = get_paths()
    job_dir = _require_job(paths, job_id)
    fsm = get_fsm(paths.jobs_output_root)
    payload = await request.json()
    session_id = _optional_str(payload.get("session_id")) or DEFAULT_MANAGER_SESSION_ID
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
    atomic_write_json(job_dir / "approved_plate_specs.json", approval)
    fsm.force_transition(job_id, JobState.PRODUCING, reason="manager_approved")
    append_completion_event(paths, job_id, "production_started", {"approved_count": len(validated)})

    tool = ToolRegistry(paths)
    result = tool.run("run_autocad_job", {"job_id": job_id, "autocad_live_policy": str(payload.get("autocad_live_policy") or "off")})
    if not result.get("ok"):
        blocked = notify_job_blocked(
            paths,
            job_id,
            reason=str(result.get("error") or "production_failed"),
            session_id=session_id,
            state=JobState.FAILED,
        )
        return {"ok": False, "error": blocked.get("error"), "message": blocked.get("message"), "job": _job_detail(paths, job_id)}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    if summary.get("ok") is not True:
        manual_reviews = summary.get("manual_reviews") if isinstance(summary.get("manual_reviews"), list) else []
        reason = "production_manual_review_pending" if manual_reviews else "qc_failed"
        append_completion_event(
            paths,
            job_id,
            "qc_completed",
            {
                "ok": False,
                "manual_review_count": len(manual_reviews),
                "produced_count": len(summary.get("produced", [])) if isinstance(summary.get("produced"), list) else 0,
            },
        )
        blocked = notify_job_blocked(paths, job_id, reason=reason, session_id=session_id, summary=summary)
        return {
            "ok": False,
            "error": blocked.get("error"),
            "message": blocked.get("message"),
            "manual_reviews": manual_reviews,
            "job": _job_detail(paths, job_id),
        }
    poz_nos = [str(p.get("poz_no", "")) for p in validated if isinstance(p, dict)]
    get_audit_logger(paths.workspace_root).log_approval(job_id, "teknik-ofis-muduru", len(validated), poz_nos)
    completion = complete_approved_job(
        paths,
        job_id,
        summary,
        approved_count=len(validated),
        session_id=session_id,
    )
    return {
        "ok": bool(completion.get("ok")),
        "message": completion.get("message"),
        "partlist": completion.get("partlist"),
        "retrospective": completion.get("retrospective"),
        "error": completion.get("error"),
        "job": _job_detail(paths, job_id),
    }


@app.post("/api/jobs/{job_id}/partlist", dependencies=[Depends(require_auth)])
async def create_partlist_api(job_id: str, request: Request) -> dict[str, Any]:
    paths = get_paths()
    job_dir = _require_job(paths, job_id)
    output_dir = paths.jobs_output_root / job_id
    payload = await _optional_json(request)
    session_id = _optional_str(payload.get("session_id")) or DEFAULT_MANAGER_SESSION_ID
    summary = _read_json(output_dir / "job_summary.json")
    if isinstance(summary, dict):
        completion = complete_approved_job(
            paths,
            job_id,
            summary,
            approved_count=None,
            session_id=session_id,
        )
        return {
            "ok": bool(completion.get("ok")),
            "message": completion.get("message"),
            "partlist": completion.get("partlist"),
            "retrospective": completion.get("retrospective"),
            "error": completion.get("error"),
            "job": _job_detail(paths, job_id),
        }

    ensure_autocad_import_path(paths)
    from autocad_mcp.technical_office.partlist import create_partlist

    append_completion_event(paths, job_id, "partlist_started", {"message": "Partlist generation requested"})
    result = create_partlist(job_dir, output_dir)
    append_completion_event(
        paths,
        job_id,
        "partlist_completed",
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
    completion = backfill_job_learning(paths, job_id, dry_run=False, session_id=session_id)
    if not result.ok:
        record_job_status("awaiting_approval")
    return {
        "ok": bool(completion.get("ok")),
        "message": completion.get("message"),
        "partlist": partlist_dict,
        "retrospective": completion.get("retrospective"),
        "error": completion.get("error") or (None if result.ok else "partlist_failed"),
        "job": _job_detail(paths, job_id),
    }


@app.get("/api/events/{job_id}")
def job_events(request: Request, job_id: str) -> StreamingResponse:
    require_auth(request)
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
        except Exception as exc:
            log.warning("selected_job_context_failed", job_id=selected_job_id, error=str(exc))
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


def _manager_incoming_history(messages: Any, current_message: str) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        return []
    history: list[dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            history.append({"role": role, "content": content})
    if history and history[-1]["role"] == "user" and history[-1]["content"].strip() == current_message.strip():
        history = history[:-1]
    return history


def _manager_structured_trigger(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    trigger_type = _optional_str(value.get("type"))
    if not trigger_type:
        return None
    trigger: dict[str, Any] = {"type": trigger_type}
    job_id = _optional_str(value.get("job_id"))
    if job_id:
        trigger["job_id"] = job_id
    validation_errors = value.get("validation_errors")
    if isinstance(validation_errors, list):
        trigger["validation_errors"] = [dict(item) for item in validation_errors if isinstance(item, dict)]
    candidate_ids = value.get("candidate_ids")
    if isinstance(candidate_ids, list):
        trigger["candidate_ids"] = [str(item) for item in candidate_ids if item is not None]
    return trigger


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


def _reconcile_manual_reviews(output_dir: Path) -> list[dict[str, Any]]:
    """Codex adayı veya sayfa dışlaması olan review'ları aktif listeden çıkar."""
    reviews = _read_json(output_dir / "manual_review_required.json") or []
    if not isinstance(reviews, list) or not reviews:
        return []
    candidates_data = _read_json(output_dir / "codex_candidates.json") or {}
    exclusions = _read_json(output_dir / "page_exclusions_applied.json") or {}
    covered: set[tuple[str | None, int]] = {
        (c.get("source_pdf"), int(c["source_page"]))
        for c in (candidates_data.get("candidates") or [])
        if isinstance(c, dict) and c.get("source_page") is not None
    }
    excluded_pages: set[int] = {
        int(e["page"])
        for e in (exclusions.get("excluded_pages") or [])
        if isinstance(e, dict) and e.get("page") is not None
    }
    active = []
    for r in reviews:
        if not isinstance(r, dict):
            continue
        page = r.get("page")
        pdf = r.get("source_pdf")
        if page is not None and int(page) in excluded_pages:
            continue
        if page is not None and ((pdf, int(page)) in covered or (None, int(page)) in covered):
            continue
        active.append(r)
    return active


def _job_detail(paths: RuntimePaths, job_id: str, *, shallow: bool = False) -> dict[str, Any]:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    summary = _read_json(output_dir / "job_summary.json") or {}
    manual_reviews = summary.get("manual_reviews")
    produced = summary.get("produced")
    fsm_state = get_fsm(paths.jobs_output_root).get_state(job_id).value
    active_reviews = _reconcile_manual_reviews(output_dir)
    detail: dict[str, Any] = {
        "job_id": job_id,
        "status": _job_status(job_dir, output_dir, summary),
        "fsm_state": fsm_state,
        "input_dir": _relative(job_dir, paths),
        "output_dir": _relative(output_dir, paths),
        "project_name": (_read_json(job_dir / "job.json") or {}).get("project_name"),
        "pdf_count": len(list(job_dir.glob("*.pdf"))) if job_dir.exists() else 0,
        "manual_review_count": len(manual_reviews) if isinstance(manual_reviews, list) else 0,
        "active_manual_review_count": len(active_reviews),
        "active_manual_reviews": active_reviews,
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
                    "path": path.name,
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
    except Exception as exc:
        log.debug("optional_json_ignored", path=str(request.url.path), error=str(exc))
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


_VISUAL_REVIEW_REASONS = {"visual_text_required", "text_layer_unreadable", "plate_geometry_not_found"}


def _summary_requires_visual_candidates(summary: dict[str, Any]) -> bool:
    reviews = summary.get("manual_reviews")
    if not isinstance(reviews, list):
        return False
    return any(isinstance(item, dict) and item.get("reason") in _VISUAL_REVIEW_REASONS for item in reviews)


def _extract_codex_candidates(paths: RuntimePaths, job_id: str) -> dict[str, Any]:
    output_dir = paths.jobs_output_root / job_id
    diagnostics = _read_json(output_dir / "pdf_diagnostics.json") or {}
    pdf_names = [
        item.get("source_pdf")
        for item in diagnostics.get("pdfs", [])
        if isinstance(item, dict) and item.get("classification") in _VISUAL_REVIEW_REASONS
    ]
    exclusions = _read_json(output_dir / "page_exclusions_applied.json") or {}
    excluded_pages: set[int] = {
        int(e["page"])
        for e in (exclusions.get("excluded_pages") or [])
        if isinstance(e, dict) and e.get("page") is not None
    }
    try:
        images, evidence_meta = _render_candidate_pages(paths, job_id, [str(name) for name in pdf_names if name], excluded_pages=excluded_pages)
    except RuntimeError as exc:
        _write_visual_extraction_failure(paths, job_id, str(exc), status="render_failed")
        return {"ok": True, "count": 0, "status": "render_failed", "extraction_status": "visual_extraction_failed", "error": str(exc)}
    if not images:
        error = "No rendered PDF page images available for Codex extraction."
        _write_visual_extraction_failure(paths, job_id, error, status="rendered_no_pages")
        return {"ok": True, "count": 0, "status": "rendered_no_pages", "extraction_status": "visual_extraction_failed", "error": error}
    microzoom_manifest_path = microzoom_manifest_path_for_images(images)
    microzoom_manifest = load_microzoom_manifest(microzoom_manifest_path)

    pdf_import_paths = [
        str(paths.jobs_import_root / job_id / _safe_pdf_name(n))
        for n in pdf_names if n
    ]
    schema_path = _candidate_schema_path(paths)
    prompt = _candidate_prompt(
        job_id,
        paths,
        pdf_import_paths=pdf_import_paths,
        manifest_path=str(microzoom_manifest_path) if microzoom_manifest_path else None,
    )
    evidence_paths = [Path(e["path"]) for e in evidence_meta if e.get("path")]
    all_images = images + evidence_paths
    result = _app_codex_bridge().run(
        CodexRunRequest(
            prompt=prompt,
            agent_id="pdf-visual-candidate",
            sandbox="read-only",
            timeout_seconds=120,
            images=all_images,
            output_schema=schema_path,
        ),
        job_id=job_id,
        on_event=lambda event: _append_job_event(paths, job_id, "delta", {"source": "codex_cli", "event": event}),
    )
    extraction_status = "extracted"
    if not result.ok:
        extraction_status = "partial_timeout" if _is_codex_timeout(result.error) else "partial_codex_failed"
    try:
        parsed = json.loads(result.content)
    except json.JSONDecodeError as exc:
        error = result.error or f"Codex candidate JSON parse failed: {exc.msg}"
        _write_visual_extraction_failure(paths, job_id, error, status="codex_json_failed")
        return {"ok": True, "count": 0, "status": "codex_json_failed", "extraction_status": "visual_extraction_failed", "error": error}
    candidates = parsed.get("candidates") if isinstance(parsed, dict) else None
    if not isinstance(candidates, list):
        error = result.error or "Codex response must contain a candidates list."
        _write_visual_extraction_failure(paths, job_id, error, status="codex_json_failed")
        return {"ok": True, "count": 0, "status": "codex_json_failed", "extraction_status": "visual_extraction_failed", "error": error}
    normalized = [
        _normalize_candidate(
            item,
            index,
            provider="codex_cli",
            allowed_pdf_names=pdf_names,
            visual_manifest=microzoom_manifest,
            microzoom_manifest_path=microzoom_manifest_path,
            paths=paths,
        )
        for index, item in enumerate(candidates, start=1)
        if isinstance(item, dict)
    ]
    refinement_attempted = False
    if _polygon_refinement_needed(normalized):
        refinement_attempted = True
        refined = _refine_polygon_candidates(
            paths,
            job_id,
            normalized,
            images=images,
            evidence_meta=evidence_meta,
            pdf_names=[str(name) for name in pdf_names if name],
            microzoom_manifest=microzoom_manifest,
            microzoom_manifest_path=microzoom_manifest_path,
        )
        if refined:
            normalized = _merge_refined_polygon_candidates(normalized, refined)
    if not result.ok and not normalized:
        error = result.error or "Codex candidate extraction failed."
        _write_visual_extraction_failure(paths, job_id, error, status=extraction_status)
        return {"ok": True, "count": 0, "status": extraction_status, "extraction_status": "visual_extraction_failed", "error": error}
    normalized = annotate_candidate_qualities(
        normalized,
        paths,
        job_id,
        extraction_status=extraction_status,
        refinement_attempted=refinement_attempted,
    )
    codex_run = _codex_record_payload(result)
    atomic_write_json(
        output_dir / "codex_candidates.json",
        {
            "schema_version": 1,
            "job_id": job_id,
            "candidates": normalized,
            "codex_run": codex_run,
            "extraction_status": extraction_status,
            "refinement_attempted": refinement_attempted,
        },
    )
    response: dict[str, Any] = {"ok": True, "count": len(normalized), "extraction_status": extraction_status}
    if not result.ok:
        response["error"] = result.error or "Codex candidate extraction failed."
        response["status"] = extraction_status
    return response


def _render_candidate_pages(
    paths: RuntimePaths,
    job_id: str,
    pdf_names: list[str],
    *,
    excluded_pages: set[int] | None = None,
) -> tuple[list[Path], list[dict[str, Any]]]:
    try:
        import fitz  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError("PyMuPDF is required for Codex PDF page rendering. Install the runtime dependencies and retry.") from exc
    run_id = f"{job_id}-{uuid.uuid4().hex[:8]}"
    run_dir = paths.suite_root / ".state" / "codex-runs" / run_id
    pages_dir = run_dir / "pages"
    microzoom_dir = run_dir / "microzoom"
    pages_dir.mkdir(parents=True, exist_ok=True)
    _excluded = excluded_pages or set()
    images: list[Path] = []
    full_page_images: list[dict[str, Any]] = []
    evidence_images: list[dict[str, Any]] = []
    for pdf_name in pdf_names:
        pdf_path = paths.jobs_import_root / job_id / _safe_pdf_name(pdf_name)
        if not pdf_path.exists():
            continue
        doc = None
        try:
            doc = fitz.open(pdf_path)
            for page_index, page in enumerate(doc, start=1):
                if page_index in _excluded:
                    continue
                if len(images) >= VISUAL_CANDIDATE_MAX_PAGES:
                    raise RuntimeError(
                        f"Visual analysis render limit exceeded ({VISUAL_CANDIDATE_MAX_PAGES} pages). "
                        "Split the PDF or approve a bounded page range before production."
                    )
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                target = pages_dir / f"{pdf_path.stem}-p{page_index}.png"
                pix.save(target)
                images.append(target)
                full_page_images.append(
                    {
                        "source_pdf": pdf_name,
                        "source_page": page_index,
                        "role": "full_page",
                        "path": str(target),
                        "width_px": int(getattr(pix, "width", 0) or 0),
                        "height_px": int(getattr(pix, "height", 0) or 0),
                    }
                )
                try:
                    evidence_images.extend(
                        render_microzoom_images(
                            page,
                            fitz,
                            microzoom_dir,
                            source_pdf=pdf_name,
                            source_page=page_index,
                        )
                    )
                except Exception as exc:
                    log.warning("microzoom_render_failed", pdf=str(pdf_path), page=page_index, error=str(exc))
        except RuntimeError:
            raise
        except Exception as exc:
            log.warning("candidate_page_render_failed", pdf=str(pdf_path), error=str(exc))
            continue
        finally:
            close = getattr(doc, "close", None)
            if callable(close):
                close()
    if images:
        write_microzoom_manifest(
            run_dir,
            job_id=job_id,
            full_page_images=full_page_images,
            evidence_images=evidence_images,
        )
    return images, evidence_images


def _load_visual_skill_context(paths: RuntimePaths) -> str:
    agents_root = paths.suite_root / "agents"
    parts: list[str] = []
    for rel in (
        "autocad-uzman-1/AGENT.md",
        "_shared/skills/GORSEL_ANALIZ_PROTOKOLU.md",
        "_shared/skills/MIKRO_ZOOM_PROTOKOLU.md",
        "_shared/skills/PDF_POZ_OKUMA.md",
        "_shared/skills/PLAKA_GEOMETRI_CIKARMA.md",
    ):
        p = agents_root / rel
        if p.exists():
            parts.append(f"# {rel}\n{p.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def _candidate_prompt(
    job_id: str,
    paths: RuntimePaths | None = None,
    *,
    pdf_import_paths: list[str] | None = None,
    manifest_path: str | None = None,
) -> str:
    skill_context = _load_visual_skill_context(paths) if paths is not None else ""
    header = f"{skill_context}\n\n---\n" if skill_context.strip() else ""
    source_block = ""
    if pdf_import_paths:
        source_block += f"Kaynak PDF dosyalari: {', '.join(pdf_import_paths)}\n"
    if manifest_path:
        source_block += f"Mikro-zoom manifest: {manifest_path}\n"
    if source_block:
        source_block += "\n"
    return (
        f"{header}"
        f"{source_block}"
        "Bu teknik ofis PDF sayfalarindan plaka adaylarini oku. Yalnizca JSON dondur.\n"
        "Shell komutu calistirma, dosya arama yapma, ek dosya okuma denemesi yapma; yalnizca attached render ve mikro-zoom kanitlarini kullan.\n"
        "Schema: {\"candidates\":[{\"source_pdf\":\"...pdf\",\"source_page\":1,\"poz_no\":\"1001\",\"width\":200,\"height\":100,\"thickness\":10,\"material\":\"S355\",\"quantity\":1,\"holes\":[{\"x\":50,\"y\":25,\"diameter\":18}],\"slots\":[],\"corner_reliefs\":[{\"corner\":\"bottom_left\",\"radius\":10,\"relief_type\":\"chamfer\",\"x_offset\":10,\"y_offset\":10}],\"polygon_vertices\":null,\"contour_type\":\"rectangle|polygon|chamfered\",\"confidence\":0.45,\"analysis_confidence\":0.45,\"uncertainties\":[],\"source_trace\":{\"source_pdf\":\"...pdf\",\"source_page\":1,\"method\":\"codex_cli\",\"microzoom_manifest_path\":null,\"evidence_images\":[]},\"microzoom_manifest_path\":null,\"evidence_images\":[],\"evidence\":\"kisa kanit\"}]}\n"
        "Her render edilen sayfayi tek tek incele; yalnizca ilk sayfalari okuyup durma. Poz numarasi sayfa numarasi degildir.\n"
        "Mikro-zoom kanitini kaynak izlemede kullan; her aday icin source_trace, analysis_confidence, uncertainties, microzoom_manifest_path ve evidence_images alanlarini doldur.\n"
        "Delik koordinatini olcu cizgisinden cikarsiyorsan uncertainties listesine 'inferred: delik koordinati dogrudan olculemedi, ...' yaz.\n"
        "Olcu zinciri toplami (ornek: 85+29.5+42.5=157) ile baslik blogundaki toplam boyut (ornek: 156.5) uyusmuyorsa uncertainties listesine dimension_chain uyarisi ekle.\n"
        "Plaka dis konturu poligon ise `contour_type='polygon'` yaz ve `polygon_vertices` icine dis konturun tum kose koordinatlarini CCW siraya (0,0)=sol alt referansiyla mm cinsinden gir. Koordinatlar net okunamiyorsa `polygon_vertices=null`, dusuk confidence ve acik uncertainty yaz.\n"
        "`contour_type='polygon'` olan aday uretilebilir sayilmak icin `polygon_vertices` zorunludur; belirsiz vertex listesini tahmin ederek doldurma.\n"
        "Plaka dis konturu dikdortgen degilse bunu `contour_type` alaninda belirt. Duz pah/chamfer varsa `corner_reliefs` icine ilgili koseleri `relief_type=chamfer` ve gorulen pah offseti mm olarak `radius` ile yaz. Yuvarlak/cugul koselerde `round` veya `cugul` kullan.\n"
        "Cizimde 30 mm, 10 mm gibi yan/kenar offsetleri poligon veya pah olusturuyorsa aday bos `corner_reliefs` ile onaylanabilir gorunmemeli; emin degilsen dusuk confidence ve acik evidence yaz.\n"
        "Emin olmadigin olcu veya deligi uydurma; eksikse alani bos birak veya aday verme. Tum adaylar mudur onayi gerektirir.\n"
        f"Job: {job_id}"
    )


def _polygon_refinement_prompt(job_id: str, candidates: list[dict[str, Any]], manifest_path: str | None) -> str:
    candidate_lines = []
    for candidate in candidates:
        if not _candidate_needs_polygon_refinement(candidate):
            continue
        candidate_lines.append(
            json.dumps(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "source_pdf": candidate.get("source_pdf"),
                    "source_page": candidate.get("source_page"),
                    "poz_no": candidate.get("poz_no"),
                    "width": candidate.get("width"),
                    "height": candidate.get("height"),
                    "holes": candidate.get("holes") or [],
                    "corner_reliefs": candidate.get("corner_reliefs") or [],
                    "polygon_vertices": candidate.get("polygon_vertices"),
                    "uncertainties": candidate.get("uncertainties") or [],
                },
                ensure_ascii=False,
            )
        )
    return (
        "Bu ek analiz yalnizca poligon dis kontur ve delik merkezlerini netlestirmek icindir. "
        "Shell komutu calistirma, dosya arama yapma, ek dosya okuma denemesi yapma; yalnizca attached image ve mikro-zoom kanitlarini kullan. "
        "Yalnizca JSON dondur.\n"
        f"Mikro-zoom manifest: {manifest_path or 'yok'}\n"
        "Poligon icin uretilebilir sonuc ancak tum dis kontur koseleri CCW sirada `polygon_vertices` olarak verilebiliyorsa gecerlidir. "
        "Koordinat net degilse `polygon_vertices=null`, dusuk confidence ve acik uncertainty yaz; uydurma.\n"
        "Var olan adaylar:\n"
        + "\n".join(candidate_lines)
        + "\n"
        + "Schema: {\"candidates\":[{\"source_pdf\":\"...pdf\",\"source_page\":1,\"poz_no\":\"1001\",\"width\":200,\"height\":100,\"thickness\":10,\"material\":\"S355\",\"quantity\":1,\"holes\":[{\"x\":50,\"y\":25,\"diameter\":18}],\"slots\":[],\"corner_reliefs\":[{\"corner\":\"bottom_left\",\"radius\":10,\"relief_type\":\"chamfer\",\"x_offset\":10,\"y_offset\":10}],\"polygon_vertices\":null,\"contour_type\":\"polygon\",\"confidence\":0.45,\"analysis_confidence\":0.45,\"uncertainties\":[],\"source_trace\":{\"source_pdf\":\"...pdf\",\"source_page\":1,\"method\":\"codex_cli_polygon_refinement\",\"microzoom_manifest_path\":null,\"evidence_images\":[]},\"microzoom_manifest_path\":null,\"evidence_images\":[],\"evidence\":\"kisa kanit\"}]}\n"
        f"Job: {job_id}"
    )


def _candidate_schema_path(paths: RuntimePaths) -> Path:
    path = paths.suite_root / ".state" / "codex-runs" / "plate-candidates.v4.schema.json"
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
            "x_offset": number_or_null,
            "y_offset": number_or_null,
        },
        "required": ["corner", "radius", "relief_type", "x_offset", "y_offset"],
        "additionalProperties": False,
    }
    polygon_vertex_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
        },
        "required": ["x", "y"],
        "additionalProperties": False,
    }
    source_trace_schema = {
        "type": "object",
        "properties": {
            "source_pdf": {"type": "string"},
            "source_page": integer_or_null,
            "method": string_or_null,
            "microzoom_manifest_path": string_or_null,
            "evidence_images": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["source_pdf", "source_page", "method", "microzoom_manifest_path", "evidence_images"],
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
            "polygon_vertices": {"type": ["array", "null"], "items": polygon_vertex_schema},
            "contour_type": string_or_null,
            "confidence": {"type": "number"},
            "analysis_confidence": {"type": "number"},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
            "source_trace": source_trace_schema,
            "microzoom_manifest_path": string_or_null,
            "evidence_images": {"type": "array", "items": {"type": "string"}},
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
            "polygon_vertices",
            "contour_type",
            "confidence",
            "analysis_confidence",
            "uncertainties",
            "source_trace",
            "microzoom_manifest_path",
            "evidence_images",
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


def _refine_polygon_candidates(
    paths: RuntimePaths,
    job_id: str,
    candidates: list[dict[str, Any]],
    *,
    images: list[Path],
    evidence_meta: list[dict[str, Any]],
    pdf_names: list[str],
    microzoom_manifest: dict[str, Any] | None,
    microzoom_manifest_path: Path | None,
) -> list[dict[str, Any]]:
    prompt = _polygon_refinement_prompt(
        job_id,
        candidates,
        _path_text_for_candidate(paths, microzoom_manifest_path) if microzoom_manifest_path else None,
    )
    evidence_paths = [Path(e["path"]) for e in evidence_meta if e.get("path")]
    result = _app_codex_bridge().run(
        CodexRunRequest(
            prompt=prompt,
            agent_id="pdf-visual-candidate",
            sandbox="read-only",
            timeout_seconds=120,
            images=images + evidence_paths,
            output_schema=_candidate_schema_path(paths),
        ),
        job_id=job_id,
        on_event=lambda event: _append_job_event(paths, job_id, "delta", {"source": "codex_cli_polygon_refinement", "event": event}),
    )
    if not result.ok:
        _append_job_event(paths, job_id, "polygon_refinement_failed", {"error": result.error or "Codex polygon refinement failed."})
        return []
    try:
        parsed = json.loads(result.content)
    except json.JSONDecodeError as exc:
        _append_job_event(paths, job_id, "polygon_refinement_failed", {"error": f"Codex polygon refinement JSON parse failed: {exc.msg}"})
        return []
    raw = parsed.get("candidates") if isinstance(parsed, dict) else None
    if not isinstance(raw, list):
        return []
    return [
        _normalize_candidate(
            item,
            index,
            provider="codex_cli_polygon_refinement",
            allowed_pdf_names=pdf_names,
            visual_manifest=microzoom_manifest,
            microzoom_manifest_path=microzoom_manifest_path,
            paths=paths,
        )
        for index, item in enumerate(raw, start=1)
        if isinstance(item, dict)
    ]


def _merge_refined_polygon_candidates(
    original: list[dict[str, Any]],
    refined: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refined_by_key = {
        _candidate_merge_key(candidate): candidate
        for candidate in refined
        if _candidate_has_polygon_vertices(candidate)
    }
    merged: list[dict[str, Any]] = []
    for candidate in original:
        replacement = refined_by_key.get(_candidate_merge_key(candidate))
        if replacement is None:
            merged.append(candidate)
            continue
        updated = dict(candidate)
        for key in (
            "holes",
            "slots",
            "corner_reliefs",
            "polygon_vertices",
            "contour_type",
            "confidence",
            "analysis_confidence",
            "uncertainties",
            "source_trace",
            "microzoom_manifest_path",
            "evidence_images",
            "evidence",
            "coordinate_inferred",
        ):
            if key in replacement:
                updated[key] = replacement[key]
        updated["refinement_source"] = replacement.get("provider") or "codex_cli_polygon_refinement"
        merged.append(updated)
    return merged


def _polygon_refinement_needed(candidates: list[dict[str, Any]]) -> bool:
    return any(_candidate_needs_polygon_refinement(candidate) for candidate in candidates)


def _candidate_needs_polygon_refinement(candidate: dict[str, Any]) -> bool:
    contour_type = str(candidate.get("contour_type") or "").lower()
    if "polygon" not in contour_type:
        return False
    if not _candidate_has_polygon_vertices(candidate):
        return True
    confidence = _optional_float(candidate.get("analysis_confidence")) or _optional_float(candidate.get("confidence")) or 0.0
    if confidence < 0.6:
        return True
    uncertainties = candidate.get("uncertainties")
    if isinstance(uncertainties, list) and any(_uncertainty_is_geometry_blocker(item) for item in uncertainties):
        return True
    return False


def _candidate_has_polygon_vertices(candidate: dict[str, Any]) -> bool:
    vertices = candidate.get("polygon_vertices")
    return isinstance(vertices, list) and len(vertices) >= 3 and all(isinstance(item, dict) and item.get("x") is not None and item.get("y") is not None for item in vertices)


def _uncertainty_is_geometry_blocker(value: Any) -> bool:
    text = str(value or "").lower()
    return any(term in text for term in ("ambiguous", "belirsiz", "uncertain", "net degil", "verify", "onay"))


def _candidate_merge_key(candidate: dict[str, Any]) -> tuple[str, int, str]:
    try:
        page = int(candidate.get("source_page") or 0)
    except (TypeError, ValueError):
        page = 0
    return (str(candidate.get("source_pdf") or ""), page, str(candidate.get("poz_no") or ""))


def _is_codex_timeout(error: Any) -> bool:
    return "timed out" in str(error or "").lower() or "timeout" in str(error or "").lower()


def _codex_record_payload(result: Any) -> dict[str, Any]:
    record = getattr(result, "record", None)
    to_dict = getattr(record, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        return value if isinstance(value, dict) else {}
    return {}


def _path_text_for_candidate(paths: RuntimePaths | None, value: Any) -> str | None:
    if value in (None, ""):
        return None
    path = Path(str(value))
    if paths is None:
        return str(path)
    try:
        return str(path.resolve().relative_to(paths.suite_root.resolve())).replace("\\", "/")
    except Exception:
        return str(path)


def _paths_text_for_candidate(paths: RuntimePaths | None, values: list[Any]) -> list[str]:
    return [text for value in values if (text := _path_text_for_candidate(paths, value))]


def _write_visual_extraction_failure(
    paths: RuntimePaths,
    job_id: str,
    error: str,
    *,
    status: str,
) -> None:
    output_dir = paths.jobs_output_root / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        output_dir / "codex_candidates.json",
        {
            "schema_version": 1,
            "job_id": job_id,
            "candidates": [],
            "extraction_status": "visual_extraction_failed",
            "failure_status": status,
            "error": error,
        },
    )
    review = {
        "reason": "visual_extraction_failed",
        "page": None,
        "poz_no": None,
        "detail": error,
        "next_action": "Poligon/delik geometrisi netlestirilmeden DXF/NC1 uretme; gorsel analiz yeniden denensin veya mudur olcu girsin.",
        "approval_required": True,
    }
    existing = _read_json(output_dir / "manual_review_required.json")
    reviews = existing if isinstance(existing, list) else []
    if not any(isinstance(item, dict) and item.get("reason") == "visual_extraction_failed" for item in reviews):
        reviews.append(review)
        atomic_write_json(output_dir / "manual_review_required.json", reviews)
    summary = _read_json(output_dir / "job_summary.json")
    if isinstance(summary, dict):
        manual_reviews = summary.get("manual_reviews") if isinstance(summary.get("manual_reviews"), list) else []
        if not any(isinstance(item, dict) and item.get("reason") == "visual_extraction_failed" for item in manual_reviews):
            summary["manual_reviews"] = manual_reviews + [review]
            summary["ok"] = False
            atomic_write_json(output_dir / "job_summary.json", summary)


def _normalize_candidate(
    item: dict[str, Any],
    index: int,
    *,
    provider: str,
    allowed_pdf_names: list[str] | None = None,
    visual_manifest: dict[str, Any] | None = None,
    microzoom_manifest_path: Path | None = None,
    paths: RuntimePaths | None = None,
) -> dict[str, Any]:
    source_pdf = str(item.get("source_pdf") or "").strip()
    allowed = [name for name in (allowed_pdf_names or []) if name]
    if source_pdf not in set(allowed) and len(allowed) == 1:
        source_pdf = allowed[0]
    source_page = item.get("source_page")
    manifest_text = _path_text_for_candidate(paths, microzoom_manifest_path) if microzoom_manifest_path else _path_text_for_candidate(paths, item.get("microzoom_manifest_path"))
    generated_evidence = evidence_for_page(visual_manifest, source_pdf=source_pdf, source_page=source_page)
    # Runtime-rendered evidence is authoritative. Model-provided image paths can
    # be stale or hallucinated, so only use them when no manifest evidence exists.
    raw_evidence_images = generated_evidence or (item.get("evidence_images") if isinstance(item.get("evidence_images"), list) else [])
    evidence_images = _paths_text_for_candidate(paths, raw_evidence_images)
    _coord_inferred_keywords = (
        "inferred", "cikarim", "assumed", "varsayilan", "ambiguous",
        "belirsiz", "diagonal", "direction", "yon",
    )
    _uncertainties_list = item.get("uncertainties") or []
    coordinate_inferred = any(
        isinstance(u, str) and any(kw in u.lower() for kw in _coord_inferred_keywords)
        for u in _uncertainties_list
    )
    source_trace = {
        "source_pdf": source_pdf or str(item.get("source_pdf") or ""),
        "source_page": source_page,
        "method": provider,
        "microzoom_manifest_path": manifest_text,
        "evidence_images": evidence_images,
    }
    if isinstance(item.get("source_trace"), dict):
        incoming_trace = item["source_trace"]
        if incoming_trace.get("method") not in (None, ""):
            source_trace["method"] = provider
    return {
        "candidate_id": str(item.get("candidate_id") or f"{provider}-{index}"),
        "provider": provider,
        "source_pdf": source_pdf or item.get("source_pdf"),
        "source_page": source_page,
        "poz_no": item.get("poz_no"),
        "width": item.get("width"),
        "height": item.get("height"),
        "thickness": item.get("thickness"),
        "material": item.get("material") or "UNKNOWN",
        "quantity": item.get("quantity") or 1,
        "holes": item.get("holes") if isinstance(item.get("holes"), list) else [],
        "slots": item.get("slots") if isinstance(item.get("slots"), list) else [],
        "corner_reliefs": item.get("corner_reliefs") if isinstance(item.get("corner_reliefs"), list) else [],
        "polygon_vertices": item.get("polygon_vertices") if isinstance(item.get("polygon_vertices"), list) else None,
        "contour_type": item.get("contour_type"),
        "confidence": item.get("confidence") or 0.0,
        "analysis_confidence": item.get("analysis_confidence") or item.get("confidence") or 0.0,
        "uncertainties": item.get("uncertainties") if isinstance(item.get("uncertainties"), list) else [],
        "source_trace": source_trace,
        "microzoom_manifest_path": manifest_text,
        "evidence_images": evidence_images,
        "evidence": item.get("evidence") or item.get("reason"),
        "coordinate_inferred": coordinate_inferred,
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
    return validate_approved_rows(rows, paths, job_id)


def _legacy_validate_approved_rows(rows: list[dict[str, Any]], paths: RuntimePaths, job_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
            visual_evidence_error = _visual_candidate_evidence_error(row, paths, source_pdf=source_pdf)
            if visual_evidence_error:
                raise ValueError(visual_evidence_error)
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
                corner_reliefs=[
                    CornerReliefSpec(
                        corner=_required_text(item, "corner"),
                        radius=_required_float(item, "radius"),
                        relief_type=normalize_relief_type(str(item.get("relief_type") or item.get("type") or "round")),
                        x_offset=_optional_float(item.get("x_offset")),
                        y_offset=_optional_float(item.get("y_offset")),
                    )
                    for item in row.get("corner_reliefs", [])
                    if isinstance(item, dict)
                    and normalize_relief_type(str(item.get("relief_type") or item.get("type") or "")) != "polygon_contour"
                    and item.get("corner")  # polygon_contour sentinel'inin corner alanı yoktur
                ],
                polygon_vertices=row.get("polygon_vertices") or None,
                source_page=int(row["source_page"]) if row.get("source_page") not in (None, "") else None,
                confidence=_optional_float(row.get("confidence")) or 1.0,
                notes=["manager_approved_from_visual_candidate"],
            )
            validation = spec.validate()
            if validation:
                raise ValueError("; ".join(validation))
            data = spec.to_dict()
            data["source_pdf"] = source_pdf
            if _row_requires_visual_evidence(row):
                for key in (
                    "source_trace",
                    "analysis_confidence",
                    "uncertainties",
                    "microzoom_manifest_path",
                    "evidence_images",
                    "provider",
                    "approval_required",
                ):
                    if key in row:
                        data[key] = row[key]
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


def _visual_candidate_evidence_error(row: dict[str, Any], paths: RuntimePaths, *, source_pdf: str) -> str | None:
    if not _row_requires_visual_evidence(row):
        return None
    source_trace = row.get("source_trace")
    if not isinstance(source_trace, dict):
        return "visual candidate requires source_trace before manager approval"
    if not isinstance(row.get("uncertainties"), list):
        return "visual candidate requires uncertainties before manager approval"
    if row.get("analysis_confidence") in (None, ""):
        return "visual candidate requires analysis_confidence before manager approval"
    manifest_value = row.get("microzoom_manifest_path") or source_trace.get("microzoom_manifest_path")
    if not manifest_value:
        return "visual candidate requires microzoom_manifest_path before manager approval"
    manifest_path = _resolve_suite_path(paths, manifest_value)
    manifest = load_microzoom_manifest(manifest_path)
    evidence_values = row.get("evidence_images")
    if not isinstance(evidence_values, list):
        evidence_values = source_trace.get("evidence_images") if isinstance(source_trace.get("evidence_images"), list) else []
    resolved_evidence = [str(_resolve_suite_path(paths, value)) for value in evidence_values if value]
    ok, error = microzoom_manifest_is_valid_for_candidate(
        manifest,
        source_pdf=source_pdf,
        source_page=row.get("source_page"),
        evidence_images=resolved_evidence,
    )
    if not ok:
        return error or "visual candidate microzoom evidence is invalid"
    return None


def _row_requires_visual_evidence(row: dict[str, Any]) -> bool:
    provider = str(row.get("provider") or "")
    return row.get("approval_required") is True or provider in {"codex_cli", "manager_pdf_scan", "pdf-visual-candidate"}


def _resolve_suite_path(paths: RuntimePaths, value: Any) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return paths.suite_root / path


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
                history=history,
            )
        except Exception as exc:
            log.warning("manager_memory_record_failed", session_id=session_id, error=str(exc))


def _save_assistant_turn(
    paths: RuntimePaths,
    session_id: str,
    history: list[dict[str, Any]],
    content: str,
) -> None:
    save_session(
        paths.sessions_root,
        session_id,
        history + [{"role": "assistant", "content": content, "timestamp": _now_ms()}],
    )


def _append_job_event(paths: RuntimePaths, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
    stage_events = {
        "production_started",
        "qc_completed",
        "partlist_started",
        "partlist_completed",
        "retrospective_written",
        "manager_notification",
    }
    if event_type not in JOB_STATUS_VALUES and event_type not in {"started", "delta", "candidate", "failed", "completed", "codex_extracting"} | stage_events:
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
    if "/" in requested:
        return roots[0] / requested
    basename = Path(requested).name
    matches: list[Path] = []
    for root in roots:
        if root.exists():
            for candidate in root.rglob(basename):
                if candidate.is_file():
                    matches.append(candidate)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail="ambiguous file path; use the relative path")
    return roots[0] / requested


def _relative_job_file_path(paths: RuntimePaths, job_id: str, path: Path) -> str:
    resolved = path.resolve()
    roots = [paths.jobs_import_root / job_id, paths.jobs_output_root / job_id]
    for root in roots:
        try:
            return resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail="file is outside job roots")


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
    except Exception as exc:
        log.warning("json_read_failed", path=str(path), error=str(exc))
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


def _upload_limit_bytes(env_name: str, default_mb: int) -> int:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default_mb * 1024 * 1024
    try:
        mb = float(raw)
    except ValueError:
        log.warning("invalid_upload_limit_env", env_name=env_name, value=raw)
        return default_mb * 1024 * 1024
    return max(1, int(mb * 1024 * 1024))


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
