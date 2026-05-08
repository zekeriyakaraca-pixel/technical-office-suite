"""Minimal PDF content extraction for plate jobs.

This module intentionally avoids mandatory PDF dependencies. It handles simple
vector PDFs with plain or Flate-compressed content streams and marks ambiguous
or scanned-like inputs for manual review.
"""

from __future__ import annotations

import re
import os
import sys
import zlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median


@dataclass
class PdfPageContent:
    page_number: int
    text: str
    vector_operator_count: int
    raw_length: int
    vector_circles: list[dict[str, float]] = field(default_factory=list)
    vector_lines: list[dict[str, float]] = field(default_factory=list)
    vector_curves: list[dict[str, float]] = field(default_factory=list)


@dataclass
class PdfExtraction:
    pages: list[PdfPageContent]
    manual_review_required: bool
    confidence: float
    notes: list[str]

    @property
    def all_text(self) -> str:
        return "\n".join(page.text for page in self.pages)


STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
STRING_RE = re.compile(r"\((?:\\.|[^\\()])*\)\s*(?:Tj|'|\")")
ARRAY_TEXT_RE = re.compile(r"\[(.*?)\]\s*TJ", re.S)
VECTOR_OP_RE = re.compile(r"(?<![A-Za-z])(?:m|l|c|v|y|h|re|S|s|f|F|B|b)(?![A-Za-z])")
PAGE_CONTENT_HINT_RE = re.compile(rb"/Type\s*/Page\b|/Contents\s+\d+\s+0\s+R")


def extract_pdf_content(path: str | Path) -> PdfExtraction:
    pdf_path = Path(path)
    pymupdf_extraction = _extract_with_pymupdf(pdf_path)
    if pymupdf_extraction is not None:
        return pymupdf_extraction

    raw = pdf_path.read_bytes()
    font_maps = _extract_tounicode_font_maps(raw)
    pages: list[PdfPageContent] = []
    notes: list[str] = []

    page_index = 0
    for match in STREAM_RE.finditer(raw):
        stream = _decode_stream(raw, match)
        header = raw[max(0, match.start() - 500) : match.start()]
        if _skip_non_page_stream(header, stream):
            continue
        text = _extract_text(stream, font_maps)
        vector_ops = len(VECTOR_OP_RE.findall(stream))
        vector_lines, vector_curves, vector_circles = _extract_vector_geometry(stream)
        if text.strip() or vector_ops:
            page_index += 1
            pages.append(
                PdfPageContent(
                    page_number=page_index,
                    text=text.strip(),
                    vector_operator_count=vector_ops,
                    raw_length=len(stream),
                    vector_circles=vector_circles,
                    vector_lines=vector_lines,
                    vector_curves=vector_curves,
                )
            )

    if not pages:
        return PdfExtraction(
            pages=[],
            manual_review_required=True,
            confidence=0.0,
            notes=["No readable text or vector streams found; scanned or encrypted PDF is likely."],
        )

    total_text = sum(len(page.text) for page in pages)
    total_vectors = sum(page.vector_operator_count for page in pages)
    if total_text == 0:
        return PdfExtraction(
            pages=pages,
            manual_review_required=True,
            confidence=0.25,
            notes=["Vector data exists but no readable position text was found."],
        )

    confidence = 0.9 if total_vectors else 0.65
    if not total_vectors:
        notes.append("Readable text found but vector operators were not detected; geometry confidence is limited.")
    return PdfExtraction(
        pages=pages,
        manual_review_required=False,
        confidence=confidence,
        notes=notes,
    )


def _extract_with_pymupdf(pdf_path: Path) -> PdfExtraction | None:
    try:
        import fitz  # type: ignore[import-not-found]
    except Exception:
        optional_path = os.environ.get("AUTOCAD_MCP_OPTIONAL_SITE_PACKAGES", "").strip()
        if not optional_path:
            return None
        if optional_path not in sys.path:
            sys.path.append(optional_path)
        try:
            import fitz  # type: ignore[import-not-found]
        except Exception:
            return None

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return None

    pages: list[PdfPageContent] = []
    for index, page in enumerate(doc, start=1):
        text = page.get_text("text").strip()
        drawings = page.get_drawings()
        circles = _extract_pymupdf_circles(drawings)
        lines = _extract_pymupdf_lines(drawings)
        curves = _extract_pymupdf_curves(drawings)
        if text or drawings:
            pages.append(
                PdfPageContent(
                    page_number=index,
                    text=text,
                    vector_operator_count=sum(len(drawing.get("items", [])) for drawing in drawings),
                    raw_length=len(text),
                    vector_circles=circles,
                    vector_lines=lines,
                    vector_curves=curves,
                )
            )

    if not pages:
        return PdfExtraction(
            pages=[],
            manual_review_required=True,
            confidence=0.0,
            notes=["No readable text or vector streams found; scanned or encrypted PDF is likely."],
        )

    total_text = sum(len(page.text) for page in pages)
    total_vectors = sum(page.vector_operator_count for page in pages)
    if total_text == 0:
        return PdfExtraction(
            pages=pages,
            manual_review_required=True,
            confidence=0.25,
            notes=["Vector data exists but no readable position text was found."],
        )

    confidence = 0.95 if total_vectors else 0.7
    notes = [] if total_vectors else ["Readable text found but vector operators were not detected; geometry confidence is limited."]
    return PdfExtraction(
        pages=pages,
        manual_review_required=False,
        confidence=confidence,
        notes=notes,
    )


