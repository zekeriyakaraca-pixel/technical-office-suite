from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, TypeVar

import structlog

log = structlog.get_logger(__name__)

T = TypeVar("T")


class RetryExhausted(RuntimeError):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, attempts: int, last_error: str) -> None:
        super().__init__(f"Tum denemeler basarisiz oldu ({attempts} deneme). Son hata: {last_error}")
        self.attempts = attempts
        self.last_error = last_error


class TimeoutError(RuntimeError):
    """Raised when a single attempt exceeds timeout_seconds."""


async def with_retry(
    fn: Callable[[], Any],
    *,
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    timeout_seconds: float = 120.0,
    job_id: str | None = None,
    operation: str = "operation",
) -> Any:
    """
    Run `fn` (sync or async) with retries and per-attempt timeout.

    Exponential backoff: wait = backoff_base ^ attempt_number seconds.
    Raises RetryExhausted after all attempts fail.
    """
    last_error = "unknown"
    for attempt in range(1, max_attempts + 1):
        try:
            log.debug("retry.attempt", job_id=job_id, operation=operation, attempt=attempt, max=max_attempts)
            if asyncio.iscoroutinefunction(fn):
                result = await asyncio.wait_for(fn(), timeout=timeout_seconds)
            else:
                result = await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout_seconds)
            log.debug("retry.success", job_id=job_id, operation=operation, attempt=attempt)
            return result
        except asyncio.TimeoutError:
            last_error = f"AutoCAD/{operation} {timeout_seconds:.0f} saniye icinde tamamlanamadi. Codex CLI veya PDF boyutunu kontrol edin."
            log.warning("retry.timeout", job_id=job_id, operation=operation, attempt=attempt, timeout_seconds=timeout_seconds)
        except Exception as exc:
            last_error = str(exc)
            log.warning("retry.error", job_id=job_id, operation=operation, attempt=attempt, error=last_error)

        if attempt < max_attempts:
            wait = backoff_base ** attempt
            log.info("retry.backoff", job_id=job_id, operation=operation, wait_seconds=wait, next_attempt=attempt + 1)
            await asyncio.sleep(wait)

    raise RetryExhausted(attempts=max_attempts, last_error=last_error)


def with_retry_sync(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    timeout_seconds: float = 120.0,
    job_id: str | None = None,
    operation: str = "operation",
) -> T:
    """
    Synchronous variant of with_retry for use outside async contexts.
    Uses threading.Timer for timeout enforcement.
    """
    import threading

    last_error = "unknown"
    for attempt in range(1, max_attempts + 1):
        result_container: list[Any] = []
        error_container: list[BaseException] = []
        timed_out = threading.Event()

        def target() -> None:
            try:
                result_container.append(fn())
            except Exception as exc:
                error_container.append(exc)

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout=timeout_seconds)

        if thread.is_alive():
            timed_out.set()
            last_error = f"{operation} {timeout_seconds:.0f} saniyede tamamlanamadi."
            log.warning("retry.timeout_sync", job_id=job_id, operation=operation, attempt=attempt)
        elif error_container:
            last_error = str(error_container[0])
            log.warning("retry.error_sync", job_id=job_id, operation=operation, attempt=attempt, error=last_error)
        else:
            return result_container[0]  # type: ignore[return-value]

        if attempt < max_attempts:
            wait = backoff_base ** attempt
            log.info("retry.backoff_sync", job_id=job_id, operation=operation, wait_seconds=wait)
            time.sleep(wait)

    raise RetryExhausted(attempts=max_attempts, last_error=last_error)
