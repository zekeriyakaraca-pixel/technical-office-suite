"""End-to-end PDF to DXF/NC1 plate production job runner."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from autocad_mcp.technical_office.autocad_live import run_autocad_live_validation
from autocad_mcp.technical_office.approved_specs import ApprovedPlate, load_approved_plate_specs
from autocad_mcp.technical_office.dxf_writer import write_plate_dxf
from autocad_mcp.technical_office.job_metadata import upsert_job_metadata
from autocad_mcp.technical_office.models import PlateSpec
from autocad_mcp.technical_office.naming import safe_name
from autocad_mcp.technical_office.nc1_writer import write_plate_nc1
from autocad_mcp.technical_office.page_exclusions import (
    excluded_page_records,
    filter_excluded_pages,
    load_page_exclusions,
)
from autocad_mcp.technical_office.pdf_diagnostics import (
    PdfDiagnostics,
    build_extraction_candidates,
    build_pdf_diagnostics,
    candidates_report,
    diagnostics_report,
    pattern_fingerprint,
    visual_review_manual_review,
)
from autocad_mcp.technical_office.pdf_reader import extract_pdf_content
from autocad_mcp.technical_office.plate_extractor import build_plate_specs
from autocad_mcp.technical_office.positions import load_position_records
from autocad_mcp.technical_office.qc import build_qc_report, write_qc_report


@dataclass
class ProducedPlate:
    poz_no: str
    dxf_path: str
    nc1_path: str
    qc_path: str
    ok: bool


@dataclass
class JobResult:
    job_id: str
    output_dir: str
    produced: list[ProducedPlate]
    manual_reviews: list[dict[str, Any]]
    ok: bool
    summary_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


LiveValidator = Callable[[Path], str]


def run_job(
    job_dir: str | Path,
    output_root: str | Path | None = None,
    autocad_live_policy: str = "auto_start_if_needed",
    live_validator: LiveValidator | None = None,
    input_pdf: str | Path | None = None,
    project_name: str | None = None,
) -> JobResult:
    root = Path(job_dir)
    job_id = root.name
    if project_name is not None:
        upsert_job_metadata(root, project_name)
    input_pdfs = _resolve_input_pdfs(root, input_pdf)

    output_dir = Path(output_root) if output_root else Path("outputs") / "jobs" / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    positions = load_position_records(root)
    page_exclusions = load_page_exclusions(root)
    approved_specs = load_approved_plate_specs(root)
    input_pdf_names = {pdf.name for pdf in input_pdfs}
    single_input_pdf = input_pdfs[0].name if len(input_pdfs) == 1 else None
    manual_reviews: list[dict[str, Any]] = []
    produced: list[ProducedPlate] = []
    diagnostics: list[PdfDiagnostics] = []
    extraction_candidates: list[dict[str, Any]] = []
    applied_page_exclusions: list[dict[str, Any]] = []

    for pdf in input_pdfs:
        raw_extraction = extract_pdf_content(pdf)
        pdf_diagnostics = build_pdf_diagnostics(pdf, raw_extraction)
        diagnostics.append(pdf_diagnostics)
        applied_page_exclusions.extend(
            excluded_page_records(raw_extraction.pages, page_exclusions, source_pdf=pdf.name)
        )
        extraction = filter_excluded_pages(raw_extraction, page_exclusions, source_pdf=pdf.name)
        extraction_candidates.extend(build_extraction_candidates(pdf, extraction, pdf_diagnostics))

        if approved_specs:
            continue

        if pdf_diagnostics.approval_required:
            manual_reviews.append(visual_review_manual_review(pdf_diagnostics))
            continue

        extraction_result = build_plate_specs(extraction, positions if len(input_pdfs) == 1 else None)
        for review in extraction_result.manual_reviews:
            review_data = review.to_dict()
            review_data["source_pdf"] = pdf.name
            manual_reviews.append(review_data)

        for spec in extraction_result.plates:
            produced.append(
                _produce_plate(
                    spec,
                    output_dir,
                    source_pdf=pdf.name,
                    autocad_live_policy=autocad_live_policy,
                    live_validator=live_validator,
                )
            )

    if approved_specs:
        manual_reviews = _manual_reviews_for_approved_visual_scope(diagnostics, approved_specs)
        geometry_reviews = _manual_reviews_for_open_manager_geometry_notes(output_dir, approved_specs)
        manual_reviews.extend(geometry_reviews)
        geometry_reviews_by_poz = _reviews_by_poz(geometry_reviews)
        for approved in approved_specs:
            source_pdf = approved.source_pdf
            if single_input_pdf and source_pdf not in input_pdf_names:
                source_pdf = single_input_pdf
            produced.append(
                _produce_plate(
                    approved.spec,
                    output_dir,
                    source_pdf=source_pdf,
                    autocad_live_policy=autocad_live_policy,
                    live_validator=live_validator,
                    blocking_reviews=geometry_reviews_by_poz.get(approved.spec.poz_no, []),
                )
            )

    (output_dir / "pdf_diagnostics.json").write_text(
        json.dumps(diagnostics_report(job_id, diagnostics), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "extraction_candidates.json").write_text(
        json.dumps(candidates_report(job_id, extraction_candidates), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    exclusions_path = output_dir / "page_exclusions_applied.json"
    if applied_page_exclusions:
        exclusions_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "job_id": job_id,
                    "excluded_pages": applied_page_exclusions,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    else:
        exclusions_path.unlink(missing_ok=True)

    if manual_reviews:
        (output_dir / "manual_review_required.json").write_text(
            json.dumps(manual_reviews, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _write_manager_notifications(output_dir, manual_reviews)
    else:
        (output_dir / "manual_review_required.json").unlink(missing_ok=True)
        (output_dir / "manager_notifications.json").unlink(missing_ok=True)

    result = JobResult(
        job_id=job_id,
        output_dir=str(output_dir),
        produced=produced,
        manual_reviews=manual_reviews,
        ok=bool(produced) and all(item.ok for item in produced) and not manual_reviews,
        summary_path=str(output_dir / "job_summary.json"),
    )
    Path(result.summary_path).write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    if approved_specs and result.ok:
        _write_learning_event(root, output_dir, approved_specs, diagnostics)
    return result


def _manual_reviews_for_approved_visual_scope(
    diagnostics: list[PdfDiagnostics],
    approved_specs: list[ApprovedPlate],
) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    approved_pages_by_pdf: dict[str, set[int]] = {}
    unknown_pages_by_pdf: dict[str, int] = {}
    diagnostic_pdf_names = {item.source_pdf for item in diagnostics}
    single_diagnostic_pdf = diagnostics[0].source_pdf if len(diagnostics) == 1 else None
    for approved in approved_specs:
        source_pdf = approved.source_pdf
        if single_diagnostic_pdf and source_pdf not in diagnostic_pdf_names:
            source_pdf = single_diagnostic_pdf
        page = approved.spec.source_page
        if page is None:
            unknown_pages_by_pdf[source_pdf] = unknown_pages_by_pdf.get(source_pdf, 0) + 1
            continue
        approved_pages_by_pdf.setdefault(source_pdf, set()).add(page)

    for item in diagnostics:
        if not item.approval_required or item.page_count <= 0:
            continue
        approved_pages = approved_pages_by_pdf.get(item.source_pdf, set())
        missing_pages = [page for page in range(1, item.page_count + 1) if page not in approved_pages]
        unknown_count = unknown_pages_by_pdf.get(item.source_pdf, 0)
        if not missing_pages and unknown_count == 0:
            continue
        detail = (
            f"`{item.source_pdf}` gorsel/OCR onayi gerektiren {item.page_count} sayfa iceriyor; "
            f"onayli spec {len(approved_pages)} sayfayi kapsiyor."
        )
        if missing_pages:
            detail += f" Eksik sayfalar: {_page_range_text(missing_pages)}."
        if unknown_count:
            detail += f" {unknown_count} onayli pozda source_page yok; sayfa kapsami dogrulanamadi."
        reviews.append(
            {
                "reason": "approved_specs_missing_visual_pages",
                "page": None,
                "poz_no": None,
                "source_pdf": item.source_pdf,
                "detail": detail,
                "next_action": (
                    "Eksik sayfalar icin gorsel aday veya manuel poz/olcu onayi tamamlanmadan "
                    "teslim ve partlist kapisini acma."
                ),
                "approval_required": True,
            }
        )
    return reviews


def _manual_reviews_for_open_manager_geometry_notes(
    output_dir: Path,
    approved_specs: list[ApprovedPlate],
) -> list[dict[str, Any]]:
    notes_path = output_dir / "manager_issue_notes.jsonl"
    if not notes_path.exists():
        return []
    approved_by_poz = {approved.spec.poz_no: approved for approved in approved_specs}
    reviews: list[dict[str, Any]] = []
    for line in notes_path.read_text(encoding="utf-8").splitlines():
        try:
            note = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(note, dict) or note.get("status") not in {None, "open"}:
            continue
        tags = {str(tag).lower() for tag in note.get("tags", []) if tag}
        if not ({"pah/kose eksigi", "poligon kontur"} & tags):
            continue
        affected = [str(poz) for poz in note.get("affected_pozs", []) if str(poz) in approved_by_poz]
        for poz_no in affected:
            approved = approved_by_poz[poz_no]
            if approved.spec.corner_reliefs:
                continue
            reviews.append(
                {
                    "reason": "manager_geometry_issue_open",
                    "page": approved.spec.source_page,
                    "poz_no": poz_no,
                    "source_pdf": approved.source_pdf,
                    "detail": (
                        "Mudur notunda bu poz icin poligon kontur veya pah/kose eksigi acik. "
                        "Aday geometri duzeltilmeden QC ok=true teslim kapisini acamaz."
                    ),
                    "next_action": "Gorsel analiz adayini kontur/pah bilgisiyle duzelt ve poz yeniden uret.",
                    "approval_required": True,
                }
            )
    return reviews


def _page_range_text(pages: list[int]) -> str:
    if not pages:
        return ""
    ranges: list[str] = []
    start = pages[0]
    previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = page
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


def _reviews_by_poz(reviews: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for review in reviews:
        poz_no = review.get("poz_no")
        if isinstance(poz_no, str) and poz_no:
            grouped.setdefault(poz_no, []).append(review)
    return grouped


def _write_manager_notifications(output_dir: Path, manual_reviews: list[dict[str, Any]]) -> None:
    notifications = []
    for review in manual_reviews:
        poz_no = review.get("poz_no")
        notifications.append(
            {
                "role": "teknik-ofis-muduru",
                "severity": "blocker",
                "reason": review.get("reason"),
                "poz_no": poz_no,
                "source_pdf": review.get("source_pdf"),
                "message": (
                    f"Poz {poz_no} otomatik uretilmedi; teslim veya partlist oncesi teknik ofis mudurune bildir."
                    if poz_no
                    else "PDF otomatik uretime uygun bulunmadi; teslim veya partlist oncesi teknik ofis muduru onayi gerekir."
                ),
                "detail": review.get("detail"),
                "next_action": review.get("next_action"),
                "approval_required": bool(review.get("approval_required")),
            }
        )
    (output_dir / "manager_notifications.json").write_text(
        json.dumps(notifications, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _produce_plate(
    spec: PlateSpec,
    output_dir: Path,
    *,
    source_pdf: str,
    autocad_live_policy: str,
    live_validator: LiveValidator | None,
    blocking_reviews: list[dict[str, Any]] | None = None,
) -> ProducedPlate:
    name = safe_name(spec.poz_no)
    plate_dir = output_dir / name
    dxf_path = plate_dir / f"{name}.dxf"
    nc1_path = plate_dir / f"{name}.nc1"
    qc_path = plate_dir / f"{name}_qc.json"
    write_plate_dxf(spec, dxf_path)
    write_plate_nc1(spec, nc1_path)
    autocad_live_status = _autocad_live_status(dxf_path, autocad_live_policy, live_validator)
    qc_report = build_qc_report(spec, dxf_path, nc1_path, autocad_live_status=autocad_live_status)
    if blocking_reviews:
        qc_report["manual_review_required"] = True
        qc_report["blocking_reviews"] = blocking_reviews
        qc_report["ok"] = False
    qc_report["source_pdf"] = source_pdf
    write_qc_report(qc_report, qc_path)
    return ProducedPlate(
        poz_no=spec.poz_no,
        dxf_path=str(dxf_path),
        nc1_path=str(nc1_path),
        qc_path=str(qc_path),
        ok=bool(qc_report["ok"]),
    )


def _autocad_live_status(
    dxf_path: Path,
    policy: str,
    live_validator: LiveValidator | None,
) -> str:
    normalized = policy.strip().lower()
    if normalized in ("off", "disabled", "skip", "skipped"):
        return "skipped"
    if normalized != "auto_start_if_needed":
        return f"skipped_unknown_policy:{policy}"
    try:
        validator = live_validator or run_autocad_live_validation
        return validator(dxf_path)
    except Exception:
        return "skipped_autostart_failed"


def _resolve_input_pdfs(job_dir: Path, input_pdf: str | Path | None = None) -> list[Path]:
    if input_pdf is not None:
        requested = Path(input_pdf)
        if not requested.is_absolute():
            requested = job_dir / requested
        if not requested.exists():
            raise FileNotFoundError(f"Missing requested PDF: {requested}")
        return [requested]

    contracted = job_dir / "input.pdf"
    if contracted.exists():
        return [contracted]

    pdfs = sorted(path for path in job_dir.glob("*.pdf") if path.is_file())
    if pdfs:
        return pdfs
    raise FileNotFoundError(f"Missing job PDF: {contracted}")


def _write_learning_event(
    job_dir: Path,
    output_dir: Path,
    approved_specs: list[ApprovedPlate],
    diagnostics: list[PdfDiagnostics],
) -> None:
    suite_root = _suite_root_for_job(job_dir)
    if suite_root is None:
        return
    entries_dir = suite_root / "journal" / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)
    fingerprints = sorted({pattern_fingerprint(item) for item in diagnostics})
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S")
    event = {
        "schema_version": 1,
        "event_type": "manager_approved_visual_pdf_candidate",
        "job_id": job_dir.name,
        "approved_poz_count": len(approved_specs),
        "pattern_fingerprints": fingerprints,
        "output_dir": str(output_dir),
        "approved_specs": [approved.spec.poz_no for approved in approved_specs],
        "created_at": datetime.now().astimezone().isoformat(),
    }
    event_path = entries_dir / f"{timestamp}_{job_dir.name}_visual_learning.json"
    event_path.write_text(json.dumps(event, indent=2, ensure_ascii=False), encoding="utf-8")
    _maybe_write_skill_proposal(suite_root, fingerprints)


def _maybe_write_skill_proposal(suite_root: Path, fingerprints: list[str]) -> None:
    if not fingerprints:
        return
    entries_dir = suite_root / "journal" / "entries"
    events: list[dict[str, Any]] = []
    for path in entries_dir.glob("*_visual_learning.json"):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("event_type") == "manager_approved_visual_pdf_candidate":
            events.append(event)
    for fingerprint in fingerprints:
        matching = [event for event in events if fingerprint in event.get("pattern_fingerprints", [])]
        if len(matching) < 2:
            continue
        proposals_dir = suite_root / "journal" / "skill_proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        proposal_path = proposals_dir / f"{safe_name(fingerprint)}.md"
        if proposal_path.exists():
            continue
        proposal_path.write_text(
            "\n".join(
                [
                    "# PDF Skill Guncelleme Onerisi",
                    "",
                    f"- Oruntu: `{fingerprint}`",
                    f"- Kanitli is sayisi: {len(matching)}",
                    "- Oneri: PDF_POZ_OKUMA skill'i, bu oruntuyu once gorsel/OCR aday akisi ile ele alacak sekilde guncellensin.",
                    "- Kural: Mudur onayi ve QC ok=true olmadan paylasimli skill dosyasina kalici bilgi yazilmaz.",
                ]
            ),
            encoding="utf-8",
        )


def _suite_root_for_job(job_dir: Path) -> Path | None:
    resolved = job_dir.resolve()
    parents = list(resolved.parents)
    if len(parents) >= 4 and parents[0].name == "jobs" and parents[1].name == "imports" and parents[2].name == "workspace":
        return parents[3]
    for parent in parents:
        if (parent / "agents").exists() and (parent / "journal").exists():
            return parent
    return None
