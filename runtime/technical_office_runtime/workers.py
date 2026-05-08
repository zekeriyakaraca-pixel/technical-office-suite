from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import structlog

from .config import RuntimePaths, get_paths
from .memory_bridge import compute_pdf_fingerprint, get_memory_bridge

log = structlog.get_logger(__name__)

CONSENSUS_CONFIDENCE_THRESHOLD = 0.3


class BackgroundWorkers:
    """
    Post-extraction parallel workers inspired by Ruflo's 12-worker pattern.

    Fires after successful extraction to:
      1. Pre-validate candidates (light QC pass without full pipeline)
      2. Update memory bridge with extraction patterns
      3. Check consensus between multiple candidate sources
    """

    def __init__(self, paths: RuntimePaths | None = None) -> None:
        self.paths = paths or get_paths()

    async def run_post_extraction_workers(self, job_id: str) -> dict[str, Any]:
        """Run all post-extraction workers in parallel."""
        results = await asyncio.gather(
            self._pre_validate_candidates(job_id),
            self._update_memory_bridge(job_id),
            self._check_consensus(job_id),
            return_exceptions=True,
        )
        labels = ["pre_validate", "memory_update", "consensus_check"]
        outcome: dict[str, Any] = {}
        for label, result in zip(labels, results):
            if isinstance(result, BaseException):
                log.warning("worker.error", job_id=job_id, worker=label, error=str(result))
                outcome[label] = {"ok": False, "error": str(result)}
            else:
                outcome[label] = result
        log.info("workers.completed", job_id=job_id, workers=list(outcome.keys()))
        return outcome

    async def _pre_validate_candidates(self, job_id: str) -> dict[str, Any]:
        """Light structural validation of codex_candidates.json."""
        candidates_path = self.paths.jobs_output_root / job_id / "codex_candidates.json"
        if not candidates_path.exists():
            return {"ok": True, "skipped": True, "reason": "no_candidates_file"}

        def _validate() -> dict[str, Any]:
            data = json.loads(candidates_path.read_text(encoding="utf-8"))
            candidates = data.get("candidates", [])
            issues = []
            for i, c in enumerate(candidates):
                if not isinstance(c, dict):
                    continue
                if not c.get("poz_no"):
                    issues.append({"index": i, "field": "poz_no", "issue": "missing"})
                if not c.get("width") or not c.get("height"):
                    issues.append({"index": i, "field": "dimensions", "issue": "missing_width_or_height"})
                if c.get("confidence", 0) < 0.1:
                    issues.append({"index": i, "field": "confidence", "issue": "very_low"})
            return {"ok": True, "count": len(candidates), "issues": issues, "issue_count": len(issues)}

        return await asyncio.to_thread(_validate)

    async def _update_memory_bridge(self, job_id: str) -> dict[str, Any]:
        """Record approved specs into the memory bridge for future similarity lookup."""
        approved_path = self.paths.jobs_import_root / job_id / "approved_plate_specs.json"
        if not approved_path.exists():
            return {"ok": True, "skipped": True, "reason": "no_approved_specs"}

        def _record() -> dict[str, Any]:
            specs_data = json.loads(approved_path.read_text(encoding="utf-8"))
            plates = specs_data.get("plates", [])
            if not plates:
                return {"ok": True, "skipped": True, "reason": "empty_plates"}

            bridge = get_memory_bridge(self.paths.workspace_root)
            pdf_files = list((self.paths.jobs_import_root / job_id).glob("*.pdf"))
            if not pdf_files:
                return {"ok": True, "skipped": True, "reason": "no_pdf_files"}

            # Use first PDF as representative fingerprint
            fingerprint = compute_pdf_fingerprint(pdf_files[0])
            avg_conf = sum(p.get("confidence", 0.5) for p in plates if isinstance(p, dict)) / max(len(plates), 1)
            bridge.record_extraction(
                pdf_fingerprint=fingerprint,
                job_id=job_id,
                plate_specs=plates,
                confidence=avg_conf,
                source="manager_approved",
            )
            return {"ok": True, "fingerprint": fingerprint[:12], "plate_count": len(plates), "confidence": round(avg_conf, 3)}

        return await asyncio.to_thread(_record)

    async def _check_consensus(self, job_id: str) -> dict[str, Any]:
        """
        Compare extraction_candidates.json (deterministic) vs codex_candidates.json (visual).
        Flag conflict if confidence gap > CONSENSUS_CONFIDENCE_THRESHOLD.
        """
        det_path = self.paths.jobs_output_root / job_id / "extraction_candidates.json"
        codex_path = self.paths.jobs_output_root / job_id / "codex_candidates.json"

        if not det_path.exists() or not codex_path.exists():
            return {"ok": True, "skipped": True, "reason": "single_source"}

        def _compare() -> dict[str, Any]:
            det_data = json.loads(det_path.read_text(encoding="utf-8"))
            codex_data = json.loads(codex_path.read_text(encoding="utf-8"))
            det_candidates = det_data.get("candidates", [])
            codex_candidates = codex_data.get("candidates", [])

            det_avg = _avg_confidence(det_candidates)
            codex_avg = _avg_confidence(codex_candidates)
            gap = abs(det_avg - codex_avg)
            conflict = gap > CONSENSUS_CONFIDENCE_THRESHOLD

            result: dict[str, Any] = {
                "ok": True,
                "deterministic_count": len(det_candidates),
                "codex_count": len(codex_candidates),
                "deterministic_avg_confidence": round(det_avg, 3),
                "codex_avg_confidence": round(codex_avg, 3),
                "confidence_gap": round(gap, 3),
                "conflict": conflict,
            }
            if conflict:
                result["escalation_message"] = (
                    f"⚠ İki kaynak arasında güven farkı yüksek ({gap:.0%}). "
                    "Müdür onayı öncesinde her iki aday listesini karşılaştırın."
                )
                log.warning("consensus.conflict", job_id=job_id, gap=gap)
            return result

        return await asyncio.to_thread(_compare)


def _avg_confidence(candidates: list[Any]) -> float:
    valid = [c.get("confidence", 0.0) for c in candidates if isinstance(c, dict)]
    return sum(valid) / max(len(valid), 1)


def get_workers(paths: RuntimePaths | None = None) -> BackgroundWorkers:
    return BackgroundWorkers(paths)
