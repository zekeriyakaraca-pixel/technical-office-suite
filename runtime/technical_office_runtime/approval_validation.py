from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import RuntimePaths, ensure_autocad_import_path
from .relief_types import normalize_relief_type
from .visual_evidence import load_microzoom_manifest, microzoom_manifest_is_valid_for_candidate


def validate_approved_rows(
    rows: list[dict[str, Any]],
    paths: RuntimePaths,
    job_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ensure_autocad_import_path(paths)
    from autocad_mcp.technical_office.models import CornerReliefSpec, HoleSpec, PlateSpec, SlotSpec

    pdf_names = {path.name for path in (paths.jobs_import_root / job_id).glob("*.pdf")}
    validated: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        try:
            contour_error = explicit_contour_detail_error(row)
            if contour_error:
                raise ValueError(contour_error)
            source_pdf = str(row.get("source_pdf") or "").strip()
            if source_pdf not in pdf_names and len(pdf_names) == 1:
                source_pdf = next(iter(pdf_names))
            if source_pdf not in pdf_names:
                raise ValueError(f"source_pdf must belong to job: {source_pdf}")
            visual_evidence_error = _visual_candidate_evidence_error(row, paths, source_pdf=source_pdf)
            if visual_evidence_error:
                raise ValueError(visual_evidence_error)
            polygon_vertices = _clean_polygon_vertices(row.get("polygon_vertices"))
            if row.get("polygon_vertices") not in (None, "") and polygon_vertices is None:
                raise ValueError("polygon_vertices must contain at least 3 points with numeric x/y values")
            polygon_geometry_error = _polygon_geometry_error(row, polygon_vertices)
            if polygon_geometry_error:
                raise ValueError(polygon_geometry_error)
            spec = PlateSpec(
                poz_no=_required_text(row, "poz_no"),
                width=_required_float(row, "width"),
                height=_required_float(row, "height"),
                thickness=_required_float(row, "thickness"),
                material=str(row.get("material") or "UNKNOWN"),
                quantity=int(row.get("quantity") or 1),
                unit_surface_area_m2=_optional_float(row.get("unit_surface_area_m2")),
                unit_weight_kg=_optional_float(row.get("unit_weight_kg")),
                holes=[
                    HoleSpec(
                        x=_required_float(item, "x"),
                        y=_required_float(item, "y"),
                        diameter=_required_float(item, "diameter"),
                    )
                    for item in row.get("holes", [])
                    if isinstance(item, dict)
                ],
                slots=[
                    SlotSpec(
                        x=_required_float(item, "x"),
                        y=_required_float(item, "y"),
                        length=_required_float(item, "length"),
                        width=_required_float(item, "width"),
                        rotation_deg=_optional_float(item.get("rotation_deg")) or 0.0,
                    )
                    for item in row.get("slots", [])
                    if isinstance(item, dict)
                ],
                corner_reliefs=[
                    CornerReliefSpec(
                        corner=_required_text(item, "corner"),
                        radius=_required_float(item, "radius"),
                        relief_type=normalize_relief_type(str(item.get("relief_type") or item.get("type") or "round")),
                        x_offset=_optional_float(item.get("x_offset")),
                        y_offset=_optional_float(item.get("y_offset")),
                    )
                    for item in row.get("corner_reliefs", [])
                    if isinstance(item, dict)
                    and normalize_relief_type(str(item.get("relief_type") or item.get("type") or "")) != "polygon_contour"
                    and item.get("corner")
                ],
                polygon_vertices=polygon_vertices,
                source_page=int(row["source_page"]) if row.get("source_page") not in (None, "") else None,
                confidence=_raw_conf if (_raw_conf := _optional_float(row.get("confidence"))) is not None else 0.5,
                notes=["manager_approved_from_visual_candidate"],
            )
            validation = spec.validate()
            if validation:
                raise ValueError("; ".join(validation))
            data = spec.to_dict()
            data["source_pdf"] = source_pdf
            if _row_requires_visual_evidence(row):
                for key in (
                    "source_trace",
                    "analysis_confidence",
                    "uncertainties",
                    "microzoom_manifest_path",
                    "evidence_images",
                    "provider",
                    "approval_required",
                ):
                    if key in row:
                        data[key] = row[key]
            dim_warns = _dimension_chain_warnings(row)
            if dim_warns:
                data["dimension_chain_warnings"] = dim_warns
            if row.get("coordinate_inferred"):
                notes = data.get("notes") or []
                if "coordinate_inferred_from_uncertainty" not in notes:
                    notes = list(notes) + ["coordinate_inferred_from_uncertainty"]
                data["notes"] = notes
            validated.append(data)
        except Exception as exc:
            errors.append({"row": index, "candidate_id": row.get("candidate_id"), "error": str(exc)})
    if not errors:
        coverage_errors = _approved_visual_page_coverage_errors(validated, paths, job_id)
        if coverage_errors:
            return [], coverage_errors
    return validated, errors


def annotate_candidate_quality(
    row: dict[str, Any],
    paths: RuntimePaths,
    job_id: str,
    *,
    extraction_status: str | None = None,
    refinement_attempted: bool | None = None,
) -> dict[str, Any]:
    """Attach stable, user-visible pre-validation metadata to a visual candidate."""

    item = dict(row)
    pdf_names = {path.name for path in (paths.jobs_import_root / job_id).glob("*.pdf")}
    source_pdf = str(item.get("source_pdf") or "").strip()
    if source_pdf not in pdf_names and len(pdf_names) == 1:
        source_pdf = next(iter(pdf_names))
        item["source_pdf"] = source_pdf

    blocking_errors: list[str] = []
    warnings: list[str] = []

    if not source_pdf or source_pdf not in pdf_names:
        blocking_errors.append(f"source_pdf must belong to job: {source_pdf}")

    contour_error = explicit_contour_detail_error(item)
    if contour_error:
        blocking_errors.append(contour_error)

    polygon_vertices = _clean_polygon_vertices(item.get("polygon_vertices"))
    if item.get("polygon_vertices") not in (None, "") and polygon_vertices is None:
        blocking_errors.append("polygon_vertices must contain at least 3 points with numeric x/y values")
    polygon_geometry_error = _polygon_geometry_error(item, polygon_vertices)
    if polygon_geometry_error:
        blocking_errors.append(polygon_geometry_error)

    if source_pdf:
        visual_error = _visual_candidate_evidence_error(item, paths, source_pdf=source_pdf)
        if visual_error:
            blocking_errors.append(visual_error)

    confidence = _optional_float(item.get("analysis_confidence")) or _optional_float(item.get("confidence")) or 0.0
    if confidence and confidence < 0.6:
        warnings.append(f"analysis_confidence is low ({confidence:.2f}); manager review is required")
    if item.get("coordinate_inferred"):
        warnings.append("coordinate values were inferred and require manager confirmation")
    if isinstance(item.get("uncertainties"), list) and item["uncertainties"]:
        warnings.append("candidate has unresolved uncertainties")

    blocking_errors = _dedupe_text(blocking_errors)
    warnings = _dedupe_text(warnings)
    item["blocking_validation_errors"] = blocking_errors
    item["validation_errors"] = blocking_errors + warnings
    if blocking_errors:
        item["quality_status"] = "blocked"
    elif warnings:
        item["quality_status"] = "needs_review"
    else:
        item["quality_status"] = "ready_for_approval"
    if extraction_status is not None:
        item["extraction_status"] = extraction_status
    if refinement_attempted is not None:
        item["refinement_attempted"] = bool(refinement_attempted)
    if polygon_vertices is not None:
        item["polygon_vertices"] = polygon_vertices
    return item


def annotate_candidate_qualities(
    rows: list[dict[str, Any]],
    paths: RuntimePaths,
    job_id: str,
    *,
    extraction_status: str | None = None,
    refinement_attempted: bool | None = None,
) -> list[dict[str, Any]]:
    return [
        annotate_candidate_quality(
            row,
            paths,
            job_id,
            extraction_status=extraction_status,
            refinement_attempted=refinement_attempted,
        )
        for row in rows
        if isinstance(row, dict)
    ]


def explicit_contour_detail_error(row: dict[str, Any]) -> str | None:
    polygon_error = _polygon_vertices_error(row)
    if polygon_error:
        return polygon_error
    if isinstance(row.get("corner_reliefs"), list) and row["corner_reliefs"]:
        return None
    has_polygon_vertices = _clean_polygon_vertices(row.get("polygon_vertices")) is not None
    contour_type = str(row.get("contour_type") or "").strip().lower()
    evidence = str(row.get("evidence") or row.get("reason") or "").strip().lower()
    text = f"{contour_type} {evidence}"
    strong_terms = ("pah", "chamfer", "chamfered")
    if not has_polygon_vertices:
        strong_terms = strong_terms + ("poligon", "polygon", "polygonal")
    offset_terms = ("side offset", "edge offset", "kenar offset", "yan offset", "kose offset")
    if any(term in text for term in strong_terms) or any(term in text for term in offset_terms):
        return (
            "candidate evidence indicates polygon/chamfer/edge-offset contour, but corner_reliefs is empty. "
            "Add explicit corner_reliefs entries with relief_type=chamfer/round/cugul or send this poz to manual review."
        )
    return None


def _polygon_vertices_error(row: dict[str, Any]) -> str | None:
    if not _row_indicates_polygon_contour(row):
        return None
    if _clean_polygon_vertices(row.get("polygon_vertices")) is not None:
        return None
    return (
        "candidate contour_type=polygon requires polygon_vertices with at least 3 numeric x/y points before approval. "
        "corner_reliefs or polygon_contour markers are not enough to produce the true outer contour."
    )


def _row_indicates_polygon_contour(row: dict[str, Any]) -> bool:
    contour_type = str(row.get("contour_type") or "").strip().lower()
    if contour_type in {"polygon", "polygonal"}:
        return True
    reliefs = row.get("corner_reliefs")
    if isinstance(reliefs, list):
        for relief in reliefs:
            if not isinstance(relief, dict):
                continue
            raw = str(relief.get("relief_type") or relief.get("type") or "").strip()
            if normalize_relief_type(raw) == "polygon_contour":
                return True
    return False


def _clean_polygon_vertices(value: Any) -> list[dict[str, float]] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, list) or len(value) < 3:
        return None
    vertices: list[dict[str, float]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        x = _optional_float(item.get("x"))
        y = _optional_float(item.get("y"))
        if x is None or y is None:
            return None
        vertices.append({"x": x, "y": y})
    return _normalize_polygon_orientation(vertices)


def _polygon_geometry_error(row: dict[str, Any], vertices: list[dict[str, float]] | None) -> str | None:
    if vertices is None:
        return None
    width = _optional_float(row.get("width"))
    height = _optional_float(row.get("height"))
    if width is None or height is None or width <= 0 or height <= 0:
        return None

    points = [(vertex["x"], vertex["y"]) for vertex in vertices]
    tol = 1e-6
    for index, (x, y) in enumerate(points):
        if x < -tol or x > width + tol:
            return f"polygon_vertices[{index}].x is outside plate bounds"
        if y < -tol or y > height + tol:
            return f"polygon_vertices[{index}].y is outside plate bounds"
        if _same_point((x, y), points[index - 1], tol):
            return f"polygon_vertices[{index}] duplicates an adjacent point"
    if _polygon_self_intersects(points, tol):
        return "polygon_vertices must not self-intersect"
    if abs(_signed_polygon_area(points)) <= tol:
        return "polygon_vertices must describe a non-zero area contour"

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    bbox_tol = max(1.0, min(width, height) * 0.02)
    if (
        abs(min(xs)) > bbox_tol
        or abs(min(ys)) > bbox_tol
        or abs(max(xs) - width) > bbox_tol
        or abs(max(ys) - height) > bbox_tol
    ):
        return "polygon_vertices bounding box must match plate width/height within tolerance"
    return None


def _normalize_polygon_orientation(vertices: list[dict[str, float]]) -> list[dict[str, float]]:
    points = [(vertex["x"], vertex["y"]) for vertex in vertices]
    if _signed_polygon_area(points) < 0:
        return list(reversed(vertices))
    return vertices


def _signed_polygon_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _polygon_self_intersects(points: list[tuple[float, float]], tol: float) -> bool:
    count = len(points)
    for i in range(count):
        a1 = points[i]
        a2 = points[(i + 1) % count]
        for j in range(i + 1, count):
            if abs(i - j) <= 1 or {i, j} == {0, count - 1}:
                continue
            if _segments_intersect(a1, a2, points[j], points[(j + 1) % count], tol):
                return True
    return False


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
    tol: float,
) -> bool:
    o1 = _orientation(a1, a2, b1)
    o2 = _orientation(a1, a2, b2)
    o3 = _orientation(b1, b2, a1)
    o4 = _orientation(b1, b2, a2)
    if o1 * o2 < -tol and o3 * o4 < -tol:
        return True
    if abs(o1) <= tol and _on_segment(a1, b1, a2, tol):
        return True
    if abs(o2) <= tol and _on_segment(a1, b2, a2, tol):
        return True
    if abs(o3) <= tol and _on_segment(b1, a1, b2, tol):
        return True
    if abs(o4) <= tol and _on_segment(b1, a2, b2, tol):
        return True
    return False


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    tol: float,
) -> bool:
    return (
        min(a[0], c[0]) - tol <= b[0] <= max(a[0], c[0]) + tol
        and min(a[1], c[1]) - tol <= b[1] <= max(a[1], c[1]) + tol
    )


