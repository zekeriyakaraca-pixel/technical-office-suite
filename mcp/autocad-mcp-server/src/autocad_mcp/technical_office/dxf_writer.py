"""DXF 2013 writer for plate specs."""

from __future__ import annotations

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
        x0 = slot.x - slot.length / 2.0
        y0 = slot.y - slot.width / 2.0
        pts = [(x0, y0), (x0 + slot.length, y0), (x0 + slot.length, y0 + slot.width), (x0, y0 + slot.width)]
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "PLATE_SLOTS"})

    label = f"{safe_name(spec.poz_no)} T={spec.thickness:g} {spec.material}"
    msp.add_text(label, dxfattribs={"insert": (0, spec.height + 10), "height": 5, "layer": "PLATE_TEXT"})
    doc.saveas(output_path)
    return output_path