def _extract_pymupdf_circles(drawings) -> list[dict[str, float]]:
    circles: list[dict[str, float]] = []
    for drawing in drawings:
        items = drawing.get("items", [])
        rect = drawing.get("rect")
        if not rect or drawing.get("type") != "s":
            continue
        if len(items) != 4 or not all(item[0] == "c" for item in items):
            continue
        width = float(rect.width)
        height = float(rect.height)
        if width <= 0 or abs(width - height) > max(0.5, width * 0.05):
            continue
        circles.append(
            {
                "cx_pt": float((rect.x0 + rect.x1) / 2),
                "cy_pt": float((rect.y0 + rect.y1) / 2),
                "diameter_pt": width,
            }
        )
    return circles


def _extract_pymupdf_lines(drawings) -> list[dict[str, float]]:
    lines: list[dict[str, float]] = []
    for drawing in drawings:
        for item in drawing.get("items", []):
            if item[0] != "l":
                continue
            start = item[1]
            end = item[2]
            lines.append(
                {
                    "x1_pt": float(start.x),
                    "y1_pt": float(start.y),
                    "x2_pt": float(end.x),
                    "y2_pt": float(end.y),
                }
            )
    return lines


def _extract_pymupdf_curves(drawings) -> list[dict[str, float]]:
    curves: list[dict[str, float]] = []
    for drawing in drawings:
        current: tuple[float, float] | None = None
        for item in drawing.get("items", []):
            if item[0] == "l":
                current = (float(item[2].x), float(item[2].y))
                continue
            if item[0] != "c":
                continue
            start_point = item[1]
            start = current or (float(start_point.x), float(start_point.y))
            c1 = item[2]
            c2 = item[3]
            end = item[4]
            curve = _curve_dict(
                start,
                (float(c1.x), float(c1.y)),
                (float(c2.x), float(c2.y)),
                (float(end.x), float(end.y)),
            )
            curves.append(curve)
            current = (float(end.x), float(end.y))
    return curves


def _decode_stream(raw: bytes, match: re.Match[bytes]) -> str:
    stream = match.group(1).strip(b"\r\n")
    header = raw[max(0, match.start() - 300) : match.start()]
    if b"/FlateDecode" in header:
        try:
            stream = zlib.decompress(stream)
        except zlib.error:
            pass
    for encoding in ("utf-8", "cp1254", "latin-1"):
        try:
            return stream.decode(encoding)
        except UnicodeDecodeError:
            continue
    return stream.decode("latin-1", errors="ignore")


def _skip_non_page_stream(header: bytes, stream: str) -> bool:
    if b"/FontFile" in header or b"/Subtype /Image" in header or b"/DCTDecode" in header:
        return True
    stripped = stream.lstrip()
    if stripped.startswith("/CIDInit") or stripped.startswith("\x00"):
        return True
    if PAGE_CONTENT_HINT_RE.search(header):
        return False
    return not any(operator in stream for operator in (" m", " l", " re", "BT", " cm"))


