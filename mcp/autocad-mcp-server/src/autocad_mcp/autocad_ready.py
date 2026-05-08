"""AutoCAD process bootstrap and File IPC readiness helpers."""

from __future__ import annotations

import asyncio
import glob
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import structlog

from autocad_mcp.config import IPC_DIR, LISP_DIR

log = structlog.get_logger()

COMMON_ACAD_PATHS = [
    r"C:\Program Files\Autodesk\AutoCAD 2026\acad.exe",
    r"C:\Program Files\Autodesk\AutoCAD 2025\acad.exe",
    r"C:\Program Files\Autodesk\AutoCAD 2024\acad.exe",
    r"C:\Program Files\Autodesk\AutoCAD 2023\acad.exe",
    r"C:\Program Files\Autodesk\AutoCAD 2022\acad.exe",
    r"C:\Program Files\Autodesk\AutoCAD 2021\acad.exe",
]


@dataclass
class AutoCADReadyResult:
    ok: bool
    started_process: bool = False
    acad_path: str | None = None
    backend: str = "ezdxf"
    dispatcher_loaded: bool = False
    live_validation_available: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    hwnd: int | None = None
    startup_script: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def discover_acad_path() -> str | None:
    """Find acad.exe from env, registry, and common Autodesk install paths."""
    env_path = os.environ.get("AUTOCAD_MCP_ACAD_EXE", "").strip()
    if env_path and Path(env_path).is_file():
        return str(Path(env_path))

    for candidate in _registry_acad_candidates():
        if Path(candidate).is_file():
            return str(Path(candidate))

    for candidate in COMMON_ACAD_PATHS:
        if Path(candidate).is_file():
            return str(Path(candidate))

    matches = sorted(glob.glob(r"C:\Program Files\Autodesk\AutoCAD *\acad.exe"), reverse=True)
    return matches[0] if matches else None


def build_startup_script(lisp_path: str | Path | None = None, ipc_dir: str | Path | None = None) -> str:
    """Build an AutoCAD /b startup script that loads the MCP dispatcher."""
    lisp = _lisp_path(lisp_path or (LISP_DIR / "mcp_dispatch.lsp"))
    ipc = _lisp_dir(ipc_dir or IPC_DIR)
    return "\n".join(
        [
            '(setvar "FILEDIA" 0)',
            '(setvar "CMDDIA" 0)',
            '(setvar "SECURELOAD" 0)',
            f'(load "{lisp}")',
            f'(setq *mcp-ipc-dir* "{ipc}")',
            '(princ "\\nMCP startup bootstrap complete")',
            "(princ)",
            "",
        ]
    )


