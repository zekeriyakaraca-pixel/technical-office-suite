from __future__ import annotations

from .config import ensure_autocad_import_path


def normalize_relief_type(value: str, *, default: str = "round") -> str:
    ensure_autocad_import_path()
    from autocad_mcp.technical_office.relief_types import normalize_relief_type as _normalize

    return _normalize(value, default=default)

