"""DSTV NC1 writer for plate specs."""

from __future__ import annotations

from pathlib import Path

from autocad_mcp.technical_office.contour import contour_xy_points
from autocad_mcp.technical_office.models import PlateSpec


def write_plate_nc1(spec: PlateSpec, path: str | Path) -> Path:
    errors = spec.validate()
    if errors:
        raise ValueError("; ".join(errors))

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_nc1(spec), encoding="utf-8", newline="\n")
    return output_path


def _render_nc1(spec: PlateSpec) -> str:
    contour = contour_xy_points(spec)
    lines = [
        "ST",
        f"  {spec.poz_no}",
        f"  {spec.material}",
        f"  THICKNESS {spec.thickness:.3f}",
        f"  QUANTITY {spec.quantity}",
        "AK",
    ]
    for x, y in contour:
        lines.append(f"  {x:.3f} {y:.3f}")
    lines.append("BO")
    if spec.holes:
        for hole in spec.holes:
            lines.append(f"  {hole.x:.3f} {hole.y:.3f} {hole.diameter:.3f}")
    else:
        lines.append("  NONE")
    if spec.slots:
        lines.append("SI")
        for slot in spec.slots:
            lines.append(
                f"  {slot.x:.3f} {slot.y:.3f} {slot.length:.3f} {slot.width:.3f} {slot.rotation_deg:.3f}"
            )
    lines.append("EN")
    return "\n".join(lines) + "\n"
