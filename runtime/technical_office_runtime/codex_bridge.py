from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import RuntimePaths, get_paths


DEFAULT_CODEX_TIMEOUT_SECONDS = 90
CodexEventCallback = Callable[[dict[str, Any]], None]


class CommandRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        *,
        input: str,
        cwd: str,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        ...


@dataclass(frozen=True)
class CodexRunRequest:
    prompt: str
    agent_id: str
    sandbox: str = "read-only"
    timeout_seconds: int = DEFAULT_CODEX_TIMEOUT_SECONDS
    images: list[Path] = field(default_factory=list)
    output_schema: Path | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class CodexRunRecord:
    run_id: str
    job_id: str | None
    agent_id: str
    sandbox: str
    started_at: str
    ended_at: str | None
    exit_code: int | None
    events_path: str
    final_json_path: str | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CodexRunResult:
    ok: bool
    content: str
    events: list[dict[str, Any]]
    record: CodexRunRecord
    error: str | None = None


@dataclass(frozen=True)
class CodexDoctorStatus:
    executable: str | None
    login_ok: bool
    exec_help_ok: bool
    login_output: str
    exec_help_output: str
    error: str | None = None


@dataclass(frozen=True)
class CodexMcpStatus:
    list_ok: bool
    get_ok: bool
    configured: bool
    points_to_suite: bool
    stale_path_detected: bool
    mcp_list_output: str
    mcp_get_output: str
    expected_server_path: str
    fix_commands: list[str]
    error: str | None = None


