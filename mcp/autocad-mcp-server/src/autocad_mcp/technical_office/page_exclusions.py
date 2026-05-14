"""Manager-approved page exclusions for non-plate PDF pages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocad_mcp.technical_office.pdf_reader import PdfExtraction, PdfPageContent


@dataclass(frozen=True)
class PageExclusion:
    page: int
    source_pdf: str | None = None
    reason: str = "non_plate_page"
    note: str | None = None


def load_page_exclusions(job_dir: str | Path) -> list[PageExclusion]:
    path = Path(job_dir) / "page_exclusions.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("excluded_pages", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("page_exclusions.json must contain a list or an object with an excluded_pages list")
    exclusions: list[PageExclusion] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"page_exclusions.json row {index}: object expected")
        page = _positive_int(row.get("page"), index)
        source_pdf = _optional_str(row.get("source_pdf"))
        exclusions.append(
            PageExclusion(
                page=page,
                source_pdf=source_pdf,
                reason=_optional_str(row.get("reason")) or "non_plate_page",
                note=_optional_str(row.get("note")),
            )
        )
    return exclusions


def filter_excluded_pages(
    extraction: PdfExtraction,
    exclusions: list[PageExclusion],
    *,
    source_pdf: str,
) -> PdfExtraction:
    if not exclusions:
        return extraction
    filtered_pages = [
        page
        for page in extraction.pages
        if not is_page_excluded(exclusions, source_pdf=source_pdf, page_number=page.page_number)
    ]
    if len(filtered_pages) == len(extraction.pages):
        return extraction
    return PdfExtraction(
        pages=filtered_pages,
        manual_review_required=extraction.manual_review_required,
        confidence=extraction.confidence,
        notes=extraction.notes,
    )


def excluded_page_records(
    pages: list[PdfPageContent],
    exclusions: list[PageExclusion],
    *,
    source_pdf: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for page in pages:
        exclusion = _matching_exclusion(exclusions, source_pdf=source_pdf, page_number=page.page_number)
        if exclusion is None:
            continue
        records.append(
            {
                "source_pdf": source_pdf,
                "page": page.page_number,
                "reason": exclusion.reason,
                "note": exclusion.note,
            }
        )
    return records


def is_page_excluded(exclusions: list[PageExclusion], *, source_pdf: str, page_number: int) -> bool:
    return _matching_exclusion(exclusions, source_pdf=source_pdf, page_number=page_number) is not None


def _matching_exclusion(
    exclusions: list[PageExclusion],
    *,
    source_pdf: str,
    page_number: int,
) -> PageExclusion | None:
    for exclusion in exclusions:
        if exclusion.page != page_number:
            continue
        if exclusion.source_pdf and exclusion.source_pdf != source_pdf:
            continue
        return exclusion
    return None


def _positive_int(value: Any, row_number: int) -> int:
    try:
        page = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"page_exclusions.json row {row_number}: page must be an integer") from exc
    if page <= 0:
        raise ValueError(f"page_exclusions.json row {row_number}: page must be positive")
    return page


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