def _extract_tounicode_font_maps(raw: bytes) -> dict[str, dict[int, str]]:
    object_cache: dict[int, bytes] = {}

    def object_bytes(object_id: int) -> bytes:
        if object_id not in object_cache:
            match = re.search(rb"%d 0 obj(.*?)endobj" % object_id, raw, re.S)
            object_cache[object_id] = match.group(1) if match else b""
        return object_cache[object_id]

    unicode_maps_by_font_object: dict[int, dict[int, str]] = {}
    for match in re.finditer(rb"(\d+)\s+0\s+obj(.*?)endobj", raw, re.S):
        font_object_id = int(match.group(1))
        object_data = match.group(2)
        cmap_ref = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", object_data)
        if cmap_ref is None:
            continue
        cmap_object_id = int(cmap_ref.group(1))
        cmap_stream = _decode_object_stream(object_bytes(cmap_object_id))
        unicode_maps_by_font_object[font_object_id] = _parse_tounicode_cmap(cmap_stream)

    font_maps: dict[str, dict[int, str]] = {}
    for resource_match in re.finditer(rb"/(F\d+)\s+(\d+)\s+0\s+R", raw):
        font_name = resource_match.group(1).decode("ascii", errors="ignore")
        font_object_id = int(resource_match.group(2))
        if font_object_id in unicode_maps_by_font_object:
            font_maps[font_name] = unicode_maps_by_font_object[font_object_id]
    return font_maps


def _decode_object_stream(object_data: bytes) -> str:
    match = re.search(rb"stream\r?\n(.*?)\r?\nendstream", object_data, re.S)
    if not match:
        return object_data.decode("latin-1", errors="ignore")
    stream = match.group(1).strip(b"\r\n")
    if b"/FlateDecode" in object_data:
        try:
            stream = zlib.decompress(stream)
        except zlib.error:
            pass
    return stream.decode("latin-1", errors="ignore")


def _parse_tounicode_cmap(stream: str) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for source, target in re.findall(r"<([0-9A-Fa-f]{4})>\s*<([0-9A-Fa-f]{4})>", stream):
        mapping[int(source, 16)] = chr(int(target, 16))
    return mapping


def _extract_text(stream: str, font_maps: dict[str, dict[int, str]] | None = None) -> str:
    font_maps = font_maps or {}
    parts: list[str] = []
    parts.extend(_extract_encoded_text(stream, font_maps))
    for match in STRING_RE.finditer(stream):
        token = match.group(0)
        parts.append(_unescape_pdf_string(token[: token.rfind(")") + 1]))
    for array_match in ARRAY_TEXT_RE.finditer(stream):
        for string_match in re.finditer(r"\((?:\\.|[^\\()])*\)", array_match.group(1)):
            parts.append(_unescape_pdf_string(string_match.group(0)))
    return "\n".join(part for part in parts if part)


def _extract_encoded_text(stream: str, font_maps: dict[str, dict[int, str]]) -> list[str]:
    if not font_maps:
        return []
    parts: list[str] = []
    for block in re.findall(r"BT(.*?)ET", stream, re.S):
        font_match = re.search(r"/(F\d+)\s+[0-9.]+\s+Tf", block)
        font_name = font_match.group(1) if font_match else next(iter(font_maps))
        cmap = font_maps.get(font_name, {})
        for array_match in re.finditer(r"\[(.*?)\]\s*TJ", block, re.S):
            decoded = _decode_text_array(array_match.group(1), cmap)
            if decoded.strip():
                parts.append(decoded)
        for hex_match in re.finditer(r"<([0-9A-Fa-f]+)>\s*Tj", block):
            decoded = _decode_hex_text(hex_match.group(1), cmap)
            if decoded.strip():
                parts.append(decoded)
        for string_match in re.finditer(r"\((?:\\.|[^\\()])*\)\s*Tj", block):
            decoded = _unescape_pdf_string(string_match.group(0)[: string_match.group(0).rfind(")") + 1])
            if decoded.strip():
                parts.append(decoded)
    return parts


def _decode_text_array(array_text: str, cmap: dict[int, str]) -> str:
    decoded: list[str] = []
    for token in re.finditer(r"<([0-9A-Fa-f]+)>|\((?:\\.|[^\\()])*\)", array_text):
        raw = token.group(0)
        if raw.startswith("<"):
            decoded.append(_decode_hex_text(token.group(1), cmap))
        else:
            decoded.append(_unescape_pdf_string(raw))
    return "".join(decoded)


def _decode_hex_text(hex_text: str, cmap: dict[int, str]) -> str:
    if not cmap:
        return ""
    chars: list[str] = []
    for index in range(0, len(hex_text) - 3, 4):
        code = int(hex_text[index : index + 4], 16)
        chars.append(cmap.get(code, ""))
    return "".join(chars)


