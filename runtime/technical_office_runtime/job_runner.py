from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import RuntimePaths, ensure_autocad_import_path
from .registry import relative_to_suite

JOB_COMMAND_RE = re.compile(
    r"\brun\s+job\s+(?P<job_id>[A-Za-z0-9_.-]+)(?:\s+autocad\s+(?P<policy>[A-Za-z0-9_.-]+))?",
    re.IGNORECASE,
)


def parse_job_command(text: str) -> dict[str, str] | None:
    match = JOB_COMMAND_RE.search(text)
    if not match:
        return None
    policy = (match.group("policy") or "off").lower()
    return {
        "job_id": match.group("job_id"),
        "autocad_live_policy": normalize_autocad_policy(policy),
    }


def normalize_autocad_policy(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"off", "disabled", "skip", "skipped"}:
        return "off"
    if normalized in {"on", "auto", "auto_start_if_needed"}:
        return "auto_start_if_needed"
    return normalized


def run_pipeline_command(command: dict[str, str], paths: RuntimePaths) -> dict[str, Any]:
    ensure_autocad_import_path(paths)
    from autocad_mcp.technical_office.pipeline import run_job

    job_id = command["job_id"]
    job_dir = paths.jobs_import_root / job_id
    if not job_dir.exists():
        raise FileNotFoundError(f"Job input folder not found: {relative_to_suite(job_dir, paths)}")

    output_dir = paths.jobs_output_root / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    project_name = _load_project_name(job_dir)
    result = run_job(
        job_dir,
        output_root=output_dir,
        autocad_live_policy=command["autocad_live_policy"],
        project_name=project_name,
    )
    return _summarize_result(asdict(result), paths)


def format_job_summary(summary: dict[str, Any]) -> str:
    produced = summary["produced"]
    manual_reviews = summary["manual_reviews"]
    review_status = "manual_review_clear" if not manual_reviews else "manual_review_required"
    lines = [
        f"İş {summary['job_id']} tamamlandı. ok={str(summary['ok']).lower()}",
        f"Manuel inceleme durumu: {review_status} ({len(manual_reviews)} kayıt)",
        f"Çıktı klasörü: {summary['output_dir']}",
        f"Özet: {summary['summary_path']}",
        "Üretilen dosyalar:",
    ]
    for item in produced:
        lines.append(
            f"- {item['poz_no']}: DXF={item['dxf_path']} NC1={item['nc1_path']} QC={item['qc_path']} ok={str(item['ok']).lower()}"
        )
    if not produced:
        lines.append("- yok")
    if manual_reviews:
        lines.append("Manuel inceleme notları:")
        for review in manual_reviews[:5]:
            detail = review.get("detail") or review.get("reason")
            source_pdf = review.get("source_pdf") or "bilinmeyen PDF"
            lines.append(f"- {source_pdf}: {review.get('reason')} - {detail}")
            next_action = review.get("next_action")
            if next_action:
                lines.append(f"  Mudur aksiyonu: {next_action}")
        if len(manual_reviews) > 5:
            lines.append(f"- ... {len(manual_reviews) - 5} kayıt daha var")
    return "\n".join(lines)


def _load_project_name(job_dir: Path) -> str | None:
    metadata_path = job_dir / "job.json"
    if not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    project_name = metadata.get("project_name")
    return project_name if isinstance(project_name, str) and project_name.strip() else None


def _summarize_result(result: dict[str, Any], paths: RuntimePaths) -> dict[str, Any]:
    produced = []
    for item in result.get("produced", []):
        produced.append(
            {
                "poz_no": item["poz_no"],
                "dxf_path": relative_to_suite(item["dxf_path"], paths),
                "nc1_path": relative_to_suite(item["nc1_path"], paths),
                "qc_path": relative_to_suite(item["qc_path"], paths),
                "ok": bool(item["ok"]),
            }
        )
    return {
        "job_id": result["job_id"],
        "ok": bool(result["ok"]),
        "output_dir": relative_to_suite(result["output_dir"], paths),
        "summary_path": relative_to_suite(result["summary_path"], paths),
        "produced": produced,
        "manual_reviews": result.get("manual_reviews", []),
    }
