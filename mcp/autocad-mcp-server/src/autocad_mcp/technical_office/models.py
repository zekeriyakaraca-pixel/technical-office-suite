"""Data contracts for technical office plate production."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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
            if relief.radius * 2 >= min(self.width, self.height):
                errors.append(f"corner relief {index} radius is too large for plate bounds")
            if relief.relief_type not in {"round", "cugul", "chamfer", "pah"}:
                errors.append(f"corner relief {index} has unsupported type: {relief.relief_type}")
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
