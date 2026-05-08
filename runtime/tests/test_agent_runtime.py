from __future__ import annotations

import json
import subprocess
import sys
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

from technical_office_runtime.app import _candidate_schema_path, _render_candidate_pages, _validate_approved_rows
from technical_office_runtime.agent_context import build_system_prompt, load_agent_context
from technical_office_runtime.cli import build_doctor_report
from technical_office_runtime.codex_bridge import (
    CodexBridge,
    CodexDoctorStatus,
    CodexMcpStatus,
    CodexRunRequest,
    _command_invocation,
    _repair_mojibake,
    _run_subprocess_streaming,
    inspect_codex_mcp,
)
from technical_office_runtime.config import RuntimePaths, get_paths, resolve_suite_root
from technical_office_runtime.manager_memory import get_manager_memory
from technical_office_runtime.orchestrator import (
    MANAGER_CODEX_READ_TIMEOUT_SECONDS,
    MANAGER_CODEX_WRITE_TIMEOUT_SECONDS,
    AgentOrchestrator,
)
from technical_office_runtime.session_store import load_session, save_session
from technical_office_runtime.tools import ToolRegistry


class FakeBridge:
    def __init__(self, content: str = "Codex cevabi", *, ok: bool = True, error: str | None = None) -> None:
        self.content = content
        self.ok = ok
        self.error = error
        self.calls: list[CodexRunRequest] = []

    def run(self, request: CodexRunRequest, *, job_id: str | None = None):
        self.calls.append(request)
        return SimpleNamespace(ok=self.ok, content=self.content, error=self.error, events=[], record=None)


def test_manager_context_loads_brain_and_skills():
    context = load_agent_context(get_paths(), "teknik-ofis-muduru")

    assert context.agent_id == "teknik-ofis-muduru"
    assert "Teknik ofis" in context.brain_text
    assert any(name == "IS_DAGITIMI.md" for name, _text in context.skill_texts)


def test_codex_skill_package_is_preferred_for_matching_shared_skill():
    context = load_agent_context(get_paths(), "kalite-kontrol")

    assert any(name == "SKILL.md" and "Cizim NC Kalite Kontrolu" in text for name, text in context.skill_texts)


def test_quality_control_context_directs_manual_review_to_manager():
    context = load_agent_context(get_paths(), "kalite-kontrol")
    prompt = build_system_prompt(context)

    assert "Kullanicidan OCR/vision provider acmasini" in prompt
    assert "teknik-ofis-muduru" in prompt
    assert "QC ok=true olmadan partlist/teslim acilmaz" in prompt


def test_tool_registry_returns_safe_errors_for_unknown_or_invalid_tools():
    registry = ToolRegistry(get_paths())

    assert registry.run("missing_tool", {})["ok"] is False
    invalid = registry.run("inspect_job", {})
    assert invalid["ok"] is False
    assert "job_id is required" in invalid["error"]


def test_manager_chat_uses_codex_bridge_read_only():
    bridge = FakeBridge("Mudur cevabi")

    result = AgentOrchestrator(bridge=bridge, allow_codex=True).run("bu sistemin kapsamini ozetle")

    assert result.used_llm is True
    assert result.content == "Mudur cevabi"
    assert bridge.calls[0].agent_id == "teknik-ofis-muduru"
    assert bridge.calls[0].sandbox == "read-only"
    assert bridge.calls[0].timeout_seconds == MANAGER_CODEX_READ_TIMEOUT_SECONDS


def test_manager_project_edit_request_uses_codex_workspace_write():
    bridge = FakeBridge("Duzeltme tamam")

    result = AgentOrchestrator(bridge=bridge, allow_codex=True).run(
        "runtime/technical_office_runtime/orchestrator.py dosyasinda mudur timeout sorununu duzelt ve test ekle"
    )

    assert result.used_llm is True
    assert result.content == "Duzeltme tamam"
    assert bridge.calls[0].agent_id == "teknik-ofis-muduru"
    assert bridge.calls[0].sandbox == "workspace-write"
    assert bridge.calls[0].timeout_seconds == MANAGER_CODEX_WRITE_TIMEOUT_SECONDS
    assert "Proje Duzeltme Modu" in bridge.calls[0].prompt


def test_manager_project_edit_fallback_is_specific_when_codex_fails():
    bridge = FakeBridge("", ok=False, error="Codex CLI timed out after 180 seconds.")

    result = AgentOrchestrator(bridge=bridge, allow_codex=True).run("CLAUDE.md dosyasini guncelle")

    assert result.used_llm is False
    assert bridge.calls[0].sandbox == "workspace-write"
    assert "proje/kod duzeltmesi" in result.content.lower()
    assert "toffice doctor" in result.content


