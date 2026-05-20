from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .constants import (
    RATE_LIMIT_FILE_TICKET,
    RATE_LIMIT_JOB_MUTATION,
    RATE_LIMIT_MANAGER_CHAT,
    RATE_LIMIT_UPLOAD,
)


@dataclass(frozen=True)
class RateLimitRule:
    bucket: str
    max_requests: int
    window_seconds: int


_RULES = {
    RATE_LIMIT_UPLOAD: RateLimitRule(RATE_LIMIT_UPLOAD, 10, 600),
    RATE_LIMIT_MANAGER_CHAT: RateLimitRule(RATE_LIMIT_MANAGER_CHAT, 20, 60),
    RATE_LIMIT_JOB_MUTATION: RateLimitRule(RATE_LIMIT_JOB_MUTATION, 30, 60),
    RATE_LIMIT_FILE_TICKET: RateLimitRule(RATE_LIMIT_FILE_TICKET, 120, 60),
}

_WINDOWS: dict[tuple[str, str], deque[float]] = defaultdict(deque)


async def rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if os.environ.get("TOFFICE_RATE_LIMIT_DISABLED", "").strip() == "1":
        return await call_next(request)

    rule = _rule_for_request(request)
    if rule is None:
        return await call_next(request)

    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    key = (rule.bucket, client)
    window = _WINDOWS[key]
    cutoff = now - rule.window_seconds
    while window and window[0] <= cutoff:
        window.popleft()
    if len(window) >= rule.max_requests:
        retry_after = max(1, int(rule.window_seconds - (now - window[0])))
        return JSONResponse(
            status_code=429,
            content={"ok": False, "error": "rate_limited", "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )
    window.append(now)
    return await call_next(request)


def reset_rate_limits() -> None:
    _WINDOWS.clear()


def _rule_for_request(request: Request) -> RateLimitRule | None:
    if request.method != "POST":
        return None
    path = request.url.path.rstrip("/")
    if path == "/api/jobs":
        return _RULES[RATE_LIMIT_UPLOAD]
    if path == "/api/manager/chat":
        return _RULES[RATE_LIMIT_MANAGER_CHAT]
    if path.endswith("/file-ticket") and path.startswith("/api/jobs/"):
        return _RULES[RATE_LIMIT_FILE_TICKET]
    if _is_job_mutation(path):
        return _RULES[RATE_LIMIT_JOB_MUTATION]
    return None


def _is_job_mutation(path: str) -> bool:
    if path == "/api/learning/backfill":
        return True
    if not path.startswith("/api/jobs/"):
        return False
    return path.endswith(("/run", "/approve-candidates", "/partlist", "/learning/backfill"))