def _same_point(a: tuple[float, float], b: tuple[float, float], tol: float) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _dedupe_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _approved_visual_page_coverage_errors(
    validated: list[dict[str, Any]],
    paths: RuntimePaths,
    job_id: str,
) -> list[dict[str, Any]]:
    diagnostics = _read_json(paths.jobs_output_root / job_id / "pdf_diagnostics.json") or {}
    pdfs = diagnostics.get("pdfs") if isinstance(diagnostics, dict) else None
    if not isinstance(pdfs, list):
        return []
    errors: list[dict[str, Any]] = []
    for pdf in pdfs:
        if not isinstance(pdf, dict) or pdf.get("classification") not in {"visual_text_required", "text_layer_unreadable"}:
            continue
        source_pdf = str(pdf.get("source_pdf") or "")
        page_count = pdf.get("page_count")
        if not source_pdf or not isinstance(page_count, int) or page_count <= 1:
            continue
        covered_pages = {
            int(row["source_page"])
            for row in validated
            if row.get("source_pdf") == source_pdf and isinstance(row.get("source_page"), int)
        }
        missing_pages = [page for page in range(1, page_count + 1) if page not in covered_pages]
        if not missing_pages:
            continue
        preview = ", ".join(str(page) for page in missing_pages[:12])
        suffix = "" if len(missing_pages) <= 12 else f", ... +{len(missing_pages) - 12}"
        errors.append(
            {
                "row": "job",
                "candidate_id": None,
                "error": (
                    f"{source_pdf} requires visual review coverage for {page_count} pages; "
                    f"approved candidates cover {len(covered_pages)} pages. Missing pages: {preview}{suffix}"
                ),
            }
        )
    return errors


