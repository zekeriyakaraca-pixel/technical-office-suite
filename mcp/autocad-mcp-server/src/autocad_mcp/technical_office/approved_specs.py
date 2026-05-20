"""Read manager-approved plate specs for low-confidence PDF jobs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocad_mcp.technical_office.models import CornerReliefSpec, HoleSpec, PlateSpec, SlotSpec
from autocad_mcp.technical_office.relief_types import normalize_relief_type


APPROVED_SPECS_FILENAME = "approved_plate_specs.json"


class ApprovedSpecValidationError(ValueError):
    """Raised when manager-approved specs cannot be trusted."""


@dataclass(frozen=True)
class ApprovedPlate:
    spec: PlateSpec
    source_pdf: str


def load_approved_plate_specs(job_dir: str | Path) -> list[ApprovedPlate]:
    path = Path(job_dir) / APPROVED_SPECS_FILENAME
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("plates", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ApprovedSpecValidationError("approved_plate_specs.json must contain a list or a plates list")
    approved = [_approved_plate_from_mapping(row, index + 1) for index, row in enumerate(rows)]
    if not approved:
        raise ApprovedSpecValidationError("approved_plate_specs.json does not contain any approved plates")
    return approved


def _approved_plate_from_mapping(row: Any, row_number: int) -> ApprovedPlate:
    if not isinstance(row, dict):
        raise ApprovedSpecValidationError(f"row {row_number}: each approved plate must be an object")

    spec = PlateSpec(
        poz_no=_required_str(row, "poz_no", row_number),
        width=_required_float(row, "width", row_number),
        height=_required_float(row, "height", row_number),
        thickness=_required_float(row, "thickness", row_number),
        material=_optional_str(row.get("material")) or "UNKNOWN",
        quantity=_optional_int(row.get("quantity"), "quantity", row_number) or 1,
        unit_surface_area_m2=_optional_float(row.get("unit_surface_area_m2"), "unit_surface_area_m2", row_number),
        unit_weight_kg=_optional_float(row.get("unit_weight_kg"), "unit_weight_kg", row_number),
        unit=_optional_str(row.get("unit")) or "mm",
        holes=_holes(row.get("holes", []), row_number),
        slots=_slots(row.get("slots", []), row_number),
        corner_reliefs=_corner_reliefs(row.get("corner_reliefs", []), row_number),
        polygon_vertices=_polygon_vertices(row.get("polygon_vertices"), row_number),
        source_page=_optional_int(row.get("source_page"), "source_page", row_number),
        confidence=_optional_float(row.get("confidence"), "confidence", row_number) or 1.0,
        notes=_notes(row.get("notes")),
    )
    errors = spec.validate()
    if errors:
        raise ApprovedSpecValidationError(f"row {row_number}: {'; '.join(errors)}")

    source_pdf = _optional_str(row.get("source_pdf")) or APPROVED_SPECS_FILENAME
    return ApprovedPlate(spec=spec, source_pdf=source_pdf)


def _holes(value: Any, row_number: int) -> list[HoleSpec]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ApprovedSpecValidationError(f"row {row_number}: holes must be a list")
    holes = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ApprovedSpecValidationError(f"row {row_number} hole {index}: must be an object")
        holes.append(
            HoleSpec(
                x=_required_float(item, "x", row_number),
                y=_required_float(item, "y", row_number),
                diameter=_required_float(item, "diameter", row_number),
            )
        )
    return holes


def _slots(value: Any, row_number: int) -> list[SlotSpec]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ApprovedSpecValidationError(f"row {row_number}: slots must be a list")
    slots = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ApprovedSpecValidationError(f"row {row_number} slot {index}: must be an object")
        slots.append(
            SlotSpec(
                x=_required_float(item, "x", row_number),
                y=_required_float(item, "y", row_number),
                length=_required_float(item, "length", row_number),
                width=_required_float(item, "width", row_number),
                rotation_deg=_optional_float(item.get("rotation_deg"), "rotation_deg", row_number) or 0.0,
            )
        )
    return slots


def _corner_reliefs(value: Any, row_number: int) -> list[CornerReliefSpec]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ApprovedSpecValidationError(f"row {row_number}: corner_reliefs must be a list")
    reliefs = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ApprovedSpecValidationError(f"row {row_number} corner relief {index}: must be an object")
        # polygon_contour sentinel: polygon çizim işareti, gerçek relief değil — atla
        rtype = _optional_str(item.get("relief_type") or item.get("type")) or ""
        if normalize_relief_type(rtype) == "polygon_contour":
            continue
        reliefs.append(
            CornerReliefSpec(
                corner=_required_str(item, "corner", row_number),
                radius=_required_float(item, "radius", row_number),
                relief_type=normalize_relief_type(rtype or "round"),
                x_offset=_optional_float(item.get("x_offset"), "x_offset", row_number),
                y_offset=_optional_float(item.get("y_offset"), "y_offset", row_number),
            )
        )
    return reliefs


def _polygon_vertices(value: Any, row_number: int) -> list[dict[str, float]] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, list):
        raise ApprovedSpecValidationError(f"row {row_number}: polygon_vertices must be a list")
    if len(value) < 3:
        raise ApprovedSpecValidationError(f"row {row_number}: polygon_vertices must have at least 3 points")
    vertices: list[dict[str, float]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ApprovedSpecValidationError(f"row {row_number} polygon vertex {index}: must be an object")
        vertices.append(
            {
                "x": _required_float(item, "x", row_number),
                "y": _required_float(item, "y", row_number),
            }
        )
    return _normalize_polygon_orientation(vertices)


def _notes(value: Any) -> list[str]:
    if value is None:
        return ["manager_approved_from_visual_candidate"]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _required_str(row: dict[str, Any], field: str, row_number: int) -> str:
    value = _optional_str(row.get(field))
    if value is None:
        raise ApprovedSpecValidationError(f"row {row_number}: {field} is required")
    return value


def _required_float(row: dict[str, Any], field: str, row_number: int) -> float:
    value = _optional_float(row.get(field), field, row_number)
    if value is None:
        raise ApprovedSpecValidationError(f"row {row_number}: {field} is required")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any, field: str, row_number: int) -> int | None:
    text = _optional_str(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise ApprovedSpecValidationError(f"row {row_number}: {field} must be an integer") from exc


def _optional_float(value: Any, field: str, row_number: int) -> float | None:
    text = _optional_str(value)
    if text is None:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError as exc:
        raise ApprovedSpecValidationError(f"row {row_number}: {field} must be numeric") from exc


def _normalize_polygon_orientation(vertices: list[dict[str, float]]) -> list[dict[str, float]]:
    area = 0.0
    for index, vertex in enumerate(vertices):
        next_vertex = vertices[(index + 1) % len(vertices)]
        area += vertex["x"] * next_vertex["y"] - next_vertex["x"] * vertex["y"]
    if area < 0:
        return list(reversed(vertices))
    return vertices
