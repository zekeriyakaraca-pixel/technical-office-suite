"""Shared fixtures for autocad-mcp tests."""

import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture(autouse=True)
def _isolate_backend(monkeypatch):
    """Reset the backend singleton between tests."""
    import autocad_mcp.client as client_mod
    monkeypatch.setattr(client_mod, "_backend", None)
    # Force ezdxf backend for tests (not on Windows or no AutoCAD)
    monkeypatch.setenv("AUTOCAD_MCP_BACKEND", "ezdxf")
