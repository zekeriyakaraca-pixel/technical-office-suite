from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

import structlog
from fastapi import HTTPException, Request

log = structlog.get_logger(__name__)

_ENV_VAR = "TOFFICE_API_SECRET"
_TOKEN_LIFETIME_SECONDS = 86_400  # 24 hours


def _get_secret() -> str | None:
    return os.environ.get(_ENV_VAR, "").strip() or None


def auth_enabled() -> bool:
    return bool(_get_secret())


def create_token(expires_hours: int = 24) -> str:
    """
    Generate a time-bound HMAC-signed token.
    Format: {expire_ts}:{signature_hex}
    """
    secret = _get_secret()
    if not secret:
        raise RuntimeError(f"{_ENV_VAR} ortam degiskeni ayarlanmamis. Token uretilemiyor.")
    expire_ts = int(time.time()) + expires_hours * 3600
    signature = hmac.new(secret.encode(), str(expire_ts).encode(), hashlib.sha256).hexdigest()
    return f"{expire_ts}:{signature}"


def create_file_token(job_id: str, file_path: str, *, inline: bool, expires_seconds: int = 300) -> str:
    secret = _get_secret()
    if not secret:
        return ""
    expire_ts = int(time.time()) + expires_seconds
    signature = _file_signature(secret, job_id=job_id, file_path=file_path, inline=inline, expire_ts=expire_ts)
    return f"{expire_ts}:{signature}"


def verify_token(token: str) -> bool:
    """Verify token signature and expiry. Returns False if secret is not set (permissive mode)."""
    secret = _get_secret()
    if not secret:
        # No secret configured — operate in open mode (local network)
        return True
    try:
        expire_str, sig = token.split(":", 1)
        expire_ts = int(expire_str)
    except (ValueError, TypeError):
        return False
    if time.time() > expire_ts:
        return False
    expected = hmac.new(secret.encode(), expire_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def verify_file_token(token: str, job_id: str, file_path: str, *, inline: bool) -> bool:
    secret = _get_secret()
    if not secret:
        return True
    try:
        expire_str, sig = token.split(":", 1)
        expire_ts = int(expire_str)
    except (ValueError, TypeError):
        return False
    if time.time() > expire_ts:
        return False
    expected = _file_signature(secret, job_id=job_id, file_path=file_path, inline=inline, expire_ts=expire_ts)
    return hmac.compare_digest(expected, sig)


def request_has_valid_token(request: Request) -> bool:
    secret = _get_secret()
    if not secret:
        return True
    token = _request_bearer_token(request) or request.query_params.get("access_token", "").strip()
    return bool(token and verify_token(token))


def require_auth(request: Request) -> None:
    """
    FastAPI dependency for protected endpoints.
    Skipped entirely if TOFFICE_API_SECRET is not set (local dev mode).
    """
    secret = _get_secret()
    if not secret:
        return  # No secret → open mode, allow all

    token = _request_bearer_token(request) or request.query_params.get("access_token", "").strip()
    if not token:
        log.warning("auth.missing_token", path=str(request.url.path))
        raise HTTPException(status_code=401, detail="Authorization: Bearer <token> gerekli.")
    if not verify_token(token):
        log.warning("auth.invalid_token", path=str(request.url.path))
        raise HTTPException(status_code=401, detail="Gecersiz veya suresi dolmus token.")


def auth_status() -> dict[str, Any]:
    """Return auth configuration status for the health endpoint."""
    secret_set = bool(_get_secret())
    return {
        "enabled": secret_set,
        "mode": "token_required" if secret_set else "open_local",
        "env_var": _ENV_VAR,
    }


def require_file_auth(request: Request, *, job_id: str, file_path: str, inline: bool) -> None:
    if not auth_enabled():
        return
    if request_has_valid_token(request):
        return
    file_token = request.query_params.get("file_token", "").strip()
    if file_token and verify_file_token(file_token, job_id, file_path, inline=inline):
        return
    log.warning("auth.invalid_file_token", path=str(request.url.path), job_id=job_id, file_path=file_path)
    raise HTTPException(status_code=401, detail="Valid bearer token or file_token required.")


def _request_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return ""
    return auth_header[7:].strip()


def _file_signature(secret: str, *, job_id: str, file_path: str, inline: bool, expire_ts: int) -> str:
    payload = f"file:{job_id}:{file_path}:{1 if inline else 0}:{expire_ts}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
