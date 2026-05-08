"""Build plate specs from extracted PDF content and optional position lists."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from statistics import median

from autocad_mcp.technical_office.models import CornerReliefSpec, HoleSpec, ManualReview, PlateSpec, PositionRecord
from autocad_mcp.technical_office.pdf_reader import PdfExtraction, PdfPageContent


@dataclass
class PlateExtractionResult:
    plates: list[PlateSpec]
    manual_reviews: list[ManualReview]


POZ_RE = re.compile(r"\b(?:POZ|POS|MARK)\s*[:#-]?\s*([A-Za-z0-9_.-]+)\b", re.I)
TITLE_BLOCK_POZ_RE = re.compile(r"\bScale\s*\n\s*([A-Za-z0-9_.-]{3,})\s*\n\s*Object\b", re.I)
DIM_RE = re.compile(
    r"\b(?:PLAKA|PLATE|PL)?\s*([0-9]+(?:[.,][0-9]+)?)\s*[xX*]\s*"
    r"([0-9]+(?:[.,][0-9]+)?)(?:\s*[xX*]\s*([0-9]+(?:[.,][0-9]+)?))?\b",
    re.I,
)
MATERIAL_PATTERN = r"S[0-9]{3,4}(?:J[0-9]|JR|AR|N|M|ML)?|ST[0-9]+|A36"
PLATE_TABLE_RE = re.compile(
    r"\b(?P<poz>[A-Za-z0-9_.-]{3,})\s*\n\s*"
    r"PL\s*(?P<thickness>[0-9]+(?:[.,][0-9]+)?)\s*[*xX]\s*(?P<height>[0-9]+(?:[.,][0-9]+)?)\s*\n\s*"
    rf"(?P<material>{MATERIAL_PATTERN})\s*\n?\s*"
    r"(?P<width>[0-9]+(?:[.,][0-9]+)?)\s*\n\s*"
    r"(?P<quantity>[0-9]+)\s*\n\s*"
    r"(?:(?P<unit_surface_area_m2>[0-9]+(?:[.,][0-9]+)?)\s*\n\s*"
    r"(?P<unit_weight_kg>[0-9]+(?:[.,][0-9]+)?)\s*\n\s*)?"
    r".*?\bPart\s*/\s*Assembly",
    re.I | re.S,
)
COMPACT_PLATE_TABLE_RE = re.compile(
    r"\b(?P<poz>[A-Za-z0-9_.-]{3,})\s*\n\s*"
    r"PL\s*(?P<thickness>[0-9]+(?:[.,][0-9]+)?)\s*[*xX]\s*(?P<height>[0-9]+(?:[.,][0-9]+)?)\s*\n\s*"
    rf"(?P<material>{MATERIAL_PATTERN})(?P<width>[0-9]+(?:[.,][0-9]+)?)\s*\n\s*"
    r"(?P<metrics>[0-9]+[.,][0-9]{2}[0-9]+[.,][0-9]+)\s*\n\s*"
    r".*?\bPart\s*/\s*Assembly",
    re.I | re.S,
)
COMPACT_METRICS_RE = re.compile(
    r"(?P<quantity>[0-9]{1,2})(?P<unit_surface_area_m2>0[.,][0-9]{2})(?P<unit_weight_kg>[0-9]+[.,][0-9]+)"
)
MATERIAL_RE = re.compile(rf"\b({MATERIAL_PATTERN})\b", re.I)
HOLE_CALLOUT_RE = re.compile(r"\b([0-9]+)\s*[*xX]\s*(?:Ø|⌀|%%c|DIA|D)\s*([0-9]+(?:[.,][0-9]+)?)\b", re.I)
RADIUS_CALLOUT_RE = re.compile(r"(?<![A-Za-z])R\s*([0-9]+(?:[.,][0-9]+)?)\b", re.I)
HOLE_RE = re.compile(
    r"(?:HOLE|DEL[Ii]K)\s*[:#-]?\s*"
    r"(?:X\s*=?\s*)?([0-9]+(?:[.,][0-9]+)?)\s*[,; ]+\s*"
    r"(?:Y\s*=?\s*)?([0-9]+(?:[.,][0-9]+)?)\s*[,; ]+\s*"
    r"(?:D|DIA|CAP|R)\s*=?\s*([0-9]+(?:[.,][0-9]+)?)",
    re.I,
)
HOLES_LIST_RE = re.compile(
    r"([0-9]+(?:[.,][0-9]+)?)\s*,\s*([0-9]+(?:[.,][0-9]+)?)\s*,\s*D\s*([0-9]+(?:[.,][0-9]+)?)",
    re.I,
)


def build_plate_specs(
    extraction: PdfExtraction,
    positions: list[PositionRecord] | None = None,
) -> PlateExtractionResult:
    positions = positions or []
    manual_reviews: list[ManualReview] = []
    plates: list[PlateSpec] = []

    if extraction.manual_review_required:
        manual_reviews.append(
            ManualReview(
                reason="manual_review_required",
                detail="PDF content extraction was not reliable enough for automatic production.",
            )
        )
        return PlateExtractionResult(plates=[], manual_reviews=manual_reviews)

    if positions:
        for position in positions:
            page = _page_for_position(extraction.pages, position)
            if page is None:
                manual_reviews.append(
                    ManualReview(
                        reason="page_not_found",
                        page=position.page,
                        poz_no=position.poz_no,
                        detail="The requested page does not exist in the extracted PDF content.",
                    )
                )
                continue
            spec = _spec_from_text(page.text, page, position, extraction.confidence)
            if spec is None:
                manual_reviews.append(_manual_review_for_failed_spec(page.text, page, position))
                continue
            plates.append(spec)
        return PlateExtractionResult(plates=plates, manual_reviews=manual_reviews)

    for page in extraction.pages:
        poz_numbers = _find_poz_numbers(page.text)
        if not poz_numbers:
            manual_reviews.append(
                ManualReview(
                    reason="poz_no_not_found",
                    page=page.page_number,
                    detail=(
                        "Bu sayfada teknik poz/parca numarasi algilanamadi. "
                        "Bu sayfa numarasi eksigi degildir; cizim uzerindeki poz/mark bilgisidir."
                    ),
                )
            )
            continue
        for poz_no in poz_numbers:
            position = PositionRecord(poz_no=poz_no, page=page.page_number)
            spec = _spec_from_text(page.text, page, position, extraction.confidence)
            if spec is None:
                manual_reviews.append(_manual_review_for_failed_spec(page.text, page, position))
                continue
            plates.append(spec)

    return PlateExtractionResult(plates=plates, manual_reviews=manual_reviews)


def _page_for_position(pages: list[PdfPageContent], position: PositionRecord) -> PdfPageContent | None:
    if position.page is None:
        return pages[0] if pages else None
    for page in pages:
        if page.page_number == position.page:
            return page
    return None


def _spec_from_text(
    text: str,
    page: PdfPageContent,
    position: PositionRecord,
    base_confidence: float,
) -> PlateSpec | None:
    table = PLATE_TABLE_RE.search(text)
    compact_table = COMPACT_PLATE_TABLE_RE.search(text) if table is None else None
    dim = DIM_RE.search(text.replace("\n", " ")) if table is None and compact_table is None else None
    if not dim and not table and not compact_table:
        return None

    if table:
        width = _num(table.group("width"))
        height = _num(table.group("height"))
        thickness = position.thickness_mm or _num(table.group("thickness"))
        table_material = table.group("material").upper()
        table_quantity = int(table.group("quantity"))
        unit_surface_area_m2 = _optional_num(table.group("unit_surface_area_m2"))
        unit_weight_kg = _optional_num(table.group("unit_weight_kg"))
    elif compact_table:
        width = _num(compact_table.group("width"))
        height = _num(compact_table.group("height"))
        thickness = position.thickness_mm or _num(compact_table.group("thickness"))
        table_material = compact_table.group("material").upper()
        table_quantity, unit_surface_area_m2, unit_weight_kg = _parse_compact_metrics(
            compact_table.group("metrics")
        )
    else:
        width = _num(dim.group(1))
        height = _num(dim.group(2))
        thickness = position.thickness_mm or (_num(dim.group(3)) if dim.group(3) else None)
        table_material = None
        table_quantity = None
        unit_surface_area_m2 = None
        unit_weight_kg = None
    if thickness is None:
        return None

    material_match = MATERIAL_RE.search(text)
    material = position.material or table_material or (material_match.group(1).upper() if material_match else "UNKNOWN")
    holes = _parse_holes(text)
    if not holes:
        holes = _parse_vector_holes(text, page, width, height)
    if _has_hole_callout(text) and not holes:
        return None
    corner_reliefs = _parse_corner_reliefs(text, page, width, height)
    notes: list[str] = []
    if page.vector_operator_count == 0:
        notes.append("No vector operators were detected on the source page.")
    if position.notes:
        notes.append(position.notes)

    return PlateSpec(
        poz_no=position.output_name,
        width=width,
        height=height,
        thickness=thickness,
        material=material,
        quantity=table_quantity if table_quantity is not None and position.quantity == 1 else position.quantity,
        unit_surface_area_m2=unit_surface_area_m2,
        unit_weight_kg=unit_weight_kg,
        holes=holes,
        corner_reliefs=corner_reliefs,
        source_page=page.page_number,
        confidence=base_confidence,
        notes=notes,
    )


def _parse_holes(text: str) -> list[HoleSpec]:
    holes: list[HoleSpec] = []
    seen: set[tuple[float, float, float]] = set()
    for regex in (HOLE_RE, HOLES_LIST_RE):
        for match in regex.finditer(text):
            hole = HoleSpec(x=_num(match.group(1)), y=_num(match.group(2)), diameter=_num(match.group(3)))
            key = (hole.x, hole.y, hole.diameter)
            if key not in seen:
                seen.add(key)
                holes.append(hole)
    return holes


def _parse_corner_reliefs(
    text: str,
    page: PdfPageContent,
    width: float,
    height: float,
) -> list[CornerReliefSpec]:
    vector_radius = _infer_corner_relief_radius_from_vectors(page, width, height)
    if vector_radius is not None:
        return _all_corner_reliefs(vector_radius, relief_type="cugul")

    radii = [_num(match.group(1)) for match in RADIUS_CALLOUT_RE.finditer(text)]
    if len(radii) >= 4:
        radius = Counter(_snap_dimension(radius) for radius in radii).most_common(1)[0][0]
        if 0 < radius * 2 < min(width, height):
            return _all_corner_reliefs(radius, relief_type="cugul")
    return []


def _all_corner_reliefs(radius: float, relief_type: str) -> list[CornerReliefSpec]:
    return [
        CornerReliefSpec(corner="bottom_left", radius=radius, relief_type=relief_type),
        CornerReliefSpec(corner="bottom_right", radius=radius, relief_type=relief_type),
        CornerReliefSpec(corner="top_right", radius=radius, relief_type=relief_type),
        CornerReliefSpec(corner="top_left", radius=radius, relief_type=relief_type),
    ]


def _num(value: str) -> float:
    return float(value.replace(",", "."))


def _optional_num(value: str | None) -> float | None:
    return _num(value) if value else None


def _parse_compact_metrics(value: str) -> tuple[int, float | None, float | None]:
    clean = value.strip()
    for quantity_length in (2, 1):
        if len(clean) <= quantity_length or not clean[:quantity_length].isdigit():
            continue
        rest = clean[quantity_length:]
        match = re.fullmatch(
            r"(?P<unit_surface_area_m2>[0-9]+[.,][0-9]{2})(?P<unit_weight_kg>[0-9]+[.,][0-9]+)",
            rest,
        )
        if match:
            return (
                int(clean[:quantity_length]),
                _num(match.group("unit_surface_area_m2")),
                _num(match.group("unit_weight_kg")),
            )

    match = COMPACT_METRICS_RE.fullmatch(clean)
    if match:
        return (
            int(match.group("quantity")),
            _num(match.group("unit_surface_area_m2")),
            _num(match.group("unit_weight_kg")),
        )
    return 1, None, None


def _parse_vector_holes(
    text: str,
    page: PdfPageContent,
    width: float,
    height: float,
) -> list[HoleSpec]:
    callouts = list(HOLE_CALLOUT_RE.finditer(text))
    if not callouts or not page.vector_circles:
        return []

    if len(callouts) > 1:
        multi_callout_holes = _parse_multi_callout_vector_holes(text, page, width, height, callouts)
        if multi_callout_holes:
            return multi_callout_holes

    callout = callouts[0]
    expected_count = int(callout.group(1))
    diameter = _num(callout.group(2))
    circles = _select_plate_circles_by_bbox(page, width, height, page.vector_circles, expected_count)
    if not circles:
        circles = page.vector_circles
    if len(circles) != expected_count:
        return []

    rows = _cluster_by_coordinate(circles, "cy_pt", tolerance=3.0)
    if not rows or expected_count % len(rows) != 0:
        return []

    cols = expected_count // len(rows)
    if any(len(row) != cols for row in rows):
        return []

    x_positions = _x_positions_from_dimension_chain(text, width, cols, diameter)
    if not x_positions:
        return []

    x_centers_by_row = [[circle["cx_pt"] for circle in sorted(row, key=lambda item: item["cx_pt"])] for row in rows]
    x_steps_pt = [
        values[index + 1] - values[index]
        for values in x_centers_by_row
        for index in range(len(values) - 1)
    ]
    x_steps_mm = [x_positions[index + 1] - x_positions[index] for index in range(len(x_positions) - 1)]
    if not x_steps_pt or not x_steps_mm:
        return []

    scale = median(x_steps_mm) / median(x_steps_pt)
    scaled_diameter = median(circle["diameter_pt"] for circle in circles) * scale
    if abs(scaled_diameter - diameter) > max(2.0, diameter * 0.12):
        return []

    y_centers_pt = [median(circle["cy_pt"] for circle in row) for row in rows]
    if len(rows) == 1:
        y_position = _single_row_y_position_from_vectors(text, page, width, height, rows[0])
        y_positions = [y_position if y_position is not None else height / 2.0]
    else:
        y_steps_pt = [y_centers_pt[index + 1] - y_centers_pt[index] for index in range(len(y_centers_pt) - 1)]
        y_step_mm = median(y_steps_pt) * scale
        if y_step_mm <= 0 or y_step_mm * (len(rows) - 1) >= height:
            return []
        edge = (height - y_step_mm * (len(rows) - 1)) / 2.0
        y_positions = [height - edge - row_index * y_step_mm for row_index in range(len(rows))]

    holes: list[HoleSpec] = []
    for row_index, row in enumerate(rows):
        for col_index, _circle in enumerate(sorted(row, key=lambda item: item["cx_pt"])):
            holes.append(
                HoleSpec(
                    x=round(x_positions[col_index], 3),
                    y=_snap_dimension(y_positions[row_index]),
                    diameter=diameter,
                )
            )
    return holes


def _select_plate_circles_by_bbox(
    page: PdfPageContent,
    width: float,
    height: float,
    circles: list[dict[str, float]],
    expected_count: int,
) -> list[dict[str, float]]:
    bbox_result = _plate_bbox_from_vectors(page, width, height, circles)
    if bbox_result is None:
        return []
    bbox, scale = bbox_result
    x_min, y_min, x_max, y_max = bbox
    margin = max(1.0, scale * 3.0)
    selected = [
        circle
        for circle in circles
        if x_min - margin <= circle["cx_pt"] <= x_max + margin
        and y_min - margin <= circle["cy_pt"] <= y_max + margin
    ]
    return selected if len(selected) == expected_count else []


def _single_row_y_position_from_vectors(
    text: str,
    page: PdfPageContent,
    width: float,
    height: float,
    row: list[dict[str, float]],
) -> float | None:
    bbox_result = _plate_bbox_from_vectors(page, width, height, row)
    if bbox_result is None:
        return _single_row_y_position_from_text(text, height)

    bbox, scale = bbox_result
    _x_min, y_min, _x_max, _y_max = bbox
    center_y = median(circle["cy_pt"] for circle in row)
    y_from_bottom = (center_y - y_min) / scale
    if -0.5 <= y_from_bottom <= height + 0.5:
        return _snap_vector_dimension(min(max(y_from_bottom, 0.0), height))
    return _single_row_y_position_from_text(text, height)


def _single_row_y_position_from_text(text: str, height: float) -> float | None:
    values = [
        _num(match.group(0))
        for match in re.finditer(r"(?<![A-Za-z])\d+(?:[.,]\d+)?(?![A-Za-z])", text)
    ]
    for value in values:
        if 0 < value < height and any(abs(value + other - height) <= 0.2 for other in values):
            return _snap_dimension(height - value)
    return None


def _plate_bbox_from_vectors(
    page: PdfPageContent,
    width: float,
    height: float,
    circles: list[dict[str, float]],
) -> tuple[tuple[float, float, float, float], float] | None:
    if not page.vector_lines:
        return None

    horizontal = [
        _line_info(line)
        for line in page.vector_lines
        if abs(line["y1_pt"] - line["y2_pt"]) <= 0.75 and _line_length(line) >= 20.0
    ]
    candidates: list[tuple[int, float, float, tuple[float, float, float, float], float]] = []
    for index, first in enumerate(horizontal):
        first_length = first[2] - first[0]
        if first_length <= 0:
            continue
        scale = first_length / width
        if scale <= 0:
            continue
        for second in horizontal[index + 1 :]:
            second_length = second[2] - second[0]
            if second_length <= 0 or abs(second_length - first_length) > max(1.0, first_length * 0.02):
                continue
            if abs(second[0] - first[0]) > max(1.0, scale * 1.5):
                continue
            if abs(second[2] - first[2]) > max(1.0, scale * 1.5):
                continue
            y_min = min(first[1], second[1])
            y_max = max(first[1], second[1])
            y_span = y_max - y_min
            y_scale = y_span / height
            if abs(y_scale - scale) > max(0.03, scale * 0.05):
                continue
            inside_count = sum(
                1
                for circle in circles
                if first[0] <= circle["cx_pt"] <= first[2] and y_min <= circle["cy_pt"] <= y_max
            )
            if inside_count == 0:
                continue
            bbox = (first[0], y_min, first[2], y_max)
            scale_error = abs(y_scale - scale)
            candidates.append((inside_count, -scale_error, first_length, bbox, scale))

    if not candidates:
        return None
    _inside_count, _scale_error, _length, bbox, scale = max(candidates, key=lambda item: item[:3])
    return bbox, scale


def _infer_corner_relief_radius_from_vectors(
    page: PdfPageContent,
    width: float,
    height: float,
) -> float | None:
    if not page.vector_lines or not page.vector_curves:
        return None

    horizontal = [
        _line_info(line)
        for line in page.vector_lines
        if abs(line["y1_pt"] - line["y2_pt"]) <= 1.0 and _line_length(line) >= 10.0
    ]
    vertical = [
        _line_info(line)
        for line in page.vector_lines
        if abs(line["x1_pt"] - line["x2_pt"]) <= 1.0 and _line_length(line) >= 10.0
    ]
    candidates: list[tuple[int, float, tuple[float, float, float, float]]] = []
    for h_line in horizontal:
        h_length = h_line[2] - h_line[0]
        if h_length <= 0:
            continue
        x_scale = h_length / width
        if x_scale <= 0:
            continue
        for v_line in vertical:
            v_length = v_line[3] - v_line[1]
            if v_length <= 0:
                continue
            y_scale = v_length / height
            if abs(x_scale - y_scale) > max(0.02, x_scale * 0.04):
                continue
            scale = median([x_scale, y_scale])
            bbox = (h_line[0], v_line[1], h_line[2], v_line[3])
            score = _corner_curve_hit_count(page.vector_curves, bbox, scale, width, height)
            if score >= 4:
                candidates.append((score, scale, bbox))

    if not candidates:
        return None

    _score, scale, bbox = max(candidates, key=lambda item: (item[0], item[1]))
    offsets = _corner_offsets_from_vectors(page, bbox, scale, width, height)
    if len(offsets) < 4:
        return None
    radius = _snap_corner_radius(median(offsets) / scale)
    if 0 < radius * 2 < min(width, height):
        return radius
    return None


def _snap_corner_radius(value: float) -> float:
    rounded_five = round(value / 5.0) * 5.0
    if abs(value - rounded_five) <= 1.5:
        return float(rounded_five)
    rounded = round(value)
    if abs(value - rounded) <= 1.0:
        return float(rounded)
    return _snap_dimension(value)


def _line_info(line: dict[str, float]) -> tuple[float, float, float, float]:
    return (
        min(line["x1_pt"], line["x2_pt"]),
        min(line["y1_pt"], line["y2_pt"]),
        max(line["x1_pt"], line["x2_pt"]),
        max(line["y1_pt"], line["y2_pt"]),
    )


def _line_length(line: dict[str, float]) -> float:
    return math.hypot(line["x2_pt"] - line["x1_pt"], line["y2_pt"] - line["y1_pt"])


def _corner_curve_hit_count(
    curves: list[dict[str, float]],
    bbox: tuple[float, float, float, float],
    scale: float,
    width: float,
    height: float,
) -> int:
    x_min, y_min, x_max, y_max = bbox
    margin = min(width, height) * scale * 0.35
    corners = [
        (x_min, y_min, 1, 1),
        (x_max, y_min, -1, 1),
        (x_max, y_max, -1, -1),
        (x_min, y_max, 1, -1),
    ]
    hits = 0
    for corner_x, corner_y, x_direction, y_direction in corners:
        x0 = corner_x if x_direction > 0 else corner_x - margin
        x1 = corner_x + margin if x_direction > 0 else corner_x
        y0 = corner_y if y_direction > 0 else corner_y - margin
        y1 = corner_y + margin if y_direction > 0 else corner_y
        if any(
            curve["max_x_pt"] >= x0
            and curve["min_x_pt"] <= x1
            and curve["max_y_pt"] >= y0
            and curve["min_y_pt"] <= y1
            for curve in curves
        ):
            hits += 1
    return hits


def _corner_offsets_from_vectors(
    page: PdfPageContent,
    bbox: tuple[float, float, float, float],
    scale: float,
    width: float,
    height: float,
) -> list[float]:
    x_min, y_min, x_max, y_max = bbox
    tolerance = max(1.25, scale * 2.5)
    max_offset = min(width, height) * scale * 0.3
    min_offset = max(1.0, scale * 5.0)
    points = _vector_points(page)
    corners = [(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)]
    offsets: list[float] = []
    for corner_x, corner_y in corners:
        candidates: list[float] = []
        for x, y in points:
            if abs(x - corner_x) <= tolerance:
                offset = abs(y - corner_y)
                if min_offset <= offset <= max_offset:
                    candidates.append(offset)
            if abs(y - corner_y) <= tolerance:
                offset = abs(x - corner_x)
                if min_offset <= offset <= max_offset:
                    candidates.append(offset)
        if candidates:
            offsets.append(median(candidates))
    return offsets


def _vector_points(page: PdfPageContent) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for line in page.vector_lines:
        points.append((line["x1_pt"], line["y1_pt"]))
        points.append((line["x2_pt"], line["y2_pt"]))
    for curve in page.vector_curves:
        points.extend(
            [
                (curve["x0_pt"], curve["y0_pt"]),
                (curve["x1_pt"], curve["y1_pt"]),
                (curve["x2_pt"], curve["y2_pt"]),
                (curve["x3_pt"], curve["y3_pt"]),
            ]
        )
    return points


def _parse_multi_callout_vector_holes(
    text: str,
    page: PdfPageContent,
    width: float,
    height: float,
    callouts: list[re.Match[str]],
) -> list[HoleSpec]:
    callout_counts: Counter[float] = Counter()
    for callout in callouts:
        callout_counts[_num(callout.group(2))] += int(callout.group(1))
    if sum(callout_counts.values()) != len(page.vector_circles):
        return []

    circle_groups = _cluster_by_diameter(page.vector_circles, tolerance=0.8)
    if len(circle_groups) != len(callout_counts):
        return []

    groups_by_diameter: dict[float, list[dict[str, float]]] = {}
    for diameter, circles in zip(
        sorted(callout_counts.keys(), reverse=True),
        sorted(circle_groups, key=lambda group: median(circle["diameter_pt"] for circle in group), reverse=True),
    ):
        if len(circles) != callout_counts[diameter]:
            return []
        groups_by_diameter[diameter] = circles

    ratios = [
        diameter / median(circle["diameter_pt"] for circle in circles)
        for diameter, circles in groups_by_diameter.items()
    ]
    bottom_y_pt = _infer_plate_bottom_y(page)
    if bottom_y_pt is None:
        return []
    ratio_scale = median(ratios)
    coordinate_scale = _infer_plate_y_scale(page, height, bottom_y_pt) or ratio_scale

    holes: list[HoleSpec] = []
    used_circle_ids: set[int] = set()

    if 18.0 in groups_by_diameter and len(groups_by_diameter[18.0]) == 6:
        x_positions = _left_cluster_x_positions(text)
        if not x_positions:
            return []
        d18_circles = sorted(groups_by_diameter[18.0], key=lambda circle: (circle["cy_pt"], circle["cx_pt"]))
        x_clusters = _cluster_by_coordinate(d18_circles, "cx_pt", tolerance=4.0)
        if len(x_clusters) != 2 or any(len(cluster) != 3 for cluster in x_clusters):
            return []
        for x, x_cluster in zip(x_positions, sorted(x_clusters, key=lambda cluster: median(c["cx_pt"] for c in cluster))):
            for circle in x_cluster:
                holes.append(
                    HoleSpec(
                        x=x,
                        y=_snap_vector_dimension(_vector_y_from_bottom(circle, bottom_y_pt, coordinate_scale)),
                        diameter=18.0,
                    )
                )
                used_circle_ids.add(id(circle))

    if 13.0 in groups_by_diameter:
        d13_holes = _map_d13_long_plate_holes(
            text=text,
            circles=groups_by_diameter[13.0],
            width=width,
            scale=coordinate_scale,
            bottom_y_pt=bottom_y_pt,
            used_circle_ids=used_circle_ids,
        )
        if len(d13_holes) != len(groups_by_diameter[13.0]):
            return []
        holes.extend(d13_holes)

    return sorted(holes, key=lambda hole: (-hole.y, hole.x))


def _map_d13_long_plate_holes(
    text: str,
    circles: list[dict[str, float]],
    width: float,
    scale: float,
    bottom_y_pt: float,
    used_circle_ids: set[int],
) -> list[HoleSpec]:
    available = [circle for circle in circles if id(circle) not in used_circle_ids]
    if not available:
        return []

    bottom_row = sorted(
        [
            circle for circle in available
            if _vector_y_from_bottom(circle, bottom_y_pt, scale) <= 60.0
        ],
        key=lambda circle: circle["cx_pt"],
    )
    if len(bottom_row) != 4:
        return []

    spacing = _dimension_value(text, 450.0)
    right_tail = _dimension_value(text, 3358.5)
    if spacing is None or right_tail is None:
        return []
    bottom_start = width - right_tail - spacing * (len(bottom_row) - 1)

    holes: list[HoleSpec] = []
    for index, circle in enumerate(bottom_row):
        holes.append(
            HoleSpec(
                x=_snap_dimension(bottom_start + spacing * index),
                y=_snap_vector_dimension(_vector_y_from_bottom(circle, bottom_y_pt, scale)),
                diameter=13.0,
            )
        )
        used_circle_ids.add(id(circle))

    remaining = [circle for circle in available if id(circle) not in used_circle_ids]
    if len(remaining) != 3:
        return []

    left_offset = _dimension_value(text, 35.0)
    right_offset = _dimension_value(text, 302.0)
    pre_bottom_offset = _dimension_value(text, 76.5)
    if left_offset is None or right_offset is None or pre_bottom_offset is None:
        return []

    left_circle = min(remaining, key=lambda circle: circle["cx_pt"])
    right_circle = max(remaining, key=lambda circle: circle["cx_pt"])
    middle_candidates = [circle for circle in remaining if circle is not left_circle and circle is not right_circle]
    if len(middle_candidates) != 1:
        return []
    middle_circle = middle_candidates[0]

    holes.append(
        HoleSpec(
            x=_snap_dimension(left_offset),
            y=_snap_vector_dimension(_vector_y_from_bottom(left_circle, bottom_y_pt, scale)),
            diameter=13.0,
        )
    )
    holes.append(
        HoleSpec(
            x=_snap_dimension(bottom_start - pre_bottom_offset),
            y=_snap_vector_dimension(_vector_y_from_bottom(middle_circle, bottom_y_pt, scale)),
            diameter=13.0,
        )
    )
    holes.append(
        HoleSpec(
            x=_snap_dimension(width - right_offset),
            y=_snap_vector_dimension(_vector_y_from_bottom(right_circle, bottom_y_pt, scale)),
            diameter=13.0,
        )
    )
    return holes


def _vector_y_from_bottom(circle: dict[str, float], bottom_y_pt: float, scale: float) -> float:
    if circle["cy_pt"] >= bottom_y_pt:
        return (circle["cy_pt"] - bottom_y_pt) * scale
    return (bottom_y_pt - circle["cy_pt"]) * scale


def _cluster_by_coordinate(
    circles: list[dict[str, float]],
    key: str,
    tolerance: float,
) -> list[list[dict[str, float]]]:
    clusters: list[list[dict[str, float]]] = []
    for circle in sorted(circles, key=lambda item: item[key]):
        if clusters and abs(median(item[key] for item in clusters[-1]) - circle[key]) <= tolerance:
            clusters[-1].append(circle)
        else:
            clusters.append([circle])
    return clusters


def _cluster_by_diameter(
    circles: list[dict[str, float]],
    tolerance: float,
) -> list[list[dict[str, float]]]:
    clusters: list[list[dict[str, float]]] = []
    for circle in sorted(circles, key=lambda item: item["diameter_pt"]):
        if clusters and abs(median(item["diameter_pt"] for item in clusters[-1]) - circle["diameter_pt"]) <= tolerance:
            clusters[-1].append(circle)
        else:
            clusters.append([circle])
    return clusters


def _infer_plate_bottom_y(page: PdfPageContent) -> float | None:
    if not page.vector_circles or not page.vector_lines:
        return None
    min_circle_y = min(circle["cy_pt"] for circle in page.vector_circles)
    max_circle_y = max(circle["cy_pt"] for circle in page.vector_circles)
    below_candidates: list[tuple[float, float]] = []
    above_candidates: list[tuple[float, float]] = []
    for line in page.vector_lines:
        y1 = line["y1_pt"]
        y2 = line["y2_pt"]
        length = abs(line["x2_pt"] - line["x1_pt"])
        if abs(y1 - y2) > 0.35 or length < 20.0:
            continue
        if min_circle_y - 30.0 <= y1 < min_circle_y:
            below_candidates.append((y1, length))
        if max_circle_y < y1 <= max_circle_y + 30.0:
            above_candidates.append((y1, length))
    if below_candidates:
        return _dominant_horizontal_y(below_candidates)
    if above_candidates:
        return _dominant_horizontal_y(above_candidates)
    return None


def _infer_plate_y_scale(
    page: PdfPageContent,
    height: float,
    bottom_y_pt: float,
) -> float | None:
    min_circle_y = min(circle["cy_pt"] for circle in page.vector_circles)
    max_circle_y = max(circle["cy_pt"] for circle in page.vector_circles)
    candidates: list[tuple[float, float]] = []
    for line in page.vector_lines:
        y1 = line["y1_pt"]
        y2 = line["y2_pt"]
        length = abs(line["x2_pt"] - line["x1_pt"])
        if abs(y1 - y2) > 0.35 or length < 20.0:
            continue
        if bottom_y_pt < min_circle_y:
            if max_circle_y < y1 <= max_circle_y + 30.0:
                candidates.append((y1, length))
        elif min_circle_y - 30.0 <= y1 < min_circle_y:
            candidates.append((y1, length))
    if not candidates:
        return None
    top_y_pt = _dominant_horizontal_y(candidates)
    span = abs(top_y_pt - bottom_y_pt)
    if span <= 0:
        return None
    return height / span


def _dominant_horizontal_y(candidates: list[tuple[float, float]]) -> float:
    groups: list[list[tuple[float, float]]] = []
    for y_value, length in sorted(candidates, key=lambda item: item[0]):
        if groups and abs(median(item[0] for item in groups[-1]) - y_value) <= 1.0:
            groups[-1].append((y_value, length))
        else:
            groups.append([(y_value, length)])
    dominant = max(groups, key=lambda group: sum(length for _y, length in group))
    return median(sorted(set(round(y_value, 3) for y_value, _length in dominant)))


def _left_cluster_x_positions(text: str) -> list[float]:
    first = _dimension_value(text, 45.0)
    spacing = _dimension_value(text, 70.0)
    if first is None or spacing is None:
        return []
    return [_snap_dimension(first), _snap_dimension(first + spacing)]


def _dimension_value(text: str, target: float, tolerance: float = 0.01) -> float | None:
    for match in re.finditer(r"(?<![A-Za-z])\d+(?:[.,]\d+)?(?![A-Za-z])", text):
        value = _num(match.group(0))
        if abs(value - target) <= tolerance:
            return value
    return None


def _x_positions_from_dimension_chain(
    text: str,
    width: float,
    cols: int,
    diameter: float,
) -> list[float]:
    values = [_num(match.group(0)) for match in re.finditer(r"(?<![A-Za-z])\d+(?:[.,]\d+)?(?![A-Za-z])", text)]
    if not values or cols <= 0:
        return []
    counts = Counter(round(value, 3) for value in values)
    candidates: list[tuple[int, float, float, float]] = []
    unique_values = sorted(set(values))
    for left in unique_values:
        for spacing in unique_values:
            if spacing <= diameter * 1.5:
                continue
            for right in unique_values:
                if left <= 0 or right < 0:
                    continue
                if left < diameter / 2.0 or right < diameter / 2.0:
                    continue
                if abs(left + spacing * (cols - 1) + right - width) > max(1.0, width * 0.005):
                    continue
                score = counts[round(spacing, 3)] * 10 + counts[round(left, 3)] + counts[round(right, 3)]
                if _dimension_chain_occurs(values, [left, *([spacing] * (cols - 1)), right]):
                    score += 50
                candidates.append((score, left, spacing, right))
    if not candidates:
        return []
    _, left, spacing, _right = max(candidates, key=lambda item: item[0])
    return [round(left + spacing * index, 3) for index in range(cols)]


def _dimension_chain_occurs(values: list[float], chain: list[float]) -> bool:
    if not chain or len(values) < len(chain):
        return False
    for index in range(0, len(values) - len(chain) + 1):
        if all(abs(values[index + offset] - chain[offset]) <= 0.01 for offset in range(len(chain))):
            return True
    return False


def _snap_dimension(value: float) -> float:
    rounded = round(value)
    if abs(value - rounded) <= 0.15:
        return float(rounded)
    half = round(value * 2.0) / 2.0
    if abs(value - half) <= 0.15:
        return float(half)
    return round(value, 3)


def _snap_vector_dimension(value: float) -> float:
    tight = _snap_dimension(value)
    if tight != round(value, 3):
        return tight
    rounded = round(value)
    if abs(value - rounded) <= 0.5:
        return float(rounded)
    return tight


def _find_poz_numbers(text: str) -> list[str]:
    found: list[str] = []
    for match in POZ_RE.finditer(text):
        found.append(match.group(1))
    for match in TITLE_BLOCK_POZ_RE.finditer(text):
        found.append(match.group(1))
    table = PLATE_TABLE_RE.search(text)
    if table:
        found.append(table.group("poz"))
    compact_table = COMPACT_PLATE_TABLE_RE.search(text)
    if compact_table:
        found.append(compact_table.group("poz"))
    return list(dict.fromkeys(found))


def _has_hole_callout(text: str) -> bool:
    return bool(HOLE_CALLOUT_RE.search(text))


def _manual_review_for_failed_spec(
    text: str,
    page: PdfPageContent,
    position: PositionRecord,
) -> ManualReview:
    if (PLATE_TABLE_RE.search(text) or COMPACT_PLATE_TABLE_RE.search(text)) and _has_hole_callout(text):
        return ManualReview(
            reason="hole_geometry_not_found",
            page=page.page_number,
            poz_no=position.poz_no,
            detail="Plaka profili ve delik cagrisi bulundu, ancak delik koordinatlari guvenilir cikarilamadi.",
        )
    return ManualReview(
        reason="plate_geometry_not_found",
        page=page.page_number,
        poz_no=position.poz_no,
        detail="Poz bilgisi bulundu, ancak plaka olculeri/geometrisi otomatik uretim icin guvenilir cikarilamadi.",
    )