class CodexBridge:
    def __init__(
        self,
        paths: RuntimePaths | None = None,
        *,
        executable: str | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.paths = paths or get_paths()
        self.executable = executable or resolve_codex_executable()
        self.runner = runner

    def run(
        self,
        request: CodexRunRequest,
        *,
        job_id: str | None = None,
        on_event: CodexEventCallback | None = None,
    ) -> CodexRunResult:
        run_id = request.run_id or f"codex-{uuid.uuid4().hex}"
        run_dir = self.paths.suite_root / ".state" / "codex-runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        events_path = run_dir / "events.jsonl"
        final_path = run_dir / "last-message.txt"
        started_at = _now_iso()
        events: list[dict[str, Any]] = []
        error: str | None = None
        exit_code: int | None = None

        if not self.executable:
            error = "codex.cmd executable not found on PATH."
            record = _record(
                run_id=run_id,
                job_id=job_id,
                agent_id=request.agent_id,
                sandbox=request.sandbox,
                started_at=started_at,
                ended_at=_now_iso(),
                exit_code=None,
                events_path=events_path,
                final_path=final_path,
                error=error,
            )
            _write_record(run_dir, record)
            _write_jsonl(events_path, [_normalized_event("failed", {"error": error})])
            return CodexRunResult(ok=False, content="", events=[], record=record, error=error)

        args = self._build_args(request, final_path)
        try:
            if self.runner is not None:
                completed = self.runner(
                    args,
                    input=request.prompt,
                    cwd=str(self.paths.suite_root),
                    timeout=max(1, int(request.timeout_seconds)),
                )
                exit_code = completed.returncode
                events, parse_error = _parse_jsonl(completed.stdout)
                for event in events:
                    _safe_emit(on_event, event)
                if parse_error:
                    error = parse_error
                event_error = _summarize_codex_event_error(events)
                if event_error and not error:
                    error = event_error
                if completed.returncode != 0 and not error:
                    error = _summarize_codex_error(completed.stderr or completed.stdout or "Codex CLI failed.")
                if completed.stderr:
                    (run_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
                _write_text(events_path, completed.stdout)
            else:
                stream_result = _run_subprocess_streaming(
                    args,
                    input=request.prompt,
                    cwd=str(self.paths.suite_root),
                    timeout=max(1, int(request.timeout_seconds)),
                    events_path=events_path,
                    on_event=on_event,
                )
                exit_code = stream_result.returncode
                events = stream_result.events
                if stream_result.parse_error:
                    error = stream_result.parse_error
                event_error = _summarize_codex_event_error(events)
                if event_error and not error:
                    error = event_error
                if stream_result.returncode != 0 and not error:
                    error = _summarize_codex_error(stream_result.stderr or "Codex CLI failed.")
                if stream_result.stderr:
                    (run_dir / "stderr.log").write_text(stream_result.stderr, encoding="utf-8")
        except subprocess.TimeoutExpired as exc:
            exit_code = None
            error = f"Codex CLI timed out after {request.timeout_seconds} seconds."
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
            events, _parse_error = _parse_jsonl(stdout)
            for event in events:
                _safe_emit(on_event, event)
            _write_text(events_path, stdout)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            _write_jsonl(events_path, [_normalized_event("failed", {"error": error})])

        content = final_path.read_text(encoding="utf-8").strip() if final_path.exists() else _last_event_text(events)
        content = _repair_mojibake(content)
        record = _record(
            run_id=run_id,
            job_id=job_id,
            agent_id=request.agent_id,
            sandbox=request.sandbox,
            started_at=started_at,
            ended_at=_now_iso(),
            exit_code=exit_code,
            events_path=events_path,
            final_path=final_path,
            error=error,
        )
        _write_record(run_dir, record)
        return CodexRunResult(
            ok=error is None,
            content=content,
            events=events,
            record=record,
            error=error,
        )

    def _build_args(self, request: CodexRunRequest, final_path: Path) -> list[str]:
        args = [
            self.executable or "codex.cmd",
            "exec",
            "--json",
            "--cd",
            str(self.paths.suite_root),
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            request.sandbox,
            "--output-last-message",
            str(final_path),
        ]
        if request.output_schema is not None:
            args.extend(["--output-schema", str(request.output_schema)])
        for image in request.images:
            args.extend(["--image", str(image)])
        args.append("-")
        return args


def resolve_codex_executable() -> str | None:
    return shutil.which("codex.cmd") or shutil.which("codex")


def inspect_codex(timeout: int = 10) -> CodexDoctorStatus:
    executable = resolve_codex_executable()
    if not executable:
        return CodexDoctorStatus(
            executable=None,
            login_ok=False,
            exec_help_ok=False,
            login_output="",
            exec_help_output="",
            error="codex.cmd not found on PATH.",
        )
    login = _doctor_run([executable, "login", "status"], timeout=timeout)
    help_result = _doctor_run([executable, "exec", "--help"], timeout=timeout)
    login_output = "\n".join(part for part in (login.stdout, login.stderr) if part).strip()
    exec_help_output = "\n".join(part for part in (help_result.stdout, help_result.stderr) if part).strip()
    return CodexDoctorStatus(
        executable=executable,
        login_ok=_looks_logged_in(login.returncode, login_output),
        exec_help_ok=help_result.returncode == 0 or "usage: codex exec" in exec_help_output.lower(),
        login_output=login_output,
        exec_help_output=exec_help_output,
        error=None,
    )


def inspect_codex_mcp(paths: RuntimePaths | None = None, timeout: int = 10) -> CodexMcpStatus:
    resolved_paths = paths or get_paths()
    executable = resolve_codex_executable()
    expected_server = resolved_paths.suite_root / "mcp" / "autocad-mcp-server"
    expected_src = expected_server / "src"
    fix_commands = _codex_mcp_fix_commands(executable or "codex.cmd", expected_server, expected_src)
    if not executable:
        return CodexMcpStatus(
            list_ok=False,
            get_ok=False,
            configured=False,
            points_to_suite=False,
            stale_path_detected=False,
            mcp_list_output="",
            mcp_get_output="",
            expected_server_path=str(expected_server),
            fix_commands=fix_commands,
            error="codex.cmd not found on PATH.",
        )
    list_result = _doctor_run([executable, "mcp", "list"], timeout=timeout)
    get_result = _doctor_run([executable, "mcp", "get", "autocad-mcp"], timeout=timeout)
    list_output = "\n".join(part for part in (list_result.stdout, list_result.stderr) if part).strip()
    get_output = "\n".join(part for part in (get_result.stdout, get_result.stderr) if part).strip()
    config_text = _read_codex_global_config()
    combined = f"{list_output}\n{get_output}\n{config_text}"
    normalized = _normalize_path_text(combined)
    expected_norm = _normalize_path_text(str(expected_server))
    configured = "autocad-mcp" in combined.lower()
    points_to_suite = configured and expected_norm in normalized
    stale_path_detected = configured and "technical_office_engineer" in normalized and not points_to_suite
    config_error = None
    if not config_text and (list_result.returncode != 0 or "failed to load configuration" in combined.lower()):
        config_error = combined or "codex mcp list failed."
    return CodexMcpStatus(
        list_ok=list_result.returncode == 0,
        get_ok=get_result.returncode == 0,
        configured=configured,
        points_to_suite=points_to_suite,
        stale_path_detected=stale_path_detected,
        mcp_list_output=list_output,
        mcp_get_output=get_output,
        expected_server_path=str(expected_server),
        fix_commands=fix_commands,
        error=config_error,
    )


@dataclass(frozen=True)
class _StreamingProcessResult:
    returncode: int
    stderr: str
    events: list[dict[str, Any]]
    parse_error: str | None = None


def _run_subprocess_streaming(
    args: list[str],
    *,
    input: str,
    cwd: str,
    timeout: int,
    events_path: Path,
    on_event: CodexEventCallback | None,
) -> _StreamingProcessResult:
    command, use_shell = _command_invocation(args)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=use_shell,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        process.stdin.write(input)
        process.stdin.close()
    except BrokenPipeError:
        pass
    started = time.monotonic()
    events: list[dict[str, Any]] = []
    parse_error: str | None = None
    stderr_parts: list[str] = []
    stdout_parts: list[str] = []
    stream_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
    stdout_done = False
    stderr_done = False

    def read_stream(name: str, stream: Any) -> None:
        try:
            for item in stream:
                stream_queue.put((name, item))
        finally:
            stream_queue.put((name, None))

    threading.Thread(target=read_stream, args=("stdout", process.stdout), daemon=True).start()
    threading.Thread(target=read_stream, args=("stderr", process.stderr), daemon=True).start()

    with events_path.open("w", encoding="utf-8") as handle:
        while True:
            if time.monotonic() - started > timeout and process.poll() is None:
                process.kill()
                process.wait(timeout=2)
                raise subprocess.TimeoutExpired(args, timeout, output="".join(stdout_parts), stderr="".join(stderr_parts))
            try:
                name, line = stream_queue.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None and stdout_done and stderr_done:
                    break
                continue
            if line is None:
                if name == "stdout":
                    stdout_done = True
                else:
                    stderr_done = True
                if process.poll() is not None and stdout_done and stderr_done:
                    break
                continue
            if name == "stderr":
                stderr_parts.append(line)
                continue
            stdout_parts.append(line)
            handle.write(line)
            handle.flush()
            event, line_error = _parse_jsonl_line(line, len(events) + 1)
            if line_error and parse_error is None:
                parse_error = line_error
            if event is not None:
                events.append(event)
                _safe_emit(on_event, event)
    returncode = process.wait(timeout=2)
    stderr = "".join(stderr_parts)
    return _StreamingProcessResult(returncode=returncode, stderr=stderr, events=events, parse_error=parse_error)


def _doctor_run(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        command, use_shell = _command_invocation(args)
        return subprocess.run(
            command,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=use_shell,
        )
    except Exception as exc:
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr=f"{type(exc).__name__}: {exc}")


def _command_invocation(args: list[str]) -> tuple[list[str] | str, bool]:
    return args, False


def _looks_logged_in(returncode: int, output: str) -> bool:
    lower = output.lower()
    if "not logged" in lower or "login required" in lower:
        return False
    return returncode == 0 or "logged in" in lower or "could not update path" in lower


def _summarize_codex_error(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return "Codex CLI failed."
    lower = raw.lower()
    if "requires a newer version of codex" in lower:
        match = re.search(r"The '[^']+' model requires a newer version of Codex\.[^\r\n<]*", raw, re.IGNORECASE)
        if match:
            return match.group(0).strip()
        return "The configured model requires a newer version of Codex. Upgrade the Codex CLI and retry."
    if "access is denied" in lower or "erişim engellendi" in lower or "erisim engellendi" in lower:
        return "Codex CLI configuration or PATH access was denied by Windows. Run Codex outside the nested sandbox or fix user-profile permissions."
    if "not logged" in lower or "login required" in lower:
        return "Codex CLI login is required. Run `codex login` and retry."
    if "<html" in lower or "cloudflare" in lower or "cf_chl" in lower:
        meaningful = _meaningful_codex_error_lines(raw)
        if meaningful:
            return _clip_error_text(" ".join(meaningful))
        if "403 forbidden" in lower and ("plugins/" in lower or "analytics-events" in lower):
            return "Codex CLI remote plugin/analytics sync returned 403. The warning was ignored, but Codex did not produce a final response."
    stripped = _strip_html(raw)
    meaningful = _meaningful_codex_error_lines(stripped)
    summary = " ".join(meaningful) if meaningful else stripped
    return _clip_error_text(summary or "Codex CLI failed.")


def _summarize_codex_event_error(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        event_type = str(event.get("type") or "")
        if event_type not in {"error", "turn.failed", "failed"}:
            continue
        raw = _event_error_text(event)
        if not raw:
            continue
        return _clip_error_text(_extract_structured_error_message(raw))
    return None


def _event_error_text(event: dict[str, Any]) -> str:
    for key in ("message", "error"):
        value = event.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            message = value.get("message") or value.get("error") or value.get("detail")
            if isinstance(message, str):
                return message
    return ""


def _extract_structured_error_message(raw: str) -> str:
    text = raw.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _strip_html(text)
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            pieces = [
                str(value)
                for value in (error.get("code"), error.get("message"))
                if isinstance(value, str) and value.strip()
            ]
            if pieces:
                return ": ".join(pieces)
        message = parsed.get("message") or parsed.get("detail")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return _strip_html(text)


def _meaningful_codex_error_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = _strip_html(line).strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if lower.startswith(("202", "warn ", "warning:")) and any(
            marker in lower for marker in ("remote plugin sync", "analytics", "featured plugin", "manifest", "shell snapshot")
        ):
            continue
        if any(marker in lower for marker in ("<svg", "<path", "<script", "window._cf_chl", "xmlns=", "fill=", "viewbox", "doctype")):
            continue
        if "forbidden" in lower or "error" in lower or "failed" in lower or "requires a newer version" in lower or "erişim engellendi" in lower:
            lines.append(stripped)
    return lines[-3:]


def _strip_html(text: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", without_tags).strip()


def _clip_error_text(text: str, limit: int = 500) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _parse_jsonl(text: str) -> tuple[list[dict[str, Any]], str | None]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        event, error = _parse_jsonl_line(line, line_number)
        if error:
            return events, error
        if event is not None:
            events.append(event)
    return events, None


def _parse_jsonl_line(line: str, line_number: int) -> tuple[dict[str, Any] | None, str | None]:
    stripped = line.strip()
    if not stripped:
        return None, None
    if _is_ignorable_stdout_noise(stripped):
        return None, None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return None, f"Malformed Codex JSONL on line {line_number}: {exc.msg}"
    if isinstance(parsed, dict):
        return parsed, None
    return None, None


def _is_ignorable_stdout_noise(line: str) -> bool:
    return line.startswith("SUCCESS: The process with PID ") and " has been terminated." in line


def _last_event_text(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        for key in ("text", "content", "message"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                return _repair_mojibake(value.strip())
            if isinstance(value, dict):
                content = value.get("content") or value.get("text")
                if isinstance(content, str) and content.strip():
                    return _repair_mojibake(content.strip())
        payload = event.get("payload")
        if isinstance(payload, dict):
            text = payload.get("text") or payload.get("content")
            if isinstance(text, str) and text.strip():
                return _repair_mojibake(text.strip())
    return ""


def _repair_mojibake(text: str) -> str:
    if not text or not _looks_like_utf8_mojibake(text):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return text
    if _mojibake_score(repaired) < _mojibake_score(text):
        return repaired
    return text


def _looks_like_utf8_mojibake(text: str) -> bool:
    return any(marker in text for marker in ("Ã", "Ä", "Å", "Â", "â€", "\u009f", "\u009e"))


def _mojibake_score(text: str) -> int:
    return sum(text.count(marker) for marker in ("Ã", "Ä", "Å", "Â", "â€", "\u009f", "\u009e"))


def _record(
    *,
    run_id: str,
    job_id: str | None,
    agent_id: str,
    sandbox: str,
    started_at: str,
    ended_at: str,
    exit_code: int | None,
    events_path: Path,
    final_path: Path,
    error: str | None,
) -> CodexRunRecord:
    return CodexRunRecord(
        run_id=run_id,
        job_id=job_id,
        agent_id=agent_id,
        sandbox=sandbox,
        started_at=started_at,
        ended_at=ended_at,
        exit_code=exit_code,
        events_path=str(events_path),
        final_json_path=str(final_path) if final_path.exists() else None,
        error=error,
    )


def _write_record(run_dir: Path, record: CodexRunRecord) -> None:
    (run_dir / "record.json").write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    _write_text(path, "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events))


def _normalized_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": event_type, "timestamp": int(time.time() * 1000), "payload": payload}


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


def _safe_emit(callback: CodexEventCallback | None, event: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        pass


def _normalize_path_text(value: str) -> str:
    return value.replace("\\", "/").lower()


def _codex_mcp_fix_commands(executable: str, expected_server: Path, expected_src: Path) -> list[str]:
    exe = Path(executable).name if "\\" in executable or "/" in executable else executable
    return [
        f"{exe} mcp remove autocad-mcp",
        (
            f"{exe} mcp add autocad-mcp "
            "--env AUTOCAD_MCP_BACKEND=auto "
            "--env AUTOCAD_MCP_IPC_DIR=C:/temp "
            "--env PYTHONIOENCODING=utf-8 "
            f"--env PYTHONPATH=\"{expected_src}\" "
            f"-- uv run --project \"{expected_server}\" python -m autocad_mcp"
        ),
    ]


def _read_codex_global_config() -> str:
    path = Path.home() / ".codex" / "config.toml"
    try:
        return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    except OSError:
        return ""
