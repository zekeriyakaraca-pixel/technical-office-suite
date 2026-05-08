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


def require_auth(request: Request) -> None:
    """
    FastAPI dependency for protected endpoints.
    Skipped entirely if TOFFICE_API_SECRET is not set (local dev mode).
    """
    secret = _get_secret()
    if not secret:
        return  # No secret → open mode, allow all

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
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
