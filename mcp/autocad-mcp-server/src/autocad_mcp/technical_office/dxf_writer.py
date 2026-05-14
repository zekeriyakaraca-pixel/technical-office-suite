"""DXF 2013 writer for plate specs."""

from __future__ import annotations

import math
from pathlib import Path

import ezdxf

from autocad_mcp.technical_office.contour import contour_lwpolyline_points
from autocad_mcp.technical_office.models import PlateSpec
from autocad_mcp.technical_office.naming import safe_name


def write_plate_dxf(spec: PlateSpec, path: str | Path) -> Path:
    errors = spec.validate()
    if errors:
        raise ValueError("; ".join(errors))

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 4  # millimeters
    for name, color in (
        ("PLATE_OUTER", 1),
        ("PLATE_HOLES", 5),
        ("PLATE_SLOTS", 3),
        ("PLATE_TEXT", 7),
    ):
        if name not in doc.layers:
            doc.layers.add(name, color=color)

    msp = doc.modelspace()
    outer = contour_lwpolyline_points(spec)
    msp.add_lwpolyline(outer, format="xyb", close=True, dxfattribs={"layer": "PLATE_OUTER"})
    for hole in spec.holes:
        msp.add_circle((hole.x, hole.y), hole.diameter / 2.0, dxfattribs={"layer": "PLATE_HOLES"})
    for slot in spec.slots:
        half_len = slot.length / 2.0
        half_wid = slot.width / 2.0
        rad = math.radians(slot.rotation_deg)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        corners_local = [
            (-half_len, -half_wid), (half_len, -half_wid),
            (half_len, half_wid), (-half_len, half_wid),
        ]
        pts = [
            (slot.x + lx * cos_r - ly * sin_r, slot.y + lx * sin_r + ly * cos_r)
            for lx, ly in corners_local
        ]
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "PLATE_SLOTS"})

    label = f"{safe_name(spec.poz_no)} T={spec.thickness:g} {spec.material}"
    msp.add_text(label, dxfattribs={"insert": (0, spec.height + 10), "height": 5, "layer": "PLATE_TEXT"})
    doc.saveas(output_path)
    return output_path
