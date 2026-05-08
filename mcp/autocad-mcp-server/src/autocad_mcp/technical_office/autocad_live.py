"""Optional AutoCAD live validation for generated technical-office DXFs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

from autocad_mcp.autocad_ready import AutoCADReadyResult, ensure_autocad_ready_async

EnsureReady = Callable[[], Awaitable[AutoCADReadyResult]]


async def run_autocad_live_validation_async(
    dxf_path: str | Path,
    ensure_ready: EnsureReady | None = None,
) -> str:
    """Open a generated DXF in live AutoCAD when File IPC is available."""
    ready = await (ensure_ready or (lambda: ensure_autocad_ready_async(timeout_seconds=60.0)))()
    if not ready.ok:
        return "skipped_autostart_failed"

    try:
        from autocad_mcp import client

        backend = await client.get_backend()
        if backend.name != "file_ipc":
            return "skipped_autostart_failed"

        open_result = await backend.drawing_open(str(Path(dxf_path)))
        if not open_result.ok:
            return "failed_open"

        zoom_result = await backend.zoom_extents()
        if not zoom_result.ok:
            return "failed_zoom"

        preview_path = Path(dxf_path).with_name(f"{Path(dxf_path).stem}_preview.pdf")
        plot_result = await backend.drawing_plot_pdf(str(preview_path))
        if not plot_result.ok:
            return "ok_plot_failed"
    except Exception:
        return "skipped_autostart_failed"

    return "ok"


def run_autocad_live_validation(dxf_path: str | Path) -> str:
    """Synchronous wrapper for the deterministic production pipeline."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_autocad_live_validation_async(dxf_path))
    raise RuntimeError("Use run_autocad_live_validation_async from an active event loop")
