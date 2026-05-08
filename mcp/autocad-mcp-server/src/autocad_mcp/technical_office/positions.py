"""Read and validate manager-provided position lists."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from autocad_mcp.technical_office.models import PositionRecord


class PositionValidationError(ValueError):
    """Raised when a position list cannot be trusted."""


def load_position_records(job_dir: str | Path) -> list[PositionRecord]:
    """Load positions.csv or positions.json from a job directory."""

    root = Path(job_dir)
    csv_path = root / "positions.csv"
    json_path = root / "positions.json"
    if csv_path.exists():
        return load_positions_csv(csv_path)
    if json_path.exists():
        return load_positions_json(json_path)
    return []


def load_positions_csv(path: str | Path) -> list[PositionRecord]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    return [_record_from_mapping(row, index + 2) for index, row in enumerate(rows)]


def load_positions_json(path: str | Path) -> list[PositionRecord]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("positions", [])
    if not isinstance(data, list):
        raise PositionValidationError("positions.json must contain a list or an object with a positions list")
    return [_record_from_mapping(row, index + 1) for index, row in enumerate(data)]


def _record_from_mapping(row: dict[str, Any], row_number: int) -> PositionRecord:
    poz_no = str(row.get("poz_no") or "").strip()
    if not poz_no:
        raise PositionValidationError(f"row {row_number}: poz_no is required")

    page = _optional_int(row.get("page"), "page", row_number)
    quantity = _optional_int(row.get("quantity"), "quantity", row_number) or 1
    thickness = _optional_float(row.get("thickness_mm"), "thickness_mm", row_number)
    unit = _optional_str(row.get("unit") or row.get("units"))
    material = _optional_str(row.get("material"))
    name_override = _optional_str(row.get("name_override"))
    notes = _optional_str(row.get("notes"))

    if page is not None and page <= 0:
        raise PositionValidationError(f"row {row_number}: page must be positive")
    if quantity <= 0:
        raise PositionValidationError(f"row {row_number}: quantity must be positive")
    if thickness is not None and thickness <= 0:
        raise PositionValidationError(f"row {row_number}: thickness_mm must be positive")
    if unit is not None and unit.lower() != "mm":
        raise PositionValidationError(f"row {row_number}: unsupported unit: {unit}")

    return PositionRecord(
        poz_no=poz_no,
        page=page,
        quantity=quantity,
        thickness_mm=thickness,
        material=material,
        name_override=name_override,
        notes=notes,
    )


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
        raise PositionValidationError(f"row {row_number}: {field} must be an integer") from exc


def _optional_float(value: Any, field: str, row_number: int) -> float | None:
    text = _optional_str(value)
    if text is None:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError as exc:
        raise PositionValidationError(f"row {row_number}: {field} must be numeric") from exc