def write_startup_script(
    script_dir: str | Path | None = None,
    lisp_path: str | Path | None = None,
    ipc_dir: str | Path | None = None,
) -> Path:
    target_dir = Path(script_dir) if script_dir else Path(ipc_dir or IPC_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    script_path = target_dir / "autocad_mcp_startup.scr"
    script_path.write_text(build_startup_script(lisp_path, ipc_dir), encoding="utf-8")
    return script_path


def is_acad_process_running() -> bool:
    """Return True when an acad.exe process is visible to tasklist."""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq acad.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    return "acad.exe" in (result.stdout or "").lower()


def launch_autocad(acad_path: str | Path, startup_script: str | Path) -> subprocess.Popen:
    """Launch AutoCAD with the dispatcher startup script."""
    path = Path(acad_path)
    script = Path(startup_script)
    return subprocess.Popen(
        [
            str(path),
            "/product",
            "ACAD",
            "/language",
            "en-US",
            "/nologo",
            "/b",
            str(script),
        ],
        cwd=str(path.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def wait_for_file_ipc_ready(
    timeout_seconds: float = 60.0,
    poll_seconds: float = 1.0,
) -> tuple[bool, str, str | None]:
    """Wait until the MCP backend initializes as file_ipc."""
    backend_env = os.environ.get("AUTOCAD_MCP_BACKEND", "auto").strip().lower()
    if backend_env == "ezdxf":
        return False, "ezdxf", "AUTOCAD_MCP_BACKEND=ezdxf disables File IPC live validation"

    deadline = time.monotonic() + timeout_seconds
    last_error: str | None = None
    last_backend = "unknown"

    while time.monotonic() <= deadline:
        try:
            from autocad_mcp import client

            client._backend = None
            backend = await client.get_backend()
            last_backend = backend.name
            status = await backend.status()
            if backend.name == "file_ipc" and status.ok:
                return True, backend.name, None
            last_error = status.error
        except Exception as exc:
            last_error = str(exc)
        await asyncio.sleep(poll_seconds)

    return False, last_backend, last_error or "Timed out waiting for File IPC backend"


async def ensure_autocad_ready_async(
    timeout_seconds: float = 60.0,
    launch: bool = True,
    acad_path: str | None = None,
    *,
    find_window_func: Callable[[], int | None] | None = None,
    process_running_func: Callable[[], bool] | None = None,
    discover_path_func: Callable[[], str | None] | None = None,
    launch_func: Callable[[str | Path, str | Path], object] | None = None,
    wait_func: Callable[[float], Awaitable[tuple[bool, str, str | None]]] | None = None,
    bootstrap_func: Callable[[int], bool] | None = None,
) -> AutoCADReadyResult:
    """Ensure AutoCAD is running, dispatcher is loaded, and File IPC is ready."""
    warnings: list[str] = []
    if sys.platform != "win32":
        return AutoCADReadyResult(
            ok=False,
            backend="ezdxf",
            warnings=["AutoCAD File IPC requires Windows Python"],
            error="unsupported_platform",
        )

    Path(IPC_DIR).mkdir(parents=True, exist_ok=True)
    find_window = find_window_func or _default_find_window
    process_running = process_running_func or is_acad_process_running
    discover_path = discover_path_func or discover_acad_path
    start = launch_func or launch_autocad
    wait_ready = wait_func or (lambda timeout: wait_for_file_ipc_ready(timeout))
    bootstrap = bootstrap_func or _bootstrap_dispatcher_into_window

    started_process = False
    startup_script: Path | None = None
    hwnd = find_window()

    if hwnd:
        if not bootstrap(hwnd):
            warnings.append("Dispatcher bootstrap typing could not be confirmed")
    elif launch:
        running = process_running()
        if running:
            warnings.append("acad.exe is running but no usable AutoCAD window was detected")
        else:
            selected_path = acad_path or discover_path()
            if not selected_path:
                return AutoCADReadyResult(
                    ok=False,
                    backend="ezdxf",
                    warnings=warnings,
                    error="acad.exe not found",
                )
            startup_script = write_startup_script(ipc_dir=IPC_DIR)
            try:
                start(selected_path, startup_script)
                started_process = True
                acad_path = selected_path
            except Exception as exc:
                return AutoCADReadyResult(
                    ok=False,
                    backend="ezdxf",
                    acad_path=selected_path,
                    startup_script=str(startup_script),
                    warnings=warnings,
                    error=f"AutoCAD launch failed: {exc}",
                )
    else:
        warnings.append("AutoCAD launch disabled by caller")

    ok, backend, error = await wait_ready(timeout_seconds)
    hwnd = find_window()
    if ok:
        return AutoCADReadyResult(
            ok=True,
            started_process=started_process,
            acad_path=acad_path,
            backend=backend,
            dispatcher_loaded=True,
            live_validation_available=True,
            warnings=warnings,
            hwnd=hwnd,
            startup_script=str(startup_script) if startup_script else None,
        )

    warnings.append("AutoCAD live validation is unavailable; DXF/NC production can continue headless")
    return AutoCADReadyResult(
        ok=False,
        started_process=started_process,
        acad_path=acad_path,
        backend=backend if backend != "unknown" else "ezdxf",
        dispatcher_loaded=False,
        live_validation_available=False,
        warnings=warnings,
        error=error,
        hwnd=hwnd,
        startup_script=str(startup_script) if startup_script else None,
    )


def ensure_autocad_ready(
    timeout_seconds: float = 60.0,
    launch: bool = True,
    acad_path: str | None = None,
) -> AutoCADReadyResult:
    """Synchronous wrapper for non-MCP callers."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(ensure_autocad_ready_async(timeout_seconds, launch, acad_path))
    raise RuntimeError("Use ensure_autocad_ready_async from an active event loop")


def _registry_acad_candidates() -> list[str]:
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []

    candidates: list[str] = []
    roots = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
    base_keys = [
        r"SOFTWARE\Autodesk\AutoCAD",
        r"SOFTWARE\WOW6432Node\Autodesk\AutoCAD",
    ]
    value_names = ["AcadLocation", "Location", "InstallLocation", "ACADLOCATION", "GlobUPILocation"]

    for root in roots:
        for base in base_keys:
            for key_path in _walk_registry(root, base, max_depth=3):
                try:
                    with winreg.OpenKey(root, key_path) as key:
                        for value_name in value_names:
                            try:
                                value, _ = winreg.QueryValueEx(key, value_name)
                            except OSError:
                                continue
                            candidates.extend(_acad_exe_candidates_from_value(str(value)))
                except OSError:
                    continue
    return candidates


def _walk_registry(root, key_path: str, max_depth: int) -> list[str]:
    try:
        import winreg
    except ImportError:
        return []

    found = [key_path]
    if max_depth <= 0:
        return found
    try:
        with winreg.OpenKey(root, key_path) as key:
            index = 0
            while True:
                try:
                    child = winreg.EnumKey(key, index)
                except OSError:
                    break
                found.extend(_walk_registry(root, f"{key_path}\\{child}", max_depth - 1))
                index += 1
    except OSError:
        pass
    return found


def _acad_exe_candidates_from_value(value: str) -> list[str]:
    path = Path(value)
    if path.name.lower() == "acad.exe":
        return [str(path)]
    return [str(path / "acad.exe")]


def _default_find_window() -> int | None:
    from autocad_mcp.backends.file_ipc import find_autocad_window

    return find_autocad_window()


def _bootstrap_dispatcher_into_window(hwnd: int) -> bool:
    """Best-effort load of mcp_dispatch.lsp into an already open AutoCAD window."""
    if not hwnd or sys.platform != "win32":
        return False
    expression = (
        '(progn '
        '(setvar "FILEDIA" 0) '
        '(setvar "CMDDIA" 0) '
        '(setvar "SECURELOAD" 0) '
        f'(load "{_lisp_path(LISP_DIR / "mcp_dispatch.lsp")}") '
        f'(setq *mcp-ipc-dir* "{_lisp_dir(IPC_DIR)}") '
        '(princ))'
    )
    try:
        import ctypes
        import win32gui

        target = _find_child_by_class(hwnd, "MDIClient") or hwnd
        post = ctypes.windll.user32.PostMessageW
        for _ in range(2):
            post(target, 0x0100, 0x1B, 0)
            post(target, 0x0101, 0x1B, 0)
        time.sleep(0.05)
        for ch in expression:
            post(target, 0x0102, ord(ch), 0)
        post(target, 0x0102, 0x0D, 0)
        return bool(win32gui.IsWindow(hwnd))
    except Exception as exc:
        log.warning("autocad_bootstrap_typing_failed", error=str(exc))
        return False


def _find_child_by_class(hwnd: int, class_name: str) -> int | None:
    try:
        import win32gui

        matches: list[int] = []

        def callback(child_hwnd, _):
            if win32gui.GetClassName(child_hwnd) == class_name:
                matches.append(child_hwnd)
                return False
            return True

        win32gui.EnumChildWindows(hwnd, callback, None)
        return matches[0] if matches else None
    except Exception:
        return None


def _lisp_path(path: str | Path) -> str:
    return str(Path(path)).replace("\\", "/")


def _lisp_dir(path: str | Path) -> str:
    value = str(Path(path)).replace("\\", "/")
    return value.rstrip("/") + "/"