def test_manager_project_edit_returns_partial_codex_message_on_timeout():
    bridge = FakeBridge("Raporlar hatanin ana hattini gosteriyor.", ok=False, error="Codex CLI timed out after 180 seconds.")

    result = AgentOrchestrator(bridge=bridge, allow_codex=True).run("runtime QC kapisini duzelt")

    assert result.used_llm is True
    assert result.fallback_reason == "Codex CLI timed out after 180 seconds."
    assert "Raporlar hatanin ana hattini gosteriyor." in result.content
    assert "tamamlanmadan kesildi" in result.content


def test_manager_greeting_is_local_and_fast():
    bridge = FakeBridge("Merhaba, buradayim.")

    result = AgentOrchestrator(bridge=bridge, allow_codex=True).run("merhaba")

    assert result.used_llm is False
    assert "buradayim" in result.content.lower()
    assert result.fallback_reason == "local_manager_chat"
    assert bridge.calls == []


def test_manager_capability_question_is_local_and_fast():
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(bridge=bridge, allow_codex=True).run("seninle ne is yapabiliriz")

    assert result.used_llm is False
    assert result.fallback_reason == "local_manager_chat"
    assert "pipeline" in result.content
    assert bridge.calls == []


def test_manager_can_create_agent_draft_from_natural_request(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run("yeni metraj kontrol ajani olustur")

    assert result.used_llm is False
    assert result.tool_results[0]["tool"] == "draft_agent"
    assert (tmp_path / "agents" / "_drafts").exists()


def test_manager_job_restart_plan_is_local_and_does_not_call_codex(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "deneme.pdf").write_bytes(_minimal_pdf_bytes())
    (job_dir / "approved_plate_specs.json").write_text('{"plates":[]}', encoding="utf-8")
    (output_dir / "job_summary.json").write_text('{"ok":true}', encoding="utf-8")
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run("danieli-1701 isime bastan baslamak istiyorum")

    assert result.used_llm is False
    assert result.fallback_reason == "local_job_restart_plan"
    assert bridge.calls == []
    assert "`danieli-1701 temiz baslat`" in result.content
    assert "approved_plate_specs.json" in result.content


def test_manager_restart_uses_recent_history_for_this_job_reference(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "deneme.pdf").write_bytes(_minimal_pdf_bytes())
    (output_dir / "job_summary.json").write_text('{"ok":false}', encoding="utf-8")
    bridge = FakeBridge("unused")
    history = [
        {"role": "user", "content": "danieli-1701 isinde ne durumdayiz"},
        {"role": "assistant", "content": "`danieli-1701` durum ozeti:\n- FSM: completed"},
    ]

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run("bu ise yeniden baslayacagiz", history=history)

    assert result.used_llm is False
    assert result.fallback_reason == "local_job_restart_plan"
    assert bridge.calls == []
    assert "`danieli-1701 temiz baslat`" in result.content


def test_manager_memory_records_job_facts_and_markdown(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    memory = get_manager_memory(paths.workspace_root)

    memory.record_turn(
        session_id="agent:teknik-ofis-muduru:test-memory",
        user_text=(
            "danieli-1701 isinde pdf 26 sayfa ama sistemimiz sadece 3 poz uretti; "
            "206 ve 207 poligon, pahlar eksik"
        ),
        assistant_text="Karar: Bu is tamamlanmis kabul edilmemeli.",
    )

    recall = memory.recall(
        session_id="agent:teknik-ofis-muduru:test-memory",
        message="bu ise yeniden baslayacagiz",
    )

    assert recall.primary_job_id == "danieli-1701"
    assert any(fact["fact_type"] == "issue" for fact in recall.facts)
    assert "206" in json.dumps(recall.to_payload(), ensure_ascii=False)
    assert (paths.workspace_root / "manager_vault" / "jobs" / "danieli-1701.md").exists()


def test_manager_memory_ignores_generic_job_list_as_current_job(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    memory = get_manager_memory(paths.workspace_root)

    memory.record_turn(
        session_id="agent:teknik-ofis-muduru:test-list-memory",
        user_text="merhaba",
        assistant_text="Mevcut isler:\n- `danieli-1701`: PDF=1\n- `test-001`: PDF=3",
    )

    recall = memory.recall(
        session_id="agent:teknik-ofis-muduru:test-list-memory",
        message="bu ise yeniden baslayacagiz",
        history=[{"role": "assistant", "content": "Mevcut isler:\n- `danieli-1701`: PDF=1\n- `test-001`: PDF=3"}],
    )

    assert recall.primary_job_id is None
    assert not (paths.workspace_root / "manager_vault" / "jobs" / "danieli-1701.md").exists()


def test_manager_restart_uses_persistent_memory_when_history_is_trimmed(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "deneme.pdf").write_bytes(_minimal_pdf_bytes())
    (output_dir / "job_summary.json").write_text('{"ok":false}', encoding="utf-8")
    memory = get_manager_memory(paths.workspace_root)
    memory.record_turn(
        session_id="agent:teknik-ofis-muduru:test-trimmed",
        user_text="danieli-1701 isinde ne durumdayiz",
        assistant_text="`danieli-1701` durum ozeti:\nKarar: tamamlanmis kabul edilmemeli.",
    )
    recall = memory.recall(
        session_id="agent:teknik-ofis-muduru:test-trimmed",
        message="bu ise yeniden baslayacagiz",
    )
    message = (
        "bu ise yeniden baslayacagiz\n\n"
        f"[Mudur hafiza baglami: {json.dumps(recall.to_payload(), ensure_ascii=False)}]"
    )
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(message, history=[])

    assert result.used_llm is False
    assert result.fallback_reason == "local_job_restart_plan"
    assert bridge.calls == []
    assert "`danieli-1701 temiz baslat`" in result.content


def test_manager_confirmed_job_reset_archives_outputs_and_approval(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "deneme.pdf").write_bytes(_minimal_pdf_bytes())
    (job_dir / "approved_plate_specs.json").write_text('{"plates":[]}', encoding="utf-8")
    (output_dir / "job_summary.json").write_text('{"ok":true}', encoding="utf-8")
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run("danieli-1701 temiz baslat")

    assert result.used_llm is False
    assert result.fallback_reason == "local_job_reset"
    assert bridge.calls == []
    assert result.tool_results[0]["tool"] == "reset_job_for_rerun"
    assert not (job_dir / "approved_plate_specs.json").exists()
    assert output_dir.exists()
    assert (output_dir / "fsm_state.json").exists()
    archives = list((paths.workspace_root / "archive" / "jobs" / job_id).glob("*/reset_manifest.json"))
    assert archives


def test_manager_job_status_question_is_local_and_does_not_call_codex(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": "Danieli"}), encoding="utf-8")
    (job_dir / "deneme.pdf").write_bytes(_minimal_pdf_bytes(page_count=2))
    (job_dir / "approved_plate_specs.json").write_text('{"plates":[]}', encoding="utf-8")
    (output_dir / "fsm_state.json").write_text(json.dumps({"state": "awaiting_approval"}), encoding="utf-8")
    (output_dir / "pdf_diagnostics.json").write_text(
        json.dumps({"pdfs": [{"source_pdf": "deneme.pdf", "page_count": 26, "classification": "visual_text_required"}]}),
        encoding="utf-8",
    )
    (output_dir / "job_summary.json").write_text(
        json.dumps({"ok": True, "produced": [{"poz_no": "205"}, {"poz_no": "206"}, {"poz_no": "207"}], "manual_reviews": []}),
        encoding="utf-8",
    )
    (output_dir / "codex_candidates.json").write_text(
        json.dumps({"candidates": [{"poz_no": "205"}, {"poz_no": "206"}, {"poz_no": "207"}]}),
        encoding="utf-8",
    )
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run("danieli-1701 isindwe ne durumdayiz")

    assert result.used_llm is False
    assert result.fallback_reason == "local_job_status"
    assert bridge.calls == []
    assert "`danieli-1701` durum ozeti" in result.content
    assert "FSM: awaiting_approval" in result.content
    assert "toplam 26 sayfa" in result.content
    assert "Uretilen poz: 3" in result.content
    assert "tamamlanmis kabul edilmemeli" in result.content


def test_manager_selected_job_status_question_is_local(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "selected-001"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())
    (output_dir / "fsm_state.json").write_text(json.dumps({"state": "uploaded"}), encoding="utf-8")
    selected_context = json.dumps({"selected_job_id": job_id}, ensure_ascii=False)
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        f"bu isin durumunu ozetle\n\n[Secili is baglami: {selected_context}]"
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_job_status"
    assert bridge.calls == []
    assert "`selected-001` durum ozeti" in result.content
    assert "FSM: uploaded" in result.content


def test_non_manager_chat_is_disabled():
    bridge = FakeBridge()

    result = AgentOrchestrator(agent_id="kalite-kontrol", bridge=bridge, allow_codex=True).run("merhaba disinda cevap ver")

    assert result.used_llm is False
    assert "yalnizca `teknik-ofis-muduru`" in result.content
    assert bridge.calls == []


def test_runtime_ready_request_is_local_and_does_not_call_codex():
    bridge = FakeBridge()

    result = AgentOrchestrator(bridge=bridge, allow_codex=True).run("merhaba, sistem hazir mi?")

    assert result.used_llm is False
    assert bridge.calls == []
    assert "Technical Office Runtime" in result.content


def test_manager_issue_discussion_does_not_trigger_run_from_selected_job_context(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "pdf_diagnostics.json").write_text(
        json.dumps({"pdfs": [{"source_pdf": "deneme.pdf", "page_count": 26}]}),
        encoding="utf-8",
    )
    (output_dir / "job_summary.json").write_text(
        json.dumps({"ok": True, "produced": [{"poz_no": "205"}, {"poz_no": "206"}, {"poz_no": "207"}], "manual_reviews": []}),
        encoding="utf-8",
    )
    (output_dir / "codex_candidates.json").write_text(
        json.dumps({"candidates": [{"poz_no": "205"}, {"poz_no": "206"}, {"poz_no": "207"}]}),
        encoding="utf-8",
    )
    bridge = FakeBridge("unused")
    selected_context = json.dumps({"selected_job_id": job_id, "status": "completed"}, ensure_ascii=False)

    message = "pdf icerisinde toplam 26 sayfa var ama sistemimiz 3 adet uretti; bunu duzeltmeliyiz"
    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(f"{message}\n\n[Secili is baglami: {selected_context}]")

    assert result.used_llm is False
    assert result.fallback_reason == "local_manager_issue_discussion"
    assert result.tool_results == []
    assert bridge.calls == []
    assert "26 sayfa" in result.content
    assert "3 poz" in result.content
    assert "tamamlanmis kabul edilmemeli" in result.content


def test_manager_geometry_issue_is_captured_for_visual_analysis(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "pdf_diagnostics.json").write_text(
        json.dumps({"pdfs": [{"source_pdf": "deneme.pdf", "page_count": 26}]}),
        encoding="utf-8",
    )
    (output_dir / "job_summary.json").write_text(
        json.dumps({"ok": True, "produced": [{"poz_no": "205"}, {"poz_no": "206"}, {"poz_no": "207"}], "manual_reviews": []}),
        encoding="utf-8",
    )
    bridge = FakeBridge("unused")
    selected_context = json.dumps({"selected_job_id": job_id, "status": "completed"}, ensure_ascii=False)

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        "ayrica 206 ve 207 numarali pozlarin cizimi poligon seklinde ve bazi koselerinde pahlar var, "
        f"bunlar yapilmadi; gorselanaliz bolumune bunlari iletmen gerekiyor\n\n[Secili is baglami: {selected_context}]"
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_manager_issue_discussion"
    assert result.tool_results == []
    assert bridge.calls == []
    assert "206, 207" in result.content
    assert "gorsel analiz" in result.content
    assert "pah/kose" in result.content
    notes_path = output_dir / "manager_issue_notes.jsonl"
    assert notes_path.exists()
    note = json.loads(notes_path.read_text(encoding="utf-8").splitlines()[-1])
    assert note["affected_pozs"] == ["206", "207"]
    assert "pah/kose eksigi" in note["tags"]
    assert "poligon kontur" in note["tags"]


def test_natural_language_fallback_runs_job_without_codex():
    result = AgentOrchestrator(allow_codex=False).run("test-001 isini AutoCAD live kapali calistir")

    assert result.used_llm is False
    assert "test-001" in result.content
    assert "workspace/outputs/jobs/test-001" in result.content


def test_numeric_job_reference_missing_lists_available_jobs_without_codex():
    result = AgentOrchestrator(allow_codex=False).run("5223 isini calistir")

    assert result.used_llm is False
    assert "`5223` ID'li is" in result.content
    assert "`test-001`" in result.content


def test_draft_agent_writes_only_in_drafts(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    registry = ToolRegistry(paths)

    result = registry.run(
        "draft_agent",
        {"title": "Tekla DXF kontrol ajani", "mission": "Tekla DXF islerini kontrol etmek."},
    )

    assert result["ok"] is True
    assert result["draft_id"] == "tekla-dxf-kontrol-ajani"
    assert (tmp_path / "agents" / "_drafts" / "tekla-dxf-kontrol-ajani" / "AGENT.md").exists()
    assert not (tmp_path / "agents" / "tekla-dxf-kontrol-ajani").exists()


def test_approve_agent_activates_existing_draft(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    registry = ToolRegistry(paths, expose_approval_tool=True)
    draft = registry.run("draft_agent", {"title": "Tekla DXF kontrol ajani"})

    result = registry.run("approve_agent", {"draft_id": draft["draft_id"]})

    assert result == {"ok": True, "agent_id": "tekla-dxf-kontrol-ajani", "status": "active"}
    assert (tmp_path / "agents" / "tekla-dxf-kontrol-ajani" / "AGENT.md").exists()


def test_doctor_report_checks_codex(monkeypatch):
    monkeypatch.setattr(
        "technical_office_runtime.cli.inspect_codex",
        lambda: CodexDoctorStatus(
            executable="codex.cmd",
            login_ok=True,
            exec_help_ok=True,
            login_output="Logged in using ChatGPT",
            exec_help_output="Usage: codex exec",
        ),
    )
    monkeypatch.setattr(
        "technical_office_runtime.cli.inspect_codex_mcp",
        lambda paths: CodexMcpStatus(
            list_ok=True,
            get_ok=True,
            configured=True,
            points_to_suite=True,
            stale_path_detected=False,
            mcp_list_output="autocad-mcp",
            mcp_get_output=str(paths.suite_root / "mcp" / "autocad-mcp-server"),
            expected_server_path=str(paths.suite_root / "mcp" / "autocad-mcp-server"),
            fix_commands=[],
        ),
    )

    report = build_doctor_report()

    assert "Codex executable: codex.cmd" in report
    assert "Codex login: ready" in report
    assert "Codex autocad-mcp: ready" in report
    assert "technical-office/codex-cli" in report


def test_codex_mcp_doctor_detects_stale_global_path(monkeypatch, tmp_path):
    paths = _make_minimal_paths(tmp_path)
    stale_output = r"autocad-mcp C:\Users\elekc\OneDrive\Masaüstü\programlar\Technical_office_engineer\autocad-mcp-server\.venv\Scripts\python.exe -m autocad_mcp"

    monkeypatch.setattr("technical_office_runtime.codex_bridge.resolve_codex_executable", lambda: "codex.cmd")
    monkeypatch.setattr(
        "technical_office_runtime.codex_bridge._doctor_run",
        lambda args, timeout: subprocess.CompletedProcess(args, 0, stdout=stale_output, stderr=""),
    )

    status = inspect_codex_mcp(paths)

    assert status.configured is True
    assert status.points_to_suite is False
    assert status.stale_path_detected is True
    assert "mcp remove autocad-mcp" in status.fix_commands[0]


def test_active_files_do_not_reference_removed_ai_engines():
    suite_root = Path(__file__).resolve().parents[2]
    removed_terms = ("ge" + "mini", "ol" + "lama")
    roots = [
        suite_root / "PLAN.md",
        suite_root / "CLAUDE.md",
        suite_root / "README.md",
        suite_root / "Claude_Yetkinlikleri_Rehber.md",
        suite_root / ".claude",
        suite_root / "runtime" / "technical_office_runtime",
        suite_root / "mcp" / "autocad-mcp-server" / "src",
        suite_root / "agents",
        suite_root / "workspace" / "sessions",
    ]
    offenders: list[str] = []
    for root in roots:
        paths = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file()]
        for path in paths:
            if "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".png", ".pdf", ".xlsx", ".dxf", ".nc1"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            if any(term in text for term in removed_terms):
                offenders.append(str(path.relative_to(suite_root)))

    assert offenders == []


def test_resolve_suite_root_prefers_current_workspace_markers(monkeypatch, tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "registry.json").write_text("{}", encoding="utf-8")
    (tmp_path / "mcp" / "autocad-mcp-server").mkdir(parents=True)
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.delenv("TECH_OFFICE_SUITE_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert resolve_suite_root() == tmp_path.resolve()


def test_resolve_suite_root_accepts_runtime_working_directory(monkeypatch, tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "registry.json").write_text("{}", encoding="utf-8")
    (tmp_path / "mcp" / "autocad-mcp-server").mkdir(parents=True)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.delenv("TECH_OFFICE_SUITE_ROOT", raising=False)
    monkeypatch.chdir(runtime_dir)

    assert resolve_suite_root() == tmp_path.resolve()


def test_session_store_sanitizes_unsafe_pdf_guidance(tmp_path):
    session_id = "agent:kalite-kontrol:main"
    save_session(
        tmp_path,
        session_id,
        [
            {
                "role": "assistant",
                "content": (
                    'The job "danieli-1701" requires manual review because the PDF "deneme.pdf" '
                    "contains 79 pages. To proceed, please enable local OCR/vision.\n"
                    "\\boxed{Enable OCR/vision to proceed.}"
                ),
            }
        ],
    )

    messages = load_session(tmp_path, session_id)

    assert "please enable" not in messages[0]["content"].lower()
    assert "\\boxed" not in messages[0]["content"]
    assert "teknik-ofis-muduru incelemesi gerekiyor" in messages[0]["content"]
    assert "manual_review_required" in messages[0]["content"]


def test_codex_bridge_records_jsonl_events(tmp_path):
    paths = _make_minimal_paths(tmp_path)

    def runner(args, *, input, cwd, timeout):
        assert args[:2] == ["codex.cmd", "exec"]
        assert args[-1] == "-"
        assert "Prompt" in input
        return subprocess.CompletedProcess(args, 0, stdout='{"type":"delta","text":"hello"}\n', stderr="")

    bridge = CodexBridge(paths, executable="codex.cmd", runner=runner)
    result = bridge.run(CodexRunRequest(prompt="Prompt", agent_id="teknik-ofis-muduru", run_id="run-1"))

    assert result.ok is True
    assert result.content == "hello"
    assert result.record.exit_code == 0
    assert (tmp_path / ".state" / "codex-runs" / "run-1" / "events.jsonl").exists()


def test_codex_bridge_emits_events_to_callback(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    seen = []

    def runner(args, *, input, cwd, timeout):
        return subprocess.CompletedProcess(args, 0, stdout='{"type":"delta","text":"hello"}\n', stderr="")

    bridge = CodexBridge(paths, executable="codex.cmd", runner=runner)
    result = bridge.run(
        CodexRunRequest(prompt="Prompt", agent_id="teknik-ofis-muduru", run_id="run-callback"),
        on_event=seen.append,
    )

    assert result.ok is True
    assert seen == [{"type": "delta", "text": "hello"}]


def test_codex_bridge_rejects_malformed_jsonl(tmp_path):
    paths = _make_minimal_paths(tmp_path)

    def runner(args, *, input, cwd, timeout):
        return subprocess.CompletedProcess(args, 0, stdout="not-json\n", stderr="")

    result = CodexBridge(paths, executable="codex.cmd", runner=runner).run(
        CodexRunRequest(prompt="Prompt", agent_id="teknik-ofis-muduru", run_id="bad-json")
    )

    assert result.ok is False
    assert "Malformed Codex JSONL" in (result.error or "")


def test_codex_bridge_ignores_windows_process_termination_stdout(tmp_path):
    paths = _make_minimal_paths(tmp_path)

    def runner(args, *, input, cwd, timeout):
        stdout = (
            '{"type":"delta","text":"hello"}\n'
            "SUCCESS: The process with PID 1234 (child process of PID 5678) has been terminated.\n"
        )
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    result = CodexBridge(paths, executable="codex.cmd", runner=runner).run(
        CodexRunRequest(prompt="Prompt", agent_id="teknik-ofis-muduru", run_id="success-noise")
    )

    assert result.ok is True
    assert result.content == "hello"


def test_codex_bridge_repairs_windows_utf8_mojibake(tmp_path):
    paths = _make_minimal_paths(tmp_path)

    def runner(args, *, input, cwd, timeout):
        final_path = Path(args[args.index("--output-last-message") + 1])
        final_path.write_text("Bu sistem, Ã§elik yapÄ± iÅlerini yÃ¶netir.", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout='{"type":"turn.completed"}\n', stderr="")

    result = CodexBridge(paths, executable="codex.cmd", runner=runner).run(
        CodexRunRequest(prompt="Prompt", agent_id="teknik-ofis-muduru", run_id="mojibake")
    )

    assert result.ok is True
    assert result.content == "Bu sistem, çelik yapı işlerini yönetir."


def test_repair_mojibake_leaves_normal_text_unchanged():
    assert _repair_mojibake("çelik yapı işleri") == "çelik yapı işleri"


def test_command_invocation_runs_cmd_without_powershell_wrapper():
    command, use_shell = _command_invocation(["codex.cmd", "--version"])

    assert command == ["codex.cmd", "--version"]
    assert use_shell is False


def test_codex_bridge_summarizes_upgrade_error_without_html(tmp_path):
    paths = _make_minimal_paths(tmp_path)

    def runner(args, *, input, cwd, timeout):
        stderr = (
            "2026-05-06T13:35:11Z WARN startup remote plugin sync failed: <html><body>Forbidden</body></html>\n"
            "startup websocket prewarm setup failed: {\"message\":\"The 'gpt-5.5' model requires a newer version of Codex. Please upgrade to the latest app or CLI and try again.\"}\n"
        )
        return subprocess.CompletedProcess(args, 1, stdout="", stderr=stderr)

    result = CodexBridge(paths, executable="codex.cmd", runner=runner).run(
        CodexRunRequest(prompt="Prompt", agent_id="teknik-ofis-muduru", run_id="upgrade-error")
    )

    assert result.ok is False
    assert "requires a newer version of Codex" in (result.error or "")
    assert "<html>" not in (result.error or "")


def test_codex_bridge_summarizes_plugin_403_without_html(tmp_path):
    paths = _make_minimal_paths(tmp_path)

    def runner(args, *, input, cwd, timeout):
        stderr = "remote plugin sync request to https://chatgpt.com/backend-api/plugins/list failed with status 403 Forbidden: <html><body>Cloudflare</body></html>"
        return subprocess.CompletedProcess(args, 1, stdout="", stderr=stderr)

    result = CodexBridge(paths, executable="codex.cmd", runner=runner).run(
        CodexRunRequest(prompt="Prompt", agent_id="teknik-ofis-muduru", run_id="plugin-403")
    )

    assert result.ok is False
    assert "403" in (result.error or "")
    assert "<html>" not in (result.error or "")
    assert len(result.error or "") < 600


def test_codex_bridge_reports_timeout(tmp_path):
    paths = _make_minimal_paths(tmp_path)

    def runner(args, *, input, cwd, timeout):
        raise subprocess.TimeoutExpired(args, timeout, output='{"type":"delta","text":"partial"}\n')

    result = CodexBridge(paths, executable="codex.cmd", runner=runner).run(
        CodexRunRequest(prompt="Prompt", agent_id="teknik-ofis-muduru", run_id="timeout", timeout_seconds=1)
    )

    assert result.ok is False
    assert "timed out" in (result.error or "")
    assert result.content == "partial"


def test_codex_bridge_prefers_json_event_error_over_stderr_warning(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    event_error = {
        "type": "error",
        "message": json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_json_schema",
                    "message": "Invalid schema for response_format 'codex_output_schema'",
                },
                "status": 400,
            }
        ),
    }

    def runner(args, *, input, cwd, timeout):
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout=json.dumps(event_error) + "\n",
            stderr="403 Forbidden while syncing analytics-events",
        )

    result = CodexBridge(paths, executable="codex.cmd", runner=runner).run(
        CodexRunRequest(prompt="Prompt", agent_id="pdf-visual-candidate", run_id="schema-error")
    )

    assert result.ok is False
    assert "invalid_json_schema" in (result.error or "")
    assert "403" not in (result.error or "")


def test_codex_streaming_timeout_works_without_stdout(tmp_path):
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        _run_subprocess_streaming(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            input="hello",
            cwd=str(tmp_path),
            timeout=1,
            events_path=tmp_path / "events.jsonl",
            on_event=None,
        )

    assert time.monotonic() - started < 3


def test_render_candidate_pages_uses_pymupdf(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "render-test"
    job_dir = paths.jobs_import_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())

    images = _render_candidate_pages(paths, job_id, ["input.pdf"])

    assert images
    assert images[0].suffix == ".png"
    assert images[0].exists()


def test_render_candidate_pages_renders_all_pages_for_visual_cli(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "render-all"
    job_dir = paths.jobs_import_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes(page_count=4))

    images = _render_candidate_pages(paths, job_id, ["input.pdf"])

    assert len(images) == 4
    assert [path.name for path in images] == ["input-p1.png", "input-p2.png", "input-p3.png", "input-p4.png"]


def test_candidate_schema_is_strict_and_avoids_stale_file(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    stale_schema_path = paths.suite_root / ".state" / "codex-runs" / "plate-candidates.schema.json"
    stale_schema_path.parent.mkdir(parents=True)
    stale_schema_path.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"candidates": {"type": "array", "items": {"type": "object"}}},
                "required": ["candidates"],
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )

    resolved = _candidate_schema_path(paths)
    schema = json.loads(resolved.read_text(encoding="utf-8"))
    candidate = schema["properties"]["candidates"]["items"]

    assert resolved.name == "plate-candidates.v2.schema.json"
    assert candidate["additionalProperties"] is False
    assert set(candidate["properties"]) == set(candidate["required"])
    assert candidate["properties"]["holes"]["items"]["additionalProperties"] is False
    assert candidate["properties"]["slots"]["items"]["additionalProperties"] is False
    assert candidate["properties"]["corner_reliefs"]["items"]["additionalProperties"] is False


def test_approval_validation_blocks_chamfer_evidence_without_contour_details(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "approval-chamfer"
    job_dir = paths.jobs_import_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())
    rows = [
        {
            "candidate_id": "codex_cli-1",
            "source_pdf": "input.pdf",
            "source_page": 2,
            "poz_no": "206",
            "width": 240,
            "height": 150,
            "thickness": 8,
            "material": "S275J2",
            "quantity": 10,
            "holes": [],
            "slots": [],
            "corner_reliefs": [],
            "contour_type": "polygon",
            "confidence": 0.78,
            "evidence": "drawing shows 240 bottom width and 150 height with 30 mm side offsets",
        }
    ]

    validated, errors = _validate_approved_rows(rows, paths, job_id)

    assert validated == []
    assert errors
    assert "corner_reliefs is empty" in errors[0]["error"]


def test_approval_validation_blocks_incomplete_visual_page_coverage(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "approval-coverage"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes(page_count=4))
    (output_dir / "pdf_diagnostics.json").write_text(
        json.dumps(
            {
                "pdfs": [
                    {
                        "source_pdf": "input.pdf",
                        "page_count": 4,
                        "classification": "visual_text_required",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = [
        {
            "candidate_id": "codex_cli-1",
            "source_pdf": "input.pdf",
            "source_page": 1,
            "poz_no": "P100",
            "width": 200,
            "height": 100,
            "thickness": 10,
            "material": "S355",
            "quantity": 1,
            "holes": [],
            "slots": [],
            "corner_reliefs": [],
            "confidence": 0.8,
            "evidence": "page 1",
        }
    ]

    validated, errors = _validate_approved_rows(rows, paths, job_id)

    assert validated == []
    assert errors
    assert "requires visual review coverage for 4 pages" in errors[0]["error"]
    assert "Missing pages: 2, 3, 4" in errors[0]["error"]


def test_render_candidate_pages_reports_missing_pymupdf(monkeypatch, tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "missing-fitz"
    job_dir = paths.jobs_import_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("fitz missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(RuntimeError, match="PyMuPDF"):
        _render_candidate_pages(paths, job_id, ["input.pdf"])


def _make_minimal_paths(root: Path) -> RuntimePaths:
    (root / "agents").mkdir(parents=True)
    (root / "workspace" / "imports" / "jobs").mkdir(parents=True)
    (root / "workspace" / "outputs" / "jobs").mkdir(parents=True)
    (root / "mcp" / "autocad-mcp-server" / "src").mkdir(parents=True)
    (root / "agents" / "registry.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "suite": "technical-office-suite",
                "profile": "technical-office",
                "defaultModel": "technical-office/codex-cli",
                "agents": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return RuntimePaths(
        suite_root=root,
        registry_path=root / "agents" / "registry.json",
        workspace_root=root / "workspace",
        jobs_import_root=root / "workspace" / "imports" / "jobs",
        jobs_output_root=root / "workspace" / "outputs" / "jobs",
        autocad_src=root / "mcp" / "autocad-mcp-server" / "src",
    )


def _minimal_pdf_bytes(page_count: int = 1) -> bytes:
    objects = ["1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj"]
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(page_count))
    objects.append(f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {page_count} >> endobj")
    for index in range(page_count):
        page_obj = 3 + index * 2
        content_obj = page_obj + 1
        stream = (
            f"BT /F1 12 Tf 10 180 Td (POZ: T{100 + index}) Tj ET\n"
            "0 0 m 200 0 l 200 100 l 0 100 l h S\n"
        )
        objects.append(
            f"{page_obj} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents {content_obj} 0 R >> endobj"
        )
        objects.append(f"{content_obj} 0 obj << /Length {len(stream)} >> stream\n{stream}\nendstream endobj")
    pdf = "%PDF-1.4\n" + "\n".join(objects) + "\ntrailer << /Root 1 0 R >>\n%%EOF\n"
    return pdf.encode("latin-1")
