"""PDF extraction diagnostics and low-confidence candidate reporting."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from autocad_mcp.technical_office.pdf_reader import PdfExtraction


VISUAL_REVIEW_REASONS = {"visual_text_required", "text_layer_unreadable"}


@dataclass(frozen=True)
class PdfDiagnostics:
    source_pdf: str
    page_count: int
    total_text_chars: int
    readable_text_chars: int
    total_vector_operators: int
    total_vector_lines: int
    total_vector_circles: int
    pages_with_text: int
    pages_with_vectors: int
    text_quality: str
    classification: str
    approval_required: bool
    ocr_status: str
    vision_status: str
    summary_tr: str
    next_action_tr: str
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_pdf_diagnostics(
    pdf_path: str | Path,
    extraction: PdfExtraction,
    *,
    ocr_status: str | None = None,
    vision_status: str | None = None,
) -> PdfDiagnostics:
    source_pdf = Path(pdf_path).name
    page_count = len(extraction.pages)
    total_text_chars = sum(len(page.text) for page in extraction.pages)
    readable_text_chars = sum(_readable_char_count(page.text) for page in extraction.pages)
    total_vectors = sum(page.vector_operator_count for page in extraction.pages)
    total_lines = sum(len(page.vector_lines) for page in extraction.pages)
    total_circles = sum(len(page.vector_circles) for page in extraction.pages)
    pages_with_text = sum(1 for page in extraction.pages if page.text.strip())
    pages_with_vectors = sum(1 for page in extraction.pages if page.vector_operator_count > 0)

    text_quality = _text_quality(page_count, total_text_chars, readable_text_chars)
    classification = _classification(
        extraction=extraction,
        page_count=page_count,
        total_text_chars=total_text_chars,
        readable_text_chars=readable_text_chars,
        total_vectors=total_vectors,
    )
    approval_required = classification in VISUAL_REVIEW_REASONS
    summary_tr, next_action_tr = _turkish_messages(
        classification,
        source_pdf=source_pdf,
        page_count=page_count,
        total_vectors=total_vectors,
        total_text_chars=total_text_chars,
    )

    notes = list(extraction.notes)
    if classification == "text_layer_unreadable":
        notes.append("PDF metin katmani okunabilir teknik poz bilgisi icermiyor veya bozuk karakterlerden olusuyor.")
    elif classification == "visual_text_required":
        notes.append("PDF cizim vektorleri iceriyor ancak okunabilir metin katmani bulunamadi.")

    return PdfDiagnostics(
        source_pdf=source_pdf,
        page_count=page_count,
        total_text_chars=total_text_chars,
        readable_text_chars=readable_text_chars,
        total_vector_operators=total_vectors,
        total_vector_lines=total_lines,
        total_vector_circles=total_circles,
        pages_with_text=pages_with_text,
        pages_with_vectors=pages_with_vectors,
        text_quality=text_quality,
        classification=classification,
        approval_required=approval_required,
        ocr_status=ocr_status or _local_ocr_status(),
        vision_status=vision_status or "disabled",
        summary_tr=summary_tr,
        next_action_tr=next_action_tr,
        notes=notes,
    )


def build_extraction_candidates(
    pdf_path: str | Path,
    extraction: PdfExtraction,
    diagnostics: PdfDiagnostics,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if diagnostics.classification in VISUAL_REVIEW_REASONS:
        candidates.append(_visual_review_candidate(pdf_path, diagnostics))
        candidates.extend(_ocr_candidates(pdf_path, diagnostics))
    else:
        candidates.extend(_text_candidates(extraction, diagnostics))
    return candidates


def visual_review_manual_review(diagnostics: PdfDiagnostics) -> dict[str, Any]:
    detail = diagnostics.summary_tr
    if diagnostics.ocr_status != "available":
        detail = (
            f"{detail} Yerel OCR durumu: OCR={diagnostics.ocr_status}. "
            "Bu bilgi kullaniciya kurulum talimati degil; teknik ofis muduru aksiyon notudur."
        )
    return {
        "reason": diagnostics.classification,
        "page": None,
        "poz_no": None,
        "detail": detail,
        "next_action": diagnostics.next_action_tr,
        "approval_required": diagnostics.approval_required,
        "source_pdf": diagnostics.source_pdf,
    }


def diagnostics_report(job_id: str, records: list[PdfDiagnostics]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "job_id": job_id,
        "pdfs": [record.to_dict() for record in records],
        "summary": {
            "pdf_count": len(records),
            "approval_required": any(record.approval_required for record in records),
            "classifications": sorted({record.classification for record in records}),
        },
    }


def candidates_report(job_id: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "job_id": job_id,
        "approval_required": any(bool(candidate.get("approval_required")) for candidate in candidates),
        "candidates": candidates,
    }


def pattern_fingerprint(diagnostics: PdfDiagnostics) -> str:
    vector_bucket = "dense" if diagnostics.total_vector_operators >= max(100, diagnostics.page_count * 20) else "light"
    text_bucket = diagnostics.text_quality
    return f"{diagnostics.classification}|pages={diagnostics.page_count}|vectors={vector_bucket}|text={text_bucket}"


def _classification(
    *,
    extraction: PdfExtraction,
    page_count: int,
    total_text_chars: int,
    readable_text_chars: int,
    total_vectors: int,
) -> str:
    if page_count == 0:
        return "manual_review_required" if extraction.manual_review_required else "empty_pdf"
    if total_vectors > 0 and total_text_chars == 0:
        return "visual_text_required"
    vector_dense = total_vectors >= max(100, page_count * 20)
    text_too_short = total_text_chars < max(80, page_count * 5)
    readable_ratio = readable_text_chars / max(total_text_chars, 1)
    if vector_dense and (text_too_short or readable_ratio < 0.6):
        return "text_layer_unreadable"
    if extraction.manual_review_required:
        return "manual_review_required"
    if total_vectors == 0:
        return "text_only_limited_geometry"
    return "text_vector_readable"


def _text_quality(page_count: int, total_text_chars: int, readable_text_chars: int) -> str:
    if total_text_chars == 0:
        return "missing"
    readable_ratio = readable_text_chars / max(total_text_chars, 1)
    if readable_ratio < 0.6:
        return "unreadable"
    if total_text_chars < max(80, page_count * 5):
        return "too_short"
    return "ok"


def _readable_char_count(text: str) -> int:
    return sum(1 for char in text if char.isalnum() or char in " .,:;#-/+*xX()[]_")


def _turkish_messages(
    classification: str,
    *,
    source_pdf: str,
    page_count: int,
    total_vectors: int,
    total_text_chars: int,
) -> tuple[str, str]:
    if classification == "visual_text_required":
        return (
            f"`{source_pdf}` icinde {page_count} sayfa ve {total_vectors} vektor operatoru bulundu, "
            "ancak okunabilir metin katmani yok. Poz numarasi sayfa numarasi degildir; poz bilgisi cizim uzerindeki "
            "parca/mark bilgisidir ve gorsel/OCR inceleme gerektirir.",
            "Teknik ofis muduru gorsel/OCR aday okuma veya manuel poz girisi akisina karar verir; mudur onayi olmadan DXF/NC1 uretme.",
        )
    if classification == "text_layer_unreadable":
        return (
            f"`{source_pdf}` icinde {page_count} sayfa ve yogun vektor cizimi bulundu, fakat metin katmani "
            f"yalnizca {total_text_chars} karakter ve teknik poz bilgisini guvenilir tasimiyor. Bu hata sayfa numarasi "
            "eksigi degil, poz/metin okuma problemidir.",
            "Teknik ofis muduru PDF'yi gorsel/OCR aday okuma kuyruguna alir veya manuel poz/olcu girisi ister; onay olmadan uretim acilmaz.",
        )
    if classification == "manual_review_required":
        return (
            f"`{source_pdf}` otomatik PDF okuma icin yeterli veri vermedi.",
            "PDF dosyasini, sifre/bozuk tarama/metin katmani acisindan manuel kontrol et.",
        )
    if classification == "text_only_limited_geometry":
        return (
            f"`{source_pdf}` metin iceriyor ancak vektor geometri sinyali zayif.",
            "Poz bilgisi uretime girmeden once plaka geometrisini ve delikleri QC ile dogrula.",
        )
    return (
        f"`{source_pdf}` metin ve vektor olarak otomatik okuma icin uygun gorunuyor.",
        "Standart PDF poz okuma ve QC pipeline'i ile devam et.",
    )


def _visual_review_candidate(pdf_path: str | Path, diagnostics: PdfDiagnostics) -> dict[str, Any]:
    return {
        "candidate_type": diagnostics.classification,
        "source_pdf": Path(pdf_path).name,
        "pages": [page for page in range(1, diagnostics.page_count + 1)],
        "confidence": 0.0,
        "approval_required": True,
        "status": "waiting_for_manager_approval",
        "reason": diagnostics.summary_tr,
        "next_action": diagnostics.next_action_tr,
    }


def _text_candidates(extraction: PdfExtraction, diagnostics: PdfDiagnostics) -> list[dict[str, Any]]:
    try:
        from autocad_mcp.technical_office.plate_extractor import _find_poz_numbers
    except Exception:
        return []

    candidates = []
    for page in extraction.pages:
        poz_numbers = _find_poz_numbers(page.text)
        if not poz_numbers:
            continue
        candidates.append(
            {
                "candidate_type": "text_poz",
                "source_pdf": diagnostics.source_pdf,
                "page": page.page_number,
                "poz_numbers": poz_numbers,
                "confidence": diagnostics_to_confidence(diagnostics),
                "approval_required": False,
                "status": "auto_extractable",
            }
        )
    return candidates


def diagnostics_to_confidence(diagnostics: PdfDiagnostics) -> float:
    if diagnostics.classification == "text_vector_readable":
        return 0.95
    if diagnostics.classification == "text_only_limited_geometry":
        return 0.65
    return 0.0


def _ocr_candidates(pdf_path: str | Path, diagnostics: PdfDiagnostics) -> list[dict[str, Any]]:
    if not _ocr_enabled():
        return []
    try:
        import fitz  # type: ignore[import-not-found]
        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
    except Exception:
        return []

    candidates: list[dict[str, Any]] = []
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return []

    max_pages = int(os.environ.get("TOFFICE_OCR_MAX_PAGES", "3"))
    for page_index, page in enumerate(doc, start=1):
        if page_index > max_pages:
            break
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.open(BytesIO(pix.tobytes("png")))
            text = str(pytesseract.image_to_string(image, lang=os.environ.get("TOFFICE_OCR_LANG", "eng+tur")) or "")
        except Exception:
            continue
        parsed = _candidate_from_visual_text(text, diagnostics.source_pdf, page_index, "local_ocr")
        if parsed:
            candidates.append(parsed)
    return candidates


def _candidate_from_visual_text(text: str, source_pdf: str, page: int, method: str) -> dict[str, Any] | None:
    if not text.strip():
        return None
    poz_numbers = _find_candidate_poz_numbers(text)
    dimensions = _find_candidate_dimensions(text)
    if not poz_numbers and not dimensions:
        return None
    return {
        "candidate_type": method,
        "source_pdf": source_pdf,
        "page": page,
        "poz_numbers": poz_numbers,
        "dimensions": dimensions,
        "raw_text_preview": " ".join(text.split())[:500],
        "confidence": 0.45,
        "approval_required": True,
        "status": "candidate_waiting_for_manager_approval",
    }


def _find_candidate_poz_numbers(text: str) -> list[str]:
    found: list[str] = []
    for pattern in (
        r"\b(?:POZ|POS|MARK|PART|ITEM)\s*[:#-]?\s*([A-Za-z0-9_.-]{3,})\b",
        r"\b([A-Za-z]?\d{3,}[A-Za-z0-9_.-]*)\b",
    ):
        for match in re.finditer(pattern, text, flags=re.I):
            found.append(match.group(1))
    return list(dict.fromkeys(found))[:10]


def _find_candidate_dimensions(text: str) -> list[str]:
    found = []
    for match in re.finditer(r"\b\d+(?:[.,]\d+)?\s*[xX*]\s*\d+(?:[.,]\d+)?(?:\s*[xX*]\s*\d+(?:[.,]\d+)?)?\b", text):
        found.append(match.group(0))
    return list(dict.fromkeys(found))[:10]


def _local_ocr_status() -> str:
    if not _ocr_enabled():
        return "disabled"
    try:
        import fitz  # type: ignore[import-not-found]
        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image  # noqa: F401  # type: ignore[import-not-found]
    except Exception as exc:
        return f"unavailable:{type(exc).__name__}"
    return "available"


def _ocr_enabled() -> bool:
    return os.environ.get("TOFFICE_ENABLE_LOCAL_OCR", "").strip().lower() in {"1", "true", "yes", "on"}
