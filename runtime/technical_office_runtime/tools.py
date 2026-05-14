from __future__ import annotations

import json
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .audit import get_audit_logger
from .completion import append_job_event, complete_approved_job, notify_job_blocked
from .config import RuntimePaths, ensure_autocad_import_path
from .job_fsm import JobState, get_fsm
from .job_runner import format_job_summary, run_pipeline_command
from .registry import active_agents, load_registry, relative_to_suite

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def as_function_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, paths: RuntimePaths, expose_approval_tool: bool = False) -> None:
        self.paths = paths
        specs = [
            ToolSpec(
                "list_jobs",
                "List isolated technical office jobs.",
                {"type": "object", "properties": {}, "additionalProperties": False},
                self.list_jobs,
            ),
            ToolSpec(
                "create_job",
                "Create a new job folder under workspace/imports/jobs/ with a job.json. Call this when the user wants to start a new job.",
                {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Unique job identifier, e.g. danieli-1701"},
                        "project_name": {"type": "string", "description": "Human-readable project name"},
                    },
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
                self.create_job,
            ),
            ToolSpec(
                "inspect_job",
                "Inspect PDFs, metadata, outputs, and QC state for a job.",
                {
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
                self.inspect_job,
            ),
            ToolSpec(
                "run_autocad_job",
                "Run the AutoCAD PDF to DXF/NC1/QC pipeline for a job.",
                {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "autocad_live_policy": {
                            "type": "string",
                            "enum": ["off", "auto_start_if_needed"],
                            "default": "off",
                        },
                    },
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
                self.run_autocad_job,
            ),
            ToolSpec(
                "reset_job_for_rerun",
                "Archive current outputs and manager-approved specs so an existing job can start over from its input PDFs.",
                {
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
                self.reset_job_for_rerun,
            ),
            ToolSpec(
                "read_job_summary",
                "Read a completed job summary.",
                {
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
                self.read_job_summary,
            ),
            ToolSpec(
                "read_qc_report",
                "Read and summarize a position QC report.",
                {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "poz_no": {"type": "string"},
                    },
                    "required": ["job_id", "poz_no"],
                    "additionalProperties": False,
                },
                self.read_qc_report,
            ),
            ToolSpec(
                "create_partlist",
                "Create an ERT Part_List_holes Excel file for QC ok=true rows in a completed job.",
                {
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
                self.create_partlist,
            ),
            ToolSpec(
                "list_agents",
                "List active technical office agents.",
                {"type": "object", "properties": {}, "additionalProperties": False},
                self.list_agents,
            ),
            ToolSpec(
                "read_agent_brain",
                "Read a registered agent brain document.",
                {
                    "type": "object",
                    "properties": {"agent_id": {"type": "string"}},
                    "required": ["agent_id"],
                    "additionalProperties": False,
                },
                self.read_agent_brain,
            ),
            ToolSpec(
                "draft_agent",
                "Create a draft agent under agents/_drafts. This does not activate the agent.",
                {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "mission": {"type": "string"},
                        "role": {"type": "string", "default": "specialist"},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
                self.draft_agent,
            ),
            ToolSpec(
                "approve_candidates",
                "Write approved_plate_specs.json for a job and trigger production pipeline. Used by manager chat after collecting corner_reliefs and other missing data conversationally.",
                {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "rows": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Candidate rows with all fields including corner_reliefs filled.",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Optional manager chat session that receives the final notification.",
                        },
                    },
                    "required": ["job_id", "rows"],
                    "additionalProperties": False,
                },
                self.approve_candidates,
            ),
        ]
        if expose_approval_tool:
            specs.append(
                ToolSpec(
                    "approve_agent",
                    "Activate an existing draft agent. Use only from explicit CLI approval.",
                    {
                        "type": "object",
                        "properties": {"draft_id": {"type": "string"}},
                        "required": ["draft_id"],
                        "additionalProperties": False,
                    },
                    self.approve_agent,
                )
            )
        self._tools = {spec.name: spec for spec in specs}

    def function_tools(self) -> list[dict[str, Any]]:
        return [spec.as_function_tool() for spec in self._tools.values()]

    def run(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        spec = self._tools.get(name)
        if spec is None:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return {"ok": False, "error": f"Invalid arguments for {name}: expected object"}
        try:
            return spec.handler(arguments)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def list_jobs(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "jobs": _list_job_records(self.paths)}

    def create_job(self, args: dict[str, Any]) -> dict[str, Any]:
        job_id = _required_string(args, "job_id")
        project_name = str(args.get("project_name") or job_id).strip() or job_id
        job_dir = self.paths.jobs_import_root / job_id
        if job_dir.exists():
            return {"ok": False, "error": f"Job already exists: {job_id}", "path": str(job_dir)}
        job_dir.mkdir(parents=True)
        job_meta = {
            "job_id": job_id,
            "project_name": project_name,
            "manager_agent": "teknik-ofis-muduru",
            "created_at": datetime.now().astimezone().isoformat(),
        }
        job_json_path = job_dir / "job.json"
        job_json_path.write_text(json.dumps(job_meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "ok": True,
            "job_id": job_id,
            "project_name": project_name,
            "path": relative_to_suite(job_dir, self.paths),
            "message": (
                f"`{job_id}` iş klasörü oluşturuldu: {relative_to_suite(job_dir, self.paths)}\n"
                f"Şimdi PDF dosyalarını bu klasöre kopyala, ardından işi çalıştır."
            ),
        }

    def inspect_job(self, args: dict[str, Any]) -> dict[str, Any]:
        job_id = _required_string(args, "job_id")
        job_dir = self.paths.jobs_import_root / job_id
        output_dir = self.paths.jobs_output_root / job_id
        if not job_dir.exists():
            return _job_not_found(self.paths, job_id)
        metadata = _read_json(job_dir / "job.json")
        pdfs = [
            {"name": path.name, "size_bytes": path.stat().st_size}
            for path in sorted(job_dir.glob("*.pdf"))
        ]
        summary = _read_json(output_dir / "job_summary.json")
        return {
            "ok": True,
            "job_id": job_id,
            "input_dir": relative_to_suite(job_dir, self.paths),
            "output_dir": relative_to_suite(output_dir, self.paths),
            "metadata": metadata,
            "pdfs": pdfs,
            "has_outputs": output_dir.exists(),
            "summary": summary,
        }

    def reset_job_for_rerun(self, args: dict[str, Any]) -> dict[str, Any]:
        job_id = _required_string(args, "job_id")
        job_dir = self.paths.jobs_import_root / job_id
        output_dir = self.paths.jobs_output_root / job_id
        if not job_dir.exists():
            return _job_not_found(self.paths, job_id)

        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        archive_dir = self.paths.workspace_root / "archive" / "jobs" / job_id / timestamp
        moved: list[dict[str, str]] = []

        if output_dir.exists():
            archive_dir.mkdir(parents=True, exist_ok=True)
            target = archive_dir / "outputs"
            shutil.move(str(output_dir), str(target))
            moved.append({"from": relative_to_suite(output_dir, self.paths), "to": relative_to_suite(target, self.paths)})

        approved_path = job_dir / "approved_plate_specs.json"
        if approved_path.exists():
            archive_dir.mkdir(parents=True, exist_ok=True)
            target = archive_dir / "approved_plate_specs.json"
            shutil.move(str(approved_path), str(target))
            moved.append({"from": relative_to_suite(approved_path, self.paths), "to": relative_to_suite(target, self.paths)})

        output_dir.mkdir(parents=True, exist_ok=True)
        get_fsm(self.paths.jobs_output_root).force_transition(job_id, JobState.UPLOADED, reason="manager_reset_for_rerun")
        manifest = {
            "job_id": job_id,
            "created_at": datetime.now().astimezone().isoformat(),
            "action": "reset_job_for_rerun",
            "moved": moved,
        }
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "reset_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        return {
            "ok": True,
            "job_id": job_id,
            "archive_dir": relative_to_suite(archive_dir, self.paths),
            "moved": moved,
            "message": (
                f"`{job_id}` temiz baslangic icin hazirlandi. Eski cikti/onaylar arsivlendi: "
                f"{relative_to_suite(archive_dir, self.paths)}. Sonraki adim pipeline'i yeniden calistirmak."
            ),
        }

    def run_autocad_job(self, args: dict[str, Any]) -> dict[str, Any]:
        job_id = _required_string(args, "job_id")
        job_dir = self.paths.jobs_import_root / job_id
        if not job_dir.exists():
            return _job_not_found(self.paths, job_id)
        policy = str(args.get("autocad_live_policy") or "off")
        try:
            summary = run_pipeline_command(
                {"job_id": job_id, "autocad_live_policy": policy},
                self.paths,
            )
        except Exception as exc:
            _write_journal_entry(
                self.paths,
                agent="teknik-ofis-muduru",
                entry_type="job_failure",
                body=(
                    f"**İş:** `{job_id}`\n"
                    f"**Hata:** `{type(exc).__name__}: {exc}`\n\n"
                    "**Aksiyon:** Pipeline çalıştırılamadı. Manuel inceleme gerekli. "
                    "Input klasörünü ve job.json dosyasını kontrol et."
                ),
            )
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "job_id": job_id}
        if not summary.get("ok", True):
            failed_pozs = [p["poz_no"] for p in summary.get("produced", []) if not p.get("ok")]
            if failed_pozs:
                _write_journal_entry(
                    self.paths,
                    agent="kalite-kontrol",
                    entry_type="qc_failure",
                    body=(
                        f"**İş:** `{job_id}`\n"
                        f"**QC ok=false Pozlar:** {', '.join(f'`{p}`' for p in failed_pozs)}\n\n"
                        "**Sorumlu:** Atanan AutoCAD uzmanı (journal'daki son 'started' kaydını kontrol et).\n"
                        "**Aksiyon:** Başarısız pozları yeniden üret. "
                        "Geometri sorunu varsa manual_review_required olarak işaretle."
                    ),
                )
        return {"ok": True, "summary": summary, "message": format_job_summary(summary)}

    def read_job_summary(self, args: dict[str, Any]) -> dict[str, Any]:
        job_id = _required_string(args, "job_id")
        path = self.paths.jobs_output_root / job_id / "job_summary.json"
        if not (self.paths.jobs_import_root / job_id).exists():
            return _job_not_found(self.paths, job_id)
        data = _read_json(path)
        if data is None:
            return {"ok": False, "error": f"Missing job summary for {job_id}"}
        return {"ok": True, "path": relative_to_suite(path, self.paths), "summary": data}

    def read_qc_report(self, args: dict[str, Any]) -> dict[str, Any]:
        job_id = _required_string(args, "job_id")
        poz_no = _required_string(args, "poz_no")
        path = self.paths.jobs_output_root / job_id / poz_no / f"{poz_no}_qc.json"
        if not (self.paths.jobs_import_root / job_id).exists():
            return _job_not_found(self.paths, job_id)
        data = _read_json(path)
        if data is None:
            return {"ok": False, "error": f"Missing QC report: {job_id}/{poz_no}"}
        return {
            "ok": True,
            "path": relative_to_suite(path, self.paths),
            "qc": data,
            "summary": _summarize_qc(data),
        }

    def create_partlist(self, args: dict[str, Any]) -> dict[str, Any]:
        job_id = _required_string(args, "job_id")
        job_dir = self.paths.jobs_import_root / job_id
        output_dir = self.paths.jobs_output_root / job_id
        if not job_dir.exists():
            return _job_not_found(self.paths, job_id)
        ensure_autocad_import_path(self.paths)
        from autocad_mcp.technical_office.partlist import create_partlist

        result = create_partlist(job_dir, output_dir)
        return {"ok": result.ok, "partlist": result.to_dict()}

    def list_agents(self, _args: dict[str, Any]) -> dict[str, Any]:
        registry = load_registry(self.paths)
        return {"ok": True, "agents": active_agents(registry)}

    def read_agent_brain(self, args: dict[str, Any]) -> dict[str, Any]:
        agent_id = _required_string(args, "agent_id")
        path = self.paths.suite_root / "agents" / agent_id / "AGENT.md"
        if not path.exists():
            return {"ok": False, "error": f"Agent brain not found: {agent_id}"}
        return {
            "ok": True,
            "agent_id": agent_id,
            "path": relative_to_suite(path, self.paths),
            "brain": path.read_text(encoding="utf-8"),
        }

    def draft_agent(self, args: dict[str, Any]) -> dict[str, Any]:
        title = _required_string(args, "title")
        role = str(args.get("role") or "specialist").strip() or "specialist"
        mission = str(args.get("mission") or "").strip() or f"{title} rolü için teknik ofis görevlerini yürütmek."
        draft_id = _slugify(title)
        draft_dir = self.paths.suite_root / "agents" / "_drafts" / draft_id
        draft_dir.mkdir(parents=True, exist_ok=True)
        draft_path = draft_dir / "AGENT.md"
        draft_text = _draft_agent_markdown(title=title, role=role, mission=mission)
        draft_path.write_text(draft_text, encoding="utf-8")
        return {
            "ok": True,
            "draft_id": draft_id,
            "path": relative_to_suite(draft_path, self.paths),
            "status": "draft_waiting_for_approval",
        }

    def approve_candidates(self, args: dict[str, Any]) -> dict[str, Any]:
        """Write approved_plate_specs.json and run production pipeline (manager chat flow)."""
        job_id = _required_string(args, "job_id")
        rows = args.get("rows")
        if not isinstance(rows, list) or not rows:
            return {"ok": False, "error": "rows must be a non-empty list of candidate objects"}
        job_dir = self.paths.jobs_import_root / job_id
        if not job_dir.exists():
            return _job_not_found(self.paths, job_id)
        normalized_rows = _normalize_candidate_rows_for_job(rows, job_dir)
        for row in normalized_rows:
            if not isinstance(row, dict):
                continue
            clean_reliefs = []
            for relief in row.get("corner_reliefs") or []:
                if not isinstance(relief, dict):
                    continue
                rtype = str(relief.get("relief_type") or relief.get("type") or "")
                if _normalize_relief_type(rtype) == "polygon_contour" or not relief.get("corner"):
                    continue  # polygon_contour sentinel veya eksik corner — atla
                relief["relief_type"] = _normalize_relief_type(rtype or "chamfer")
                clean_reliefs.append(relief)
            row["corner_reliefs"] = clean_reliefs
        approval = {
            "approved_by": "teknik-ofis-muduru",
            "approved_at": datetime.now().astimezone().isoformat(),
            "plates": normalized_rows,
        }
        (job_dir / "approved_plate_specs.json").write_text(
            json.dumps(approval, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        fsm = get_fsm(self.paths.jobs_output_root)
        fsm.force_transition(job_id, JobState.PRODUCING, reason="manager_approved_via_chat")
        append_job_event(self.paths, job_id, "production_started", {"approved_count": len(normalized_rows)})
        pipeline = self.run_autocad_job({"job_id": job_id, "autocad_live_policy": "off"})
        if not pipeline.get("ok"):
            blocked = notify_job_blocked(
                self.paths,
                job_id,
                reason=str(pipeline.get("error") or "production_failed"),
                session_id=str(args.get("session_id") or ""),
                state=JobState.FAILED,
            )
            return {
                "ok": False,
                "error": blocked.get("error"),
                "message": blocked.get("message"),
                "job_id": job_id,
                "pipeline": pipeline,
            }
        summary = pipeline.get("summary") if isinstance(pipeline.get("summary"), dict) else {}
        if summary.get("ok") is not True:
            manual_reviews = summary.get("manual_reviews") if isinstance(summary.get("manual_reviews"), list) else []
            reason = "production_manual_review_pending" if manual_reviews else "qc_failed"
            append_job_event(
                self.paths,
                job_id,
                "qc_completed",
                {
                    "ok": False,
                    "manual_review_count": len(manual_reviews),
                    "produced_count": len(summary.get("produced", [])) if isinstance(summary.get("produced"), list) else 0,
                },
            )
            blocked = notify_job_blocked(
                self.paths,
                job_id,
                reason=reason,
                session_id=str(args.get("session_id") or ""),
                summary=summary,
            )
            return {
                "ok": False,
                "job_id": job_id,
                "approved_count": len(normalized_rows),
                "pipeline_summary": pipeline.get("message", ""),
                "summary": summary,
                "error": blocked.get("error"),
                "message": blocked.get("message"),
            }
        poz_nos = [str(p.get("poz_no", "")) for p in normalized_rows if isinstance(p, dict)]
        get_audit_logger(self.paths.workspace_root).log_approval(job_id, "teknik-ofis-muduru", len(normalized_rows), poz_nos)
        completion = complete_approved_job(
            self.paths,
            job_id,
            summary,
            approved_count=len(normalized_rows),
            session_id=str(args.get("session_id") or ""),
        )
        return {
            "ok": bool(completion.get("ok")),
            "job_id": job_id,
            "approved_count": len(normalized_rows),
            "pipeline_summary": pipeline.get("message", ""),
            "summary": summary,
            "partlist": completion.get("partlist"),
            "retrospective": completion.get("retrospective"),
            "message": completion.get("message"),
            "error": completion.get("error"),
        }

    def approve_agent(self, args: dict[str, Any]) -> dict[str, Any]:
        draft_id = _required_string(args, "draft_id")
        draft_dir = self.paths.suite_root / "agents" / "_drafts" / draft_id
        draft_path = draft_dir / "AGENT.md"
        if not draft_path.exists():
            return {"ok": False, "error": f"Draft not found: {draft_id}"}
        active_dir = self.paths.suite_root / "agents" / draft_id
        if active_dir.exists():
            return {"ok": False, "error": f"Agent already exists: {draft_id}"}
        active_dir.mkdir(parents=True)
        shutil.copy2(draft_path, active_dir / "AGENT.md")
        registry = load_registry(self.paths)
        agents = registry.setdefault("agents", [])
        if not isinstance(agents, list):
            return {"ok": False, "error": "Registry agents field is not a list."}
        agents.append(
            {
                "id": draft_id,
                "name": _title_from_slug(draft_id),
                "role": "specialist",
                "status": "active",
                "brain": f"{draft_id}/AGENT.md",
                "skills": ["_shared"],
            }
        )
        self.paths.registry_path.write_text(
            json.dumps(registry, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"ok": True, "agent_id": draft_id, "status": "active"}


def _normalize_candidate_rows_for_job(rows: list[Any], job_dir: Path) -> list[dict[str, Any]]:
    pdf_names = {path.name for path in job_dir.glob("*.pdf")}
    single_pdf = next(iter(pdf_names)) if len(pdf_names) == 1 else None
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        source_pdf = str(item.get("source_pdf") or "").strip()
        if single_pdf and source_pdf not in pdf_names:
            item["source_pdf"] = single_pdf
        normalized.append(item)
    return normalized


def _normalize_relief_type(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"pah", "bevel", "beveled", "chamfered"}:
        return "chamfer"
    if normalized in {"round_relief", "rounded", "radius"}:
        return "round"
    return normalized or "chamfer"


def _required_string(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required.")
    return value.strip()


def _list_job_records(paths: RuntimePaths) -> list[dict[str, Any]]:
    jobs = []
    root = paths.jobs_import_root
    if root.exists():
        for job_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            jobs.append(
                {
                    "job_id": job_dir.name,
                    "path": relative_to_suite(job_dir, paths),
                    "pdf_count": len(list(job_dir.glob("*.pdf"))),
                    "has_metadata": (job_dir / "job.json").exists(),
                }
            )
    return jobs


def _job_not_found(paths: RuntimePaths, job_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"Job not found: {job_id}",
        "job_id": job_id,
        "available_jobs": _list_job_records(paths),
    }


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _summarize_qc(qc: dict[str, Any]) -> dict[str, Any]:
    plate = qc.get("plate_spec") if isinstance(qc.get("plate_spec"), dict) else {}
    return {
        "ok": bool(qc.get("ok")),
        "autocad_live_check": qc.get("autocad_live_check"),
        "poz_no": plate.get("poz_no"),
        "material": plate.get("material"),
        "thickness": plate.get("thickness"),
        "dimensions": {
            "width": plate.get("width"),
            "height": plate.get("height"),
        },
        "hole_count": len(plate.get("holes", [])) if isinstance(plate.get("holes"), list) else 0,
    }


def _slugify(value: str) -> str:
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    normalized = unicodedata.normalize("NFKD", value.translate(tr_map))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "draft-agent"


def _title_from_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def _write_journal_entry(paths: RuntimePaths, agent: str, entry_type: str, body: str) -> None:
    entries_dir = paths.suite_root / "journal" / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    path = entries_dir / f"{ts}_{entry_type}.md"
    content = (
        f"# Journal Kaydı — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"**Ajan:** {agent}  \n"
        f"**Tür:** {entry_type}\n\n"
        f"{body}\n"
    )
    try:
        path.write_text(content, encoding="utf-8")
    except OSError:
        pass


def _draft_agent_markdown(title: str, role: str, mission: str) -> str:
    return (
        f"# {title}\n\n"
        "## Status\n"
        "Draft. Kullanıcı onayı olmadan aktif ajan registry'ye eklenmez.\n\n"
        "## Role\n"
        f"{role}\n\n"
        "## Mission\n"
        f"{mission}\n\n"
        "## Safety\n"
        "- Eski proje dosyalarını silme veya taşıma.\n"
        "- Teknik üretim çıktılarında QC raporu olmadan teslim kararı verme.\n"
        "- Belirsiz PDF geometrisini uydurma; manuel inceleme iste.\n"
    )