def _extract_vector_geometry(
    stream: str,
) -> tuple[list[dict[str, float]], list[dict[str, float]], list[dict[str, float]]]:
    content = re.sub(r"BT.*?ET", " ", stream, flags=re.S)
    tokens = re.findall(
        r"[-+]?\d*\.\d+|[-+]?\d+|"
        r"\b(?:m|l|c|v|y|h|re|S|s|f\*|f|F|B|b|cm|q|Q|w|J|j|M|d|rg|RG)\b",
        content,
    )
    stack: list[float] = []
    ctm_stack: list[tuple[float, float, float, float, float, float]] = []
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    subpaths: list[list[tuple]] = []
    current_subpath: list[tuple] = []
    current: tuple[float, float] | None = None
    start: tuple[float, float] | None = None
    lines: list[dict[str, float]] = []
    curves: list[dict[str, float]] = []
    circles: list[dict[str, float]] = []

    def flush_current_subpath() -> None:
        nonlocal current_subpath
        if current_subpath:
            subpaths.append(current_subpath)
            current_subpath = []

    for token in tokens:
        if re.fullmatch(r"[-+]?\d*\.\d+|[-+]?\d+", token):
            stack.append(float(token))
            continue

        if token == "q":
            ctm_stack.append(ctm)
            stack.clear()
            continue
        if token == "Q":
            ctm = ctm_stack.pop() if ctm_stack else ctm
            stack.clear()
            continue
        if token == "cm" and len(stack) >= 6:
            matrix = tuple(stack[-6:])  # type: ignore[assignment]
            ctm = _multiply_matrix(ctm, matrix)
            stack.clear()
            continue

        if token == "m" and len(stack) >= 2:
            flush_current_subpath()
            y = stack.pop()
            x = stack.pop()
            current = _transform_point((x, y), ctm)
            start = current
            stack.clear()
            continue
        if token == "l" and len(stack) >= 2 and current is not None:
            y = stack.pop()
            x = stack.pop()
            end = _transform_point((x, y), ctm)
            current_subpath.append(("l", current, end))
            current = end
            stack.clear()
            continue
        if token == "c" and len(stack) >= 6 and current is not None:
            y3 = stack.pop()
            x3 = stack.pop()
            y2 = stack.pop()
            x2 = stack.pop()
            y1 = stack.pop()
            x1 = stack.pop()
            c1 = _transform_point((x1, y1), ctm)
            c2 = _transform_point((x2, y2), ctm)
            end = _transform_point((x3, y3), ctm)
            current_subpath.append(("c", current, c1, c2, end))
            current = end
            stack.clear()
            continue
        if token == "v" and len(stack) >= 4 and current is not None:
            y3 = stack.pop()
            x3 = stack.pop()
            y2 = stack.pop()
            x2 = stack.pop()
            c1 = current
            c2 = _transform_point((x2, y2), ctm)
            end = _transform_point((x3, y3), ctm)
            current_subpath.append(("c", current, c1, c2, end))
            current = end
            stack.clear()
            continue
        if token == "y" and len(stack) >= 4 and current is not None:
            y3 = stack.pop()
            x3 = stack.pop()
            y1 = stack.pop()
            x1 = stack.pop()
            c1 = _transform_point((x1, y1), ctm)
            end = _transform_point((x3, y3), ctm)
            current_subpath.append(("c", current, c1, end, end))
            current = end
            stack.clear()
            continue
        if token == "h" and current is not None and start is not None:
            current_subpath.append(("l", current, start))
            current = start
            stack.clear()
            continue
        if token == "re" and len(stack) >= 4:
            height = stack.pop()
            width = stack.pop()
            y = stack.pop()
            x = stack.pop()
            p1 = _transform_point((x, y), ctm)
            p2 = _transform_point((x + width, y), ctm)
            p3 = _transform_point((x + width, y + height), ctm)
            p4 = _transform_point((x, y + height), ctm)
            flush_current_subpath()
            subpaths.append([("l", p1, p2), ("l", p2, p3), ("l", p3, p4), ("l", p4, p1)])
            current = p1
            start = p1
            stack.clear()
            continue
        if token in {"S", "s", "f", "f*", "F", "B", "b"}:
            flush_current_subpath()
            for subpath in subpaths:
                for segment in subpath:
                    if segment[0] == "l":
                        start_point, end_point = segment[1], segment[2]
                        lines.append(
                            {
                                "x1_pt": start_point[0],
                                "y1_pt": start_point[1],
                                "x2_pt": end_point[0],
                                "y2_pt": end_point[1],
                            }
                        )
                    elif segment[0] == "c":
                        curve = _curve_dict(segment[1], segment[2], segment[3], segment[4])
                        curves.append(curve)
                circle = _circle_from_subpath(subpath)
                if circle is not None:
                    circles.append(circle)
            subpaths = []
            current = None
            start = None
            stack.clear()
            continue

        stack.clear()

    circles.extend(_circles_from_curve_quadrants(curves))
    return lines, curves, _dedupe_circles(circles)


