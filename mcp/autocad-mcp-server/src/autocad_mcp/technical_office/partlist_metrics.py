"""Deterministic partlist metrics for plate specs."""

from __future__ import annotations

from typing import Any


STEEL_DENSITY_KG_M3 = 7850.0


def enrich_partlist_metrics(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a plate spec with missing ERT partlist metrics filled.

    Explicit metrics extracted from the PDF are preserved. Missing metrics are
    calculated from geometry using millimeter dimensions and steel density.
    """
    enriched = dict(spec)
    missing = [
        name
        for name in ("unit_surface_area_m2", "unit_weight_kg")
        if enriched.get(name) is None
    ]
    if not missing:
        return enriched

    metrics = calculate_plate_metrics(
        enriched.get("width"),
        enriched.get("height"),
        enriched.get("thickness"),
    )
    if metrics is None:
        return enriched

    calculated: list[str] = []
    for name in missing:
        enriched[name] = metrics[name]
        calculated.append(name)
    if calculated:
        enriched["partlist_metrics_source"] = "calculated_geometry"
        enriched["partlist_metrics_calculated"] = calculated
        enriched["partlist_metrics_density_kg_m3"] = STEEL_DENSITY_KG_M3
    return enriched


def calculate_plate_metrics(
    width_mm: Any,
    height_mm: Any,
    thickness_mm: Any,
) -> dict[str, float] | None:
    width = _positive_float(width_mm)
    height = _positive_float(height_mm)
    thickness = _positive_float(thickness_mm)
    if width is None or height is None or thickness is None:
        return None

    unit_surface_area_m2 = 2 * (width * height + width * thickness + height * thickness) / 1_000_000
    unit_weight_kg = width * height * thickness * STEEL_DENSITY_KG_M3 / 1_000_000_000
    return {
        "unit_surface_area_m2": round(unit_surface_area_m2, 6),
        "unit_weight_kg": round(unit_weight_kg, 6),
    }


def _positive_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