def _visual_candidate_evidence_error(row: dict[str, Any], paths: RuntimePaths, *, source_pdf: str) -> str | None:
    if not _row_requires_visual_evidence(row):
        return None
    source_trace = row.get("source_trace")
    if not isinstance(source_trace, dict):
        return "visual candidate requires source_trace before manager approval"
    if not isinstance(row.get("uncertainties"), list):
        return "visual candidate requires uncertainties before manager approval"
    if row.get("analysis_confidence") in (None, ""):
        return "visual candidate requires analysis_confidence before manager approval"
    manifest_value = row.get("microzoom_manifest_path") or source_trace.get("microzoom_manifest_path")
    if not manifest_value:
        return "visual candidate requires microzoom_manifest_path before manager approval"
    manifest_path = _resolve_suite_path(paths, manifest_value)
    manifest = load_microzoom_manifest(manifest_path)
    evidence_values = row.get("evidence_images")
    if not isinstance(evidence_values, list):
        evidence_values = source_trace.get("evidence_images") if isinstance(source_trace.get("evidence_images"), list) else []
    resolved_evidence = [str(_resolve_suite_path(paths, value)) for value in evidence_values if value]
    ok, error = microzoom_manifest_is_valid_for_candidate(
        manifest,
        source_pdf=source_pdf,
        source_page=row.get("source_page"),
        evidence_images=resolved_evidence,
    )
    if not ok:
        return error or "visual candidate microzoom evidence is invalid"
    return None


def _row_requires_visual_evidence(row: dict[str, Any]) -> bool:
    provider = str(row.get("provider") or "")
    return row.get("approval_required") is True or provider in {"codex_cli", "manager_pdf_scan", "pdf-visual-candidate"}


def _resolve_suite_path(paths: RuntimePaths, value: Any) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return paths.suite_root / path


def _required_text(row: dict[str, Any], key: str) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _required_float(row: dict[str, Any], key: str) -> float:
    value = _optional_float(row.get(key))
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _dimension_chain_warnings(row: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for u in (row.get("uncertainties") or []):
        text = str(u).lower() if isinstance(u, str) else str(u.get("description") or "").lower()
        if any(kw in text for kw in ("chain", "zincir", "sum", "toplam", "mismatch", "uyumsuz", "dimension")):
            result.append(f"dimension_chain_warning: {u}")
    return result


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(str(value).replace(",", "."))


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
