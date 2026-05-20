"""Quality checks for generated plate outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import ezdxf

from autocad_mcp.technical_office.models import PlateSpec
from autocad_mcp.technical_office.partlist_metrics import enrich_partlist_metrics


def build_qc_report(
    spec: PlateSpec,
    dxf_path: str | Path,
    nc1_path: str | Path,
    autocad_live_status: str = "skipped",
) -> dict[str, Any]:
    dxf_file = Path(dxf_path)
    nc1_file = Path(nc1_path)
    checks: dict[str, Any] = {
        "poz_no": spec.poz_no,
        "manual_review_required": False,
        "autocad_live_check": autocad_live_status,
        "dxf": _check_dxf(spec, dxf_file),
        "nc1": _check_nc1(spec, nc1_file),
        "plate_spec": enrich_partlist_metrics(spec.to_dict()),
    }
    checks["ok"] = bool(checks["dxf"]["ok"] and checks["nc1"]["ok"])
    return checks


def write_qc_report(report: dict[str, Any], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return output


def _check_dxf(spec: PlateSpec, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "error": "DXF file does not exist"}
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    circles = [entity for entity in msp if entity.dxftype() == "CIRCLE"]
    closed_polylines = [
        entity for entity in msp if entity.dxftype() == "LWPOLYLINE" and bool(entity.closed)
    ]
    outer = next((entity for entity in closed_polylines if entity.dxf.layer == "PLATE_OUTER"), None)
    outer_points = outer.get_points("xyb") if outer is not None else []
    bulge_count = sum(1 for _x, _y, bulge in outer_points if abs(bulge) > 1e-9)
    polygon_corner_count = max(0, len(outer_points) - 4) if outer_points else 0
    corner_relief_count = max(bulge_count, polygon_corner_count)
    corner_reliefs_ok = not spec.corner_reliefs or corner_relief_count >= len(spec.corner_reliefs)
    slot_polys = [e for e in closed_polylines if e.dxf.layer == "PLATE_SLOTS"]
    slots_ok = len(slot_polys) == len(spec.slots)
    ok = (
        doc.dxfversion == "AC1027"
        and len(circles) == len(spec.holes)
        and bool(closed_polylines)
        and corner_reliefs_ok
        and slots_ok
    )
    return {
        "ok": ok,
        "dxf_version": doc.dxfversion,
        "circle_count": len(circles),
        "closed_polyline_count": len(closed_polylines),
        "expected_holes": len(spec.holes),
        "slot_polyline_count": len(slot_polys),
        "expected_slots": len(spec.slots),
        "corner_relief_count": corner_relief_count,
        "arc_corner_relief_count": bulge_count,
        "polygon_corner_relief_count": polygon_corner_count,
        "outer_contour_vertex_count": len(outer_points),
        "expected_polygon_vertex_count": len(spec.polygon_vertices or []),
        "has_polygon_vertices": bool(spec.polygon_vertices),
        "expected_corner_reliefs": len(spec.corner_reliefs),
    }


def _check_nc1(spec: PlateSpec, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "error": "NC1 file does not exist"}
    text = path.read_text(encoding="utf-8")
    ak_point_count = _count_ak_points(text)
    corner_reliefs_ok = not spec.corner_reliefs or ak_point_count > 4
    ok = (
        text.startswith("ST\n")
        and text.rstrip().endswith("EN")
        and spec.poz_no in text
        and corner_reliefs_ok
    )
    return {
        "ok": ok,
        "has_st": text.startswith("ST\n"),
        "has_en": text.rstrip().endswith("EN"),
        "contains_poz_no": spec.poz_no in text,
        "ak_point_count": ak_point_count,
        "expected_corner_reliefs": len(spec.corner_reliefs),
    }


def _count_ak_points(text: str) -> int:
    count = 0
    in_ak = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "AK":
            in_ak = True
            continue
        if in_ak and stripped == "BO":
            break
        if in_ak and stripped:
            count += 1
    return count
