import asyncio
import sys
from pathlib import Path

from autocad_mcp import autocad_ready
from autocad_mcp.backends.file_ipc import _window_score


def test_discover_acad_path_prefers_env(monkeypatch, tmp_path):
    acad = tmp_path / "acad.exe"
    acad.write_text("", encoding="utf-8")
    monkeypatch.setenv("AUTOCAD_MCP_ACAD_EXE", str(acad))

    assert autocad_ready.discover_acad_path() == str(acad)


def test_startup_script_loads_dispatcher_and_ipc_dir(tmp_path):
    lisp = tmp_path / "mcp_dispatch.lsp"
    ipc_dir = tmp_path / "ipc"
    script = autocad_ready.build_startup_script(lisp_path=lisp, ipc_dir=ipc_dir)
    lisp_lsp_path = str(lisp).replace("\\", "/")
    ipc_lsp_dir = str(ipc_dir).replace("\\", "/")

    assert '(setvar "SECURELOAD" 0)' in script
    assert f'(load "{lisp_lsp_path}")' in script
    assert f'(setq *mcp-ipc-dir* "{ipc_lsp_dir}/")' in script


def test_window_score_detects_blank_title_acad_process():
    assert _window_score("", "acad.exe", has_mdi_client=False) >= 80
    assert _window_score("Drawing1.dwg - AutoCAD", None, has_mdi_client=True) >= 80
    assert _window_score("Untitled", "notepad.exe", has_mdi_client=False) < 80


def test_ensure_ready_reports_missing_acad_path(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(autocad_ready, "IPC_DIR", tmp_path)

    result = asyncio.run(
        autocad_ready.ensure_autocad_ready_async(
            timeout_seconds=0.01,
            find_window_func=lambda: None,
            process_running_func=lambda: False,
            discover_path_func=lambda: None,
        )
    )

    assert result.ok is False
    assert result.started_process is False
    assert result.live_validation_available is False
    assert result.error == "acad.exe not found"


def test_ensure_ready_existing_window_bootstraps_and_reports_file_ipc(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(autocad_ready, "IPC_DIR", tmp_path)
    bootstrapped: list[int] = []

    async def wait_ready(_timeout):
        return True, "file_ipc", None

    result = asyncio.run(
        autocad_ready.ensure_autocad_ready_async(
            timeout_seconds=0.01,
            find_window_func=lambda: 12345,
            process_running_func=lambda: True,
            wait_func=wait_ready,
            bootstrap_func=lambda hwnd: bootstrapped.append(hwnd) is None,
        )
    )

    assert result.ok is True
    assert result.backend == "file_ipc"
    assert result.dispatcher_loaded is True
    assert result.live_validation_available is True
    assert bootstrapped == [12345]
