"""Outer contour helpers for plate DXF and NC1 outputs."""

from __future__ import annotations

import math

from autocad_mcp.technical_office.models import PlateSpec

Point = tuple[float, float]
PointBulge = tuple[float, float, float]

_QUARTER_BULGE = math.tan(math.radians(90.0) / 4.0)


def contour_lwpolyline_points(spec: PlateSpec) -> list[PointBulge]:
    """Return CCW outer-contour vertices with DXF bulges."""

    reliefs = {relief.corner: relief for relief in spec.corner_reliefs}
    if not reliefs:
        return [
            (0.0, 0.0, 0.0),
            (spec.width, 0.0, 0.0),
            (spec.width, spec.height, 0.0),
            (0.0, spec.height, 0.0),
        ]

    bl = reliefs.get("bottom_left")
    br = reliefs.get("bottom_right")
    tr = reliefs.get("top_right")
    tl = reliefs.get("top_left")
    bl_x, bl_y = _corner_offsets(bl)
    br_x, br_y = _corner_offsets(br)
    tr_x, tr_y = _corner_offsets(tr)
    tl_x, tl_y = _corner_offsets(tl)

    vertices: list[PointBulge] = [
        (bl_x, 0.0, 0.0),
        (spec.width - br_x, 0.0, _corner_bulge(br) if br else 0.0),
    ]
    if br:
        vertices.append((spec.width, br_y, 0.0))

    vertices.append((spec.width, spec.height - tr_y, _corner_bulge(tr) if tr else 0.0))
    if tr:
        vertices.append((spec.width - tr_x, spec.height, 0.0))

    vertices.append((tl_x, spec.height, _corner_bulge(tl) if tl else 0.0))
    if tl:
        vertices.append((0.0, spec.height - tl_y, 0.0))

    vertices.append((0.0, bl_y, _corner_bulge(bl) if bl else 0.0))
    return _dedupe_vertices(vertices)


def _corner_offsets(relief) -> tuple[float, float]:
    if not relief:
        return 0.0, 0.0
    x_offset = relief.x_offset if relief.x_offset is not None else relief.radius
    y_offset = relief.y_offset if relief.y_offset is not None else relief.radius
    return float(x_offset), float(y_offset)


def _corner_bulge(relief) -> float:
    if relief.relief_type in {"chamfer", "pah"}:
        return 0.0
    if relief.relief_type == "cugul":
        return -_QUARTER_BULGE
    return _QUARTER_BULGE


def contour_xy_points(spec: PlateSpec, arc_segments: int = 6) -> list[Point]:
    """Return contour points, approximating bulged arcs for NC output."""

    vertices = contour_lwpolyline_points(spec)
    if not vertices:
        return []

    points: list[Point] = [(vertices[0][0], vertices[0][1])]
    for index, vertex in enumerate(vertices):
        start = (vertex[0], vertex[1])
        end_vertex = vertices[(index + 1) % len(vertices)]
        end = (end_vertex[0], end_vertex[1])
        bulge = vertex[2]
        if abs(bulge) < 1e-9:
            _append_unique(points, end)
            continue
        for point in _bulge_arc_points(start, end, bulge, arc_segments):
            _append_unique(points, point)
    if len(points) > 1 and _same_point(points[0], points[-1]):
        points.pop()
    return points


def _bulge_arc_points(start: Point, end: Point, bulge: float, segments: int) -> list[Point]:
    theta = 4.0 * math.atan(bulge)
    chord_x = end[0] - start[0]
    chord_y = end[1] - start[1]
    chord = math.hypot(chord_x, chord_y)
    if chord <= 1e-9 or abs(theta) <= 1e-9:
        return [end]

    radius = chord / (2.0 * math.sin(abs(theta) / 2.0))
    mid = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    unit_x = chord_x / chord
    unit_y = chord_y / chord
    normal = (-unit_y, unit_x) if bulge > 0 else (unit_y, -unit_x)
    center_distance = radius * math.cos(abs(theta) / 2.0)
    center = (
        mid[0] + normal[0] * center_distance,
        mid[1] + normal[1] * center_distance,
    )
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])

    count = max(1, segments)
    return [
        (
            center[0] + radius * math.cos(start_angle + theta * step / count),
            center[1] + radius * math.sin(start_angle + theta * step / count),
        )
        for step in range(1, count + 1)
    ]


def _dedupe_vertices(vertices: list[PointBulge]) -> list[PointBulge]:
    deduped: list[PointBulge] = []
    for vertex in vertices:
        if deduped and _same_point((deduped[-1][0], deduped[-1][1]), (vertex[0], vertex[1])):
            deduped[-1] = (deduped[-1][0], deduped[-1][1], vertex[2])
            continue
        deduped.append(vertex)
    if len(deduped) > 1 and _same_point((deduped[0][0], deduped[0][1]), (deduped[-1][0], deduped[-1][1])):
        deduped.pop()
    return deduped


def _append_unique(points: list[Point], point: Point) -> None:
    if points and _same_point(points[-1], point):
        return
    points.append(point)


def _same_point(a: Point, b: Point) -> bool:
    return abs(a[0] - b[0]) <= 1e-6 and abs(a[1] - b[1]) <= 1e-6
