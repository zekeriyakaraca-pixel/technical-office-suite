"""Data contracts for technical office plate production."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

_GEOMETRY_TOLERANCE = 1e-6


@dataclass
class PositionRecord:
    """Optional manager-provided position naming and metadata."""

    poz_no: str
    page: int | None = None
    quantity: int = 1
    thickness_mm: float | None = None
    material: str | None = None
    name_override: str | None = None
    notes: str | None = None

    @property
    def output_name(self) -> str:
        return self.name_override or self.poz_no


@dataclass
class HoleSpec:
    x: float
    y: float
    diameter: float


@dataclass
class SlotSpec:
    x: float
    y: float
    length: float
    width: float
    rotation_deg: float = 0.0


@dataclass
class CornerReliefSpec:
    corner: str
    radius: float
    relief_type: str = "round"
    x_offset: float | None = None
    y_offset: float | None = None

    def effective_x_offset(self) -> float:
        return self.x_offset if self.x_offset is not None else self.radius

    def effective_y_offset(self) -> float:
        return self.y_offset if self.y_offset is not None else self.radius


@dataclass
class PlateSpec:
    poz_no: str
    width: float
    height: float
    thickness: float
    material: str = "UNKNOWN"
    quantity: int = 1
    unit_surface_area_m2: float | None = None
    unit_weight_kg: float | None = None
    unit: str = "mm"
    holes: list[HoleSpec] = field(default_factory=list)
    slots: list[SlotSpec] = field(default_factory=list)
    corner_reliefs: list[CornerReliefSpec] = field(default_factory=list)
    polygon_vertices: list[dict[str, float]] | None = None
    source_page: int | None = None
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.poz_no.strip():
            errors.append("poz_no is required")
        if self.unit != "mm":
            errors.append(f"unsupported unit: {self.unit}")
        for field_name, value in (
            ("width", self.width),
            ("height", self.height),
            ("thickness", self.thickness),
        ):
            if value <= 0:
                errors.append(f"{field_name} must be positive")
        if self.quantity <= 0:
            errors.append("quantity must be positive")
        for index, hole in enumerate(self.holes, start=1):
            if hole.diameter <= 0:
                errors.append(f"hole {index} diameter must be positive")
            if not (0 <= hole.x <= self.width and 0 <= hole.y <= self.height):
                errors.append(f"hole {index} is outside plate bounds")
        for index, slot in enumerate(self.slots, start=1):
            if slot.length <= 0 or slot.width <= 0:
                errors.append(f"slot {index} dimensions must be positive")
        seen_corners: set[str] = set()
        allowed_corners = {"bottom_left", "bottom_right", "top_right", "top_left"}
        for index, relief in enumerate(self.corner_reliefs, start=1):
            if relief.corner not in allowed_corners:
                errors.append(f"corner relief {index} has unsupported corner: {relief.corner}")
            if relief.corner in seen_corners:
                errors.append(f"corner relief {index} duplicates corner: {relief.corner}")
            seen_corners.add(relief.corner)
            if relief.radius <= 0:
                errors.append(f"corner relief {index} radius must be positive")
            x_offset = relief.effective_x_offset()
            y_offset = relief.effective_y_offset()
            if x_offset <= 0 or y_offset <= 0:
                errors.append(f"corner relief {index} offsets must be positive")
            if x_offset >= self.width or y_offset >= self.height:
                errors.append(f"corner relief {index} radius is too large for plate bounds")
            if relief.relief_type not in {"round", "cugul", "chamfer", "pah"}:
                errors.append(f"corner relief {index} has unsupported type: {relief.relief_type}")
        if self.polygon_vertices is not None:
            if len(self.polygon_vertices) < 3:
                errors.append("polygon_vertices must have at least 3 points")
            for i, v in enumerate(self.polygon_vertices):
                if "x" not in v or "y" not in v:
                    errors.append(f"polygon_vertices[{i}] must have x and y")
            errors.extend(_polygon_geometry_errors(self.polygon_vertices, self.width, self.height))
        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ManualReview:
    reason: str
    page: int | None = None
    poz_no: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _polygon_geometry_errors(vertices: list[dict[str, float]], width: float, height: float) -> list[str]:
    errors: list[str] = []
    if len(vertices) < 3:
        return errors

    points: list[tuple[float, float]] = []
    for index, vertex in enumerate(vertices):
        try:
            x = float(vertex["x"])
            y = float(vertex["y"])
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"polygon_vertices[{index}] must contain numeric x and y")
            continue
        points.append((x, y))
        if x < -_GEOMETRY_TOLERANCE or x > width + _GEOMETRY_TOLERANCE:
            errors.append(f"polygon_vertices[{index}].x is outside plate bounds")
        if y < -_GEOMETRY_TOLERANCE or y > height + _GEOMETRY_TOLERANCE:
            errors.append(f"polygon_vertices[{index}].y is outside plate bounds")

    if len(points) < 3:
        return errors

    for index, point in enumerate(points):
        previous = points[index - 1]
        if _same_point(point, previous):
            errors.append(f"polygon_vertices[{index}] duplicates an adjacent point")

    if _polygon_self_intersects(points):
        errors.append("polygon_vertices must not self-intersect")
    area = _signed_area(points)
    if abs(area) <= _GEOMETRY_TOLERANCE:
        errors.append("polygon_vertices must describe a non-zero area contour")

    bbox_tolerance = max(1.0, min(width, height) * 0.02)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    if (
        abs(min(xs)) > bbox_tolerance
        or abs(min(ys)) > bbox_tolerance
        or abs(max(xs) - width) > bbox_tolerance
        or abs(max(ys) - height) > bbox_tolerance
    ):
        errors.append("polygon_vertices bounding box must match plate width/height within tolerance")
    return errors


def _signed_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _polygon_self_intersects(points: list[tuple[float, float]]) -> bool:
    count = len(points)
    for i in range(count):
        a1 = points[i]
        a2 = points[(i + 1) % count]
        for j in range(i + 1, count):
            if abs(i - j) <= 1 or {i, j} == {0, count - 1}:
                continue
            b1 = points[j]
            b2 = points[(j + 1) % count]
            if _segments_intersect(a1, a2, b1, b2):
                return True
    return False


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    o1 = _orientation(a1, a2, b1)
    o2 = _orientation(a1, a2, b2)
    o3 = _orientation(b1, b2, a1)
    o4 = _orientation(b1, b2, a2)
    if o1 * o2 < -_GEOMETRY_TOLERANCE and o3 * o4 < -_GEOMETRY_TOLERANCE:
        return True
    if abs(o1) <= _GEOMETRY_TOLERANCE and _on_segment(a1, b1, a2):
        return True
    if abs(o2) <= _GEOMETRY_TOLERANCE and _on_segment(a1, b2, a2):
        return True
    if abs(o3) <= _GEOMETRY_TOLERANCE and _on_segment(b1, a1, b2):
        return True
    if abs(o4) <= _GEOMETRY_TOLERANCE and _on_segment(b1, a2, b2):
        return True
    return False


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
    return (
        min(a[0], c[0]) - _GEOMETRY_TOLERANCE <= b[0] <= max(a[0], c[0]) + _GEOMETRY_TOLERANCE
        and min(a[1], c[1]) - _GEOMETRY_TOLERANCE <= b[1] <= max(a[1], c[1]) + _GEOMETRY_TOLERANCE
    )


def _same_point(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= _GEOMETRY_TOLERANCE and abs(a[1] - b[1]) <= _GEOMETRY_TOLERANCE
