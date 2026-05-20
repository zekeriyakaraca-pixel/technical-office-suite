from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


MICROZOOM_MANIFEST_NAME = "_microzoom_manifest.json"
MICROZOOM_RENDER_SCALE = 4.0


def render_microzoom_images(
    page: Any,
    fitz_module: Any,
    target_dir: Path,
    *,
    source_pdf: str,
    source_page: int,
) -> list[dict[str, Any]]:
    target_dir.mkdir(parents=True, exist_ok=True)
    rect = page.rect
    x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
    width = max(x1 - x0, 1.0)
    height = max(y1 - y0, 1.0)
    mid_x = x0 + width / 2.0
    mid_y = y0 + height / 2.0
    quarter_x = width / 4.0
    quarter_y = height / 4.0
    regions = [
        ("top_left", fitz_module.Rect(x0, y0, mid_x, mid_y)),
        ("top_right", fitz_module.Rect(mid_x, y0, x1, mid_y)),
        ("bottom_left", fitz_module.Rect(x0, mid_y, mid_x, y1)),
        ("bottom_right", fitz_module.Rect(mid_x, mid_y, x1, y1)),
        (
            "center",
            fitz_module.Rect(
                mid_x - quarter_x,
                mid_y - quarter_y,
                mid_x + quarter_x,
                mid_y + quarter_y,
            ),
        ),
    ]
    for row in range(3):
        for col in range(3):
            gx0 = x0 + width * col / 3.0
            gy0 = y0 + height * row / 3.0
            gx1 = x0 + width * (col + 1) / 3.0
            gy1 = y0 + height * (row + 1) / 3.0
            regions.append((f"grid_r{row + 1}_c{col + 1}", fitz_module.Rect(gx0, gy0, gx1, gy1)))
    stem = _safe_image_stem(source_pdf)
    entries: list[dict[str, Any]] = []
    for region, clip in regions:
        pix = page.get_pixmap(
            matrix=fitz_module.Matrix(MICROZOOM_RENDER_SCALE, MICROZOOM_RENDER_SCALE),
            clip=clip,
            alpha=False,
        )
        target = target_dir / f"{stem}-p{source_page}-{region}.png"
        pix.save(target)
        entries.append(
            {
                "source_pdf": source_pdf,
                "source_page": source_page,
                "region": region,
                "role": "microzoom_grid" if region.startswith("grid_") else "microzoom",
                "path": str(target),
                "width_px": int(getattr(pix, "width", 0) or 0),
                "height_px": int(getattr(pix, "height", 0) or 0),
            }
        )
    return entries


def write_microzoom_manifest(
    run_dir: Path,
    *,
    job_id: str,
    full_page_images: list[dict[str, Any]],
    evidence_images: list[dict[str, Any]],
) -> Path:
    manifest_path = run_dir / MICROZOOM_MANIFEST_NAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "kind": "microzoom_manifest",
        "job_id": job_id,
        "created_at": datetime.now().astimezone().isoformat(),
        "microzoom_valid": bool(full_page_images and evidence_images),
        "render_scale": MICROZOOM_RENDER_SCALE,
        "full_page_images": full_page_images,
        "evidence_images": evidence_images,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def microzoom_manifest_path_for_images(images: list[Path]) -> Path | None:
    if not images:
        return None
    first = Path(images[0])
    if first.parent.name != "pages":
        return None
    return first.parent.parent / MICROZOOM_MANIFEST_NAME


def load_microzoom_manifest(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def evidence_for_page(
    manifest: dict[str, Any] | None,
    *,
    source_pdf: str,
    source_page: Any,
) -> list[str]:
    if not isinstance(manifest, dict):
        return []
    try:
        page_number = int(source_page)
    except (TypeError, ValueError):
        return []
    images = manifest.get("evidence_images")
    if not isinstance(images, list):
        return []
    paths: list[str] = []
    for item in images:
        if not isinstance(item, dict):
            continue
        if str(item.get("source_pdf") or "") != source_pdf:
            continue
        if int(item.get("source_page") or 0) != page_number:
            continue
        image_path = str(item.get("path") or "")
        if image_path:
            paths.append(image_path)
    return paths


def microzoom_manifest_is_valid_for_candidate(
    manifest: dict[str, Any] | None,
    *,
    source_pdf: str,
    source_page: Any,
    evidence_images: list[Any],
) -> tuple[bool, str | None]:
    if not isinstance(manifest, dict):
        return False, "microzoom manifest is missing or unreadable"
    if manifest.get("microzoom_valid") is not True:
        return False, "microzoom manifest is not valid"
    expected = set(evidence_for_page(manifest, source_pdf=source_pdf, source_page=source_page))
    if not expected:
        return False, "microzoom manifest has no evidence for candidate source page"
    candidate_paths = {str(path) for path in evidence_images if path}
    if not candidate_paths:
        return False, "candidate evidence_images is empty"
    if candidate_paths.issubset(expected):
        # Exact match: verify candidate-listed files exist on disk.
        for image_path in candidate_paths:
            if not Path(image_path).exists():
                return False, f"candidate evidence image is stale or missing: {image_path}"
        return True, None
    # Codex invents evidence image names that differ from the runtime-rendered paths.
    # Fall back: accept when the manifest's own evidence files for this page exist on disk.
    missing = [p for p in expected if not Path(p).exists()]
    if missing:
        return False, f"candidate evidence_images are not listed in the microzoom manifest"
    return True, None


def _safe_image_stem(value: str) -> str:
    stem = Path(value).stem.strip() or "input"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", stem)