def _curve_dict(
    start: tuple[float, float],
    c1: tuple[float, float],
    c2: tuple[float, float],
    end: tuple[float, float],
) -> dict[str, float]:
    xs = [start[0], c1[0], c2[0], end[0]]
    ys = [start[1], c1[1], c2[1], end[1]]
    return {
        "x0_pt": start[0],
        "y0_pt": start[1],
        "x1_pt": c1[0],
        "y1_pt": c1[1],
        "x2_pt": c2[0],
        "y2_pt": c2[1],
        "x3_pt": end[0],
        "y3_pt": end[1],
        "min_x_pt": min(xs),
        "min_y_pt": min(ys),
        "max_x_pt": max(xs),
        "max_y_pt": max(ys),
    }


def _circle_from_subpath(subpath: list[tuple]) -> dict[str, float] | None:
    curve_segments = [segment for segment in subpath if segment[0] == "c"]
    if len(curve_segments) != 4 or any(segment[0] != "c" for segment in subpath):
        return None
    points = [point for segment in curve_segments for point in segment[1:]]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    if width <= 0 or abs(width - height) > max(0.75, width * 0.08):
        return None
    if not _near_point(curve_segments[0][1], curve_segments[-1][4], tolerance=1.0):
        return None
    return {
        "cx_pt": (min(xs) + max(xs)) / 2.0,
        "cy_pt": (min(ys) + max(ys)) / 2.0,
        "diameter_pt": (width + height) / 2.0,
    }


def _circles_from_curve_quadrants(curves: list[dict[str, float]]) -> list[dict[str, float]]:
    """Detect circles emitted as four separately stroked quarter-arc curves."""

    grouped: dict[tuple[int, int, int], list[tuple[str, int, float, float, float]]] = {}
    tolerance = 0.5
    for index, curve in enumerate(curves):
        min_x = curve["min_x_pt"]
        min_y = curve["min_y_pt"]
        max_x = curve["max_x_pt"]
        max_y = curve["max_y_pt"]
        width = max_x - min_x
        height = max_y - min_y
        if width < 1.0 or height < 1.0 or width > 40.0 or height > 40.0:
            continue
        if abs(width - height) > max(0.8, width * 0.18):
            continue

        radius = (width + height) / 2.0
        center_candidates = [
            ((min_x, min_y), "upper_right"),
            ((max_x, min_y), "upper_left"),
            ((max_x, max_y), "lower_left"),
            ((min_x, max_y), "lower_right"),
        ]
        for (center_x, center_y), quadrant in center_candidates:
            key = (
                round(center_x / tolerance),
                round(center_y / tolerance),
                round(radius / tolerance),
            )
            grouped.setdefault(key, []).append((quadrant, index, center_x, center_y, radius))

    circles: list[dict[str, float]] = []
    for items in grouped.values():
        quadrants = {item[0] for item in items}
        curve_ids = {item[1] for item in items}
        if len(quadrants) < 4 or len(curve_ids) < 4:
            continue
        circles.append(
            {
                "cx_pt": median(item[2] for item in items),
                "cy_pt": median(item[3] for item in items),
                "diameter_pt": median(item[4] for item in items) * 2.0,
            }
        )
    return circles


def _dedupe_circles(circles: list[dict[str, float]]) -> list[dict[str, float]]:
    deduped: list[dict[str, float]] = []
    for circle in circles:
        if any(
            abs(circle["cx_pt"] - other["cx_pt"]) <= 0.5
            and abs(circle["cy_pt"] - other["cy_pt"]) <= 0.5
            and abs(circle["diameter_pt"] - other["diameter_pt"]) <= 0.5
            for other in deduped
        ):
            continue
        deduped.append(circle)
    return deduped


def _multiply_matrix(
    left: tuple[float, float, float, float, float, float],
    right: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a1, b1, c1, d1, e1, f1 = left
    a2, b2, c2, d2, e2, f2 = right
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _transform_point(
    point: tuple[float, float],
    matrix: tuple[float, float, float, float, float, float],
) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    x, y = point
    return (a * x + c * y + e, b * x + d * y + f)


def _near_point(a: tuple[float, float], b: tuple[float, float], tolerance: float) -> bool:
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tolerance


def _unescape_pdf_string(value: str) -> str:
    text = value[1:-1]
    replacements = {
        r"\(": "(",
        r"\)": ")",
        r"\\": "\\",
        r"\n": "\n",
        r"\r": "\r",
        r"\t": "\t",
        r"\b": "\b",
        r"\f": "\f",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return re.sub(r"\\([0-7]{1,3})", lambda m: chr(int(m.group(1), 8)), text)
