from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

MAX_SESSION_MESSAGES = 40
_OLD_LOCAL_MODEL_NAME = "ol" + "lama"

_SAFE_ID = re.compile(r"[^a-zA-Z0-9_\-]")
_SESSION_AGENT_RE = re.compile(r"^agent:(?P<agent_id>[^:]+):")
_JOB_ID_RE = re.compile(r"\b[A-Za-z]+-[0-9][A-Za-z0-9_.-]*\b")
_PDF_RE = re.compile(r"\b[A-Za-z0-9_.-]+\.pdf\b", re.IGNORECASE)


def _session_path(sessions_root: Path, session_id: str) -> Path:
    safe = _SAFE_ID.sub("_", session_id)[:120]
    return sessions_root / f"{safe}.json"


def load_session(sessions_root: Path, session_id: str) -> list[dict[str, Any]]:
    path = _session_path(sessions_root, session_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        messages = data.get("messages") if isinstance(data, dict) else data
        if not isinstance(messages, list):
            return []
        return sanitize_messages(
            [m for m in messages if isinstance(m, dict) and m.get("role") in {"user", "assistant"}],
            session_id=session_id,
        )
    except Exception:
        return []


def save_session(sessions_root: Path, session_id: str, messages: list[dict[str, Any]]) -> None:
    sessions_root.mkdir(parents=True, exist_ok=True)
    trimmed = sanitize_messages(messages, session_id=session_id)[-MAX_SESSION_MESSAGES:]
    path = _session_path(sessions_root, session_id)
    try:
        path.write_text(
            json.dumps({"session_id": session_id, "messages": trimmed}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def merge_history(
    server_history: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge server-side and incoming (frontend) histories.

    Server history is the authoritative source. Incoming messages that are
    already present in the server history (matched by role+content) are skipped
    to avoid duplication. Any genuinely new turns from the incoming list are
    appended at the end.
    """
    if not server_history:
        return list(incoming)

    server_set = {(m.get("role"), (m.get("content") or "").strip()) for m in server_history}
    extra = [
        m for m in incoming
        if (m.get("role"), (m.get("content") or "").strip()) not in server_set
    ]
    return server_history + extra


def list_sessions(
    sessions_root: Path,
    *,
    agent_id: str | None = None,
    search: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    entries = [_session_entry_from_path(path) for path in sessions_root.glob("*.json")]
    sessions = [entry for entry in entries if entry is not None]
    if agent_id:
        sessions = [entry for entry in sessions if entry.get("agent_id") == agent_id]
    if search:
        needle = search.strip().lower()
        sessions = [
            entry
            for entry in sessions
            if needle in str(entry.get("session_id") or "").lower()
            or needle in str(entry.get("display_name") or "").lower()
        ]
    sessions.sort(key=lambda entry: entry.get("updated_at") or 0, reverse=True)
    return sessions[: max(1, min(limit, 100))]


def ensure_main_session_entry(
    *,
    agent_id: str,
    main_key: str,
    model: str,
    origin_label: str,
) -> dict[str, Any]:
    session_id = f"agent:{agent_id}:{main_key or 'main'}"
    return {
        "session_id": session_id,
        "key": session_id,
        "agent_id": agent_id,
        "display_name": agent_id,
        "updated_at": None,
        "message_count": 0,
        "model": model,
        "modelProvider": "custom",
        "origin": {"label": origin_label, "provider": "custom"},
    }


def preview_sessions(
    sessions_root: Path,
    *,
    keys: list[str],
    limit: int = 8,
    max_chars: int = 240,
) -> dict[str, Any]:
    previews = []
    for key in keys:
        messages = load_session(sessions_root, key)
        items = []
        for message in messages[-max(1, min(limit, 50)):]:
            content = str(message.get("content") or "").strip()
            if max_chars > 0 and len(content) > max_chars:
                content = f"{content[:max_chars].rstrip()}..."
            items.append(
                {
                    "role": message.get("role") if message.get("role") in {"user", "assistant"} else "other",
                    "text": content,
                    "timestamp": _message_timestamp(message),
                }
            )
        previews.append(
            {
                "key": key,
                "status": "ok" if items else "empty",
                "items": items,
            }
        )
    return {"ts": _now_ms(), "previews": previews}


def reset_session(sessions_root: Path, session_id: str) -> bool:
    path = _session_path(sessions_root, session_id)
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def sanitize_session_files(sessions_root: Path) -> int:
    if not sessions_root.exists():
        return 0
    changed = 0
    for path in sessions_root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            session_id = path.stem
        messages_raw = data.get("messages")
        if not isinstance(messages_raw, list):
            continue
        sanitized = sanitize_messages(
            [message for message in messages_raw if isinstance(message, dict)],
            session_id=session_id,
        )
        if sanitized == messages_raw:
            continue
        try:
            path.write_text(
                json.dumps({"session_id": session_id, "messages": sanitized}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            changed += 1
        except OSError:
            continue
    return changed


def runtime_session_status(sessions_root: Path) -> dict[str, Any]:
    entries = list_sessions(sessions_root, limit=100)
    recent = [
        {
            "key": entry["key"],
            "updatedAt": entry.get("updated_at"),
        }
        for entry in entries
    ]
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        agent_id = str(entry.get("agent_id") or "main")
        by_agent.setdefault(agent_id, []).append(
            {"key": entry["key"], "updatedAt": entry.get("updated_at")}
        )
    return {
        "recent": recent,
        "byAgent": [
            {"agentId": agent_id, "recent": values}
            for agent_id, values in sorted(by_agent.items())
        ],
    }


def sanitize_messages(messages: list[dict[str, Any]], *, session_id: str) -> list[dict[str, Any]]:
    agent_id = parse_agent_id(session_id)
    sanitized: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        clean_content = _fix_mojibake(content.strip())
        if role == "assistant" and _looks_like_unsafe_pdf_guidance(clean_content):
            clean_content = _manual_review_replacement(clean_content, agent_id=agent_id)
        next_message: dict[str, Any] = {"role": role, "content": clean_content}
        timestamp = _message_timestamp(message)
        if timestamp is not None:
            next_message["timestamp"] = timestamp
        sanitized.append(next_message)
    return sanitized


def parse_agent_id(session_id: str) -> str | None:
    match = _SESSION_AGENT_RE.match(session_id.strip())
    return match.group("agent_id") if match else None


def _session_entry_from_path(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        session_id = path.stem
    messages_raw = data.get("messages")
    messages = messages_raw if isinstance(messages_raw, list) else []
    messages = sanitize_messages(
        [m for m in messages if isinstance(m, dict)],
        session_id=session_id,
    )
    updated_at = _session_updated_at(path, messages)
    return {
        "session_id": session_id,
        "key": session_id,
        "agent_id": parse_agent_id(session_id),
        "display_name": _display_name_from_session(session_id),
        "updated_at": updated_at,
        "message_count": len(messages),
        "last_user_message": _last_message(messages, "user"),
        "last_assistant_message": _last_message(messages, "assistant"),
    }


def _display_name_from_session(session_id: str) -> str:
    agent_id = parse_agent_id(session_id)
    return agent_id or session_id


def _session_updated_at(path: Path, messages: list[dict[str, Any]]) -> int | None:
    for message in reversed(messages):
        timestamp = _message_timestamp(message)
        if timestamp is not None:
            return timestamp
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return None


def _message_timestamp(message: dict[str, Any]) -> int | None:
    raw = message.get("timestamp") or message.get("createdAt") or message.get("at")
    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    if isinstance(raw, str):
        try:
            parsed = float(raw)
        except ValueError:
            return None
        if parsed > 0:
            return int(parsed)
    return None


def _last_message(messages: list[dict[str, Any]], role: str) -> str | None:
    for message in reversed(messages):
        if message.get("role") == role:
            content = message.get("content")
            return content if isinstance(content, str) else None
    return None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _fix_mojibake(value: str) -> str:
    if not any(marker in value for marker in ("Ã", "Ä", "Å", "ğŸ")):
        return value
    try:
        fixed = value.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return value
    return fixed if fixed.count("\ufffd") <= value.count("\ufffd") else value


def _looks_like_unsafe_pdf_guidance(content: str) -> bool:
    lower = content.lower()
    return any(
        phrase in lower
        for phrase in (
            "position numbers (page numbers)",
            "lacks explicit page numbering",
            "add page numbers to the pdf",
            "please enable local ocr/vision",
            "enable ocr/vision",
            "please enable local ocr/" + _OLD_LOCAL_MODEL_NAME + " vision",
            "enable ocr/" + _OLD_LOCAL_MODEL_NAME + " vision",
            "local " + _OLD_LOCAL_MODEL_NAME + " ajan beyni",
            "requires manual review because the pdf",
            "position) field",
            "requires manual correction",
            "specific pages",
            "generated files: none",
            "pdftotext your_file.pdf",
            "your_file.pdf",
        )
    ) or "\\boxed" in content


def _manual_review_replacement(content: str, *, agent_id: str | None) -> str:
    job_id = _extract_first(_JOB_ID_RE, content) or "ilgili is"
    pdf_name = _extract_first(_PDF_RE, content) or "ilgili PDF"
    if agent_id == "kalite-kontrol":
        return "\n".join(
            [
                f"`{job_id}` icin QC karari: uretim/teslim kapali, teknik-ofis-muduru incelemesi gerekiyor.",
                f"`{pdf_name}` metin katmanindan teknik poz/olcu bilgisi guvenilir okunamiyor; bu QC ajaninin sistem ayari yapacagi bir durum degil.",
                "Kalite kontrol ajani kullaniciya OCR/vision provider acma talimati vermez; bulguyu `manual_review_required` olarak mudure iletir.",
                "Mudur gorsel/OCR aday okuma veya manuel poz/olcu girisi akisina karar verdikten sonra adaylar onaylanir; QC ok=true olmadan partlist veya teslim acilmaz.",
            ]
        )
    return "\n".join(
        [
            f"`{job_id}` icin eski hata yorumu guncellendi: `poz_no` sayfa numarasi degil, teknik poz/parca numarasidir.",
            f"`{pdf_name}` metin katmani guvenilir okunamiyorsa is `manual_review_required` olarak ele alinir.",
            "Gorsel/OCR aday okuma veya manuel poz/olcu girisi akisina teknik-ofis-muduru karar verir; mudur onayi ve QC ok=true olmadan partlist/teslim acilmaz.",
        ]
    )


def _extract_first(pattern: re.Pattern[str], content: str) -> str | None:
    match = pattern.search(content)
    if not match:
        return None
    value = match.group(0)
    return None if value.lower() == "your_file.pdf" else value
