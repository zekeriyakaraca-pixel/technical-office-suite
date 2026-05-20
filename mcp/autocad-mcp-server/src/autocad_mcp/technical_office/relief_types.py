from __future__ import annotations


def normalize_relief_type(value: str, *, default: str = "round") -> str:
    normalized = value.strip().lower()
    if normalized in {"pah", "bevel", "beveled", "chamfered"}:
        return "chamfer"
    if normalized in {"round_relief", "rounded", "radius"}:
        return "round"
    return normalized or default

