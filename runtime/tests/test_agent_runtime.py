from __future__ import annotations

import json
import subprocess
import sys
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

from technical_office_runtime.app import app, _candidate_schema_path, _extract_codex_candidates, _render_candidate_pages, _validate_approved_rows
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
from technical_office_runtime.guided_flows import FLOW_MANAGER_ACTION_CONFIRMATION, get_guided_flow_store
from technical_office_runtime.manager_memory import get_manager_memory
from technical_office_runtime.orchestrator import (
    MANAGER_CODEX_READ_TIMEOUT_SECONDS,
    MANAGER_CODEX_WRITE_TIMEOUT_SECONDS,
    AgentOrchestrator,
    _parse_corner_reliefs_by_pending_candidate,
)
from technical_office_runtime.session_store import load_session, save_session
from technical_office_runtime.tools import ToolRegistry


class FakeBridge:
    def __init__(self, content: str = "Codex cevabi", *, ok: bool = True, error: str | None = None) -> None:
        self.content = content
        self.ok = ok
        self.error = error
        self.calls: list[CodexRunRequest] = []

    def run(self, request: CodexRunRequest, *, job_id: str | None = None, on_event=None):
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


def test_visual_analysis_training_docs_are_wired():
    root = Path(__file__).resolve().parents[2]
    required_files = [
        root / "agents" / "_shared" / "skills" / "GORSEL_ANALIZ_PROTOKOLU.md",
        root / "agents" / "_shared" / "skills" / "MIKRO_ZOOM_PROTOKOLU.md",
        root / "agents" / "_shared" / "skills" / "PDF_POZ_OKUMA.md",
        root / "agents" / "_shared" / "skills" / "PLAKA_GEOMETRI_CIKARMA.md",
        root / "agents" / "teknik-ofis-muduru" / "AGENT.md",
        root / "agents" / "autocad-uzman-1" / "AGENT.md",
        root / "agents" / "autocad-uzman-2" / "AGENT.md",
        root / "agents" / "kalite-kontrol" / "AGENT.md",
    ]

    for path in required_files:
        assert path.exists()
    wired_files = [
        root / "agents" / "_shared" / "skills" / "PDF_POZ_OKUMA.md",
        root / "agents" / "_shared" / "skills" / "PLAKA_GEOMETRI_CIKARMA.md",
        root / "agents" / "teknik-ofis-muduru" / "AGENT.md",
        root / "agents" / "autocad-uzman-1" / "AGENT.md",
        root / "agents" / "autocad-uzman-2" / "AGENT.md",
        root / "agents" / "kalite-kontrol" / "AGENT.md",
    ]
    for path in wired_files:
        assert "GORSEL_ANALIZ_PROTOKOLU" in path.read_text(encoding="utf-8")
    protocol = (root / "agents" / "_shared" / "skills" / "GORSEL_ANALIZ_PROTOKOLU.md").read_text(encoding="utf-8")
    microzoom = (root / "agents" / "_shared" / "skills" / "MIKRO_ZOOM_PROTOKOLU.md").read_text(encoding="utf-8")
    assert "source_trace" in protocol
    assert "analysis_confidence" in protocol
    assert "microzoom_manifest_path" in protocol
    assert "_microzoom_manifest.json" in microzoom
    assert "stale" in microzoom


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


def test_manager_memory_binds_contextual_turn_to_recent_job(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    memory = get_manager_memory(paths.workspace_root)
    session_id = "agent:teknik-ofis-muduru:test-contextual-memory"

    memory.record_turn(
        session_id=session_id,
        user_text="danieli-1701 isinde ne durumdayiz",
        assistant_text="`danieli-1701` durum ozeti: eksik sayfa kapsami var.",
    )
    memory.record_turn(
        session_id=session_id,
        user_text="1. adimdan baslayalim",
        assistant_text="`danieli-1701` icin 1. adimi baslattim.",
        history=[
            {"role": "user", "content": "danieli-1701 isinde ne durumdayiz"},
            {"role": "assistant", "content": "`danieli-1701` durum ozeti: eksik sayfa kapsami var."},
        ],
    )

    recall = memory.recall(session_id=session_id, message="2. adima gecelim")
    event_text = json.dumps(recall.to_payload(), ensure_ascii=False)

    assert recall.primary_job_id == "danieli-1701"
    assert "1. adimdan baslayalim" in event_text
    assert any("danieli-1701" in event.get("job_ids", []) for event in recall.recent_events)


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


def test_failed_job_status_creates_manager_action_confirmation_flow(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "failed-001"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "approved_plate_specs.json").write_text(
        json.dumps(
            {
                "plates": [
                    {
                        "poz_no": "4039",
                        "width": 200,
                        "height": 100,
                        "thickness": 10,
                        "corner_reliefs": [
                            {"corner": "bottom_left_inner", "radius": 10, "relief_type": "round_relief"}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "fsm_state.json").write_text(json.dumps({"state": "failed"}), encoding="utf-8")
    (output_dir / "events.jsonl").write_text(
        json.dumps({"type": "failed", "payload": {"error": "ApprovedSpecValidationError"}}),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id}, ensure_ascii=False)
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        f"bu isin durumunu ozetle\n\n[Secili is baglami: {selected_context}]",
        session_id="agent:teknik-ofis-muduru:test",
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_manager_action_confirmation"
    assert bridge.calls == []
    assert "Neden:" in result.content
    assert "Onerilen duzeltme:" in result.content
    assert "Onay verirsen uygulayacagim:" in result.content
    flow = get_guided_flow_store(paths.workspace_root).get_open(
        session_id="agent:teknik-ofis-muduru:test",
        job_id=job_id,
        flow_type=FLOW_MANAGER_ACTION_CONFIRMATION,
    )
    assert flow is not None
    assert flow.action_type == "approved_spec_repair_rerun"


def test_manager_action_confirmation_applies_open_flow(tmp_path, monkeypatch):
    paths = _make_minimal_paths(tmp_path)
    job_id = "failed-apply-001"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "approved_plate_specs.json").write_text(
        json.dumps(
            {
                "plates": [
                    {
                        "poz_no": "4039",
                        "width": 200,
                        "height": 100,
                        "thickness": 10,
                        "corner_reliefs": [
                            {"corner": "bottom_left_inner", "radius": 10, "relief_type": "round"}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "fsm_state.json").write_text(json.dumps({"state": "failed"}), encoding="utf-8")
    selected_context = json.dumps({"selected_job_id": job_id}, ensure_ascii=False)
    session_id = "agent:teknik-ofis-muduru:test-apply"
    orch = AgentOrchestrator(paths, bridge=FakeBridge("unused"), allow_codex=True)
    orch.run(f"son durum\n\n[Secili is baglami: {selected_context}]", session_id=session_id)
    calls: list[tuple[str, str | None]] = []

    def fake_apply(paths_arg, tools_arg, job_id_arg, *, session_id=None):
        calls.append((job_id_arg, session_id))
        return {
            "ok": True,
            "job_id": job_id_arg,
            "fsm_state": "completed",
            "summary": {"ok": True, "produced": [{"poz_no": "4039"}], "manual_reviews": []},
            "partlist": {"path": "D-28_partlist.xlsx"},
        }

    monkeypatch.setattr("technical_office_runtime.orchestrator._apply_approved_spec_repair_and_rerun", fake_apply)

    result = orch.run(f"yap\n\n[Secili is baglami: {selected_context}]", session_id=session_id)

    assert calls == [(job_id, session_id)]
    assert result.fallback_reason == "local_manager_action_apply"
    assert "aksiyonunu uyguladim" in result.content
    assert "Uretilen poz: 1" in result.content
    flow = get_guided_flow_store(paths.workspace_root).get_open(
        session_id=session_id,
        job_id=job_id,
        flow_type=FLOW_MANAGER_ACTION_CONFIRMATION,
    )
    assert flow is None


def test_bare_yap_without_open_manager_action_does_not_route_to_codex(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run("yap", session_id="agent:teknik-ofis-muduru:test-empty")

    assert result.fallback_reason == "local_manager_action_missing"
    assert bridge.calls == []
    assert "bekleyen mudur aksiyonu" in result.content


def test_manager_action_cancel_clears_flow_without_applying(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "failed-cancel-001"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    spec = {
        "plates": [
            {
                "poz_no": "4039",
                "width": 200,
                "height": 100,
                "thickness": 10,
                "corner_reliefs": [{"corner": "bottom_left_inner", "radius": 10, "relief_type": "round"}],
            }
        ]
    }
    (job_dir / "approved_plate_specs.json").write_text(json.dumps(spec), encoding="utf-8")
    (output_dir / "fsm_state.json").write_text(json.dumps({"state": "failed"}), encoding="utf-8")
    selected_context = json.dumps({"selected_job_id": job_id}, ensure_ascii=False)
    session_id = "agent:teknik-ofis-muduru:test-cancel"
    orch = AgentOrchestrator(paths, bridge=FakeBridge("unused"), allow_codex=True)
    orch.run(f"son durum\n\n[Secili is baglami: {selected_context}]", session_id=session_id)

    result = orch.run(f"iptal et\n\n[Secili is baglami: {selected_context}]", session_id=session_id)

    assert result.fallback_reason == "local_manager_action_cancelled"
    assert json.loads((job_dir / "approved_plate_specs.json").read_text(encoding="utf-8")) == spec
    flow = get_guided_flow_store(paths.workspace_root).get_open(
        session_id=session_id,
        job_id=job_id,
        flow_type=FLOW_MANAGER_ACTION_CONFIRMATION,
    )
    assert flow is None


def test_manager_manual_review_question_lists_reviews_without_codex(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "fsm_state.json").write_text(json.dumps({"state": "awaiting_approval"}), encoding="utf-8")
    (output_dir / "job_summary.json").write_text(
        json.dumps(
            {
                "ok": False,
                "produced": [{"poz_no": "206"}],
                "manual_reviews": [
                    {
                        "reason": "manager_geometry_issue_open",
                        "page": 2,
                        "poz_no": "206",
                        "source_pdf": "deneme.pdf",
                        "detail": "Aday geometri duzeltilmeden QC ok=true teslim kapisini acamaz.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "needs_manager_approval"}, ensure_ascii=False)
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        f"hangi pozlar icin manuel inceleme belirledin\n\n[Secili is baglami: {selected_context}]"
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_manual_review_details"
    assert bridge.calls == []
    assert "manual_review_required.json" in result.content
    assert "Poz 206" in result.content
    assert "Karar: Bu maddeler kapanmadan teslim/partlist acilmamali." in result.content

    detail_result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        f"manuel inceleme gerektiren seyler nedir\n\n[Secili is baglami: {selected_context}]"
    )

    assert detail_result.used_llm is False
    assert detail_result.fallback_reason == "local_manual_review_details"
    assert "Poz 206" in detail_result.content


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
    (job_dir / "deneme.pdf").write_bytes(_minimal_pdf_bytes(page_count=26))
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


def test_manager_rejects_completed_status_when_user_reports_open_errors(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "456"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "4042.pdf").write_bytes(_minimal_pdf_bytes(page_count=1))
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "job_summary.json").write_text(
        json.dumps({"ok": True, "produced": [{"poz_no": "4042"}], "manual_reviews": []}),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "completed"}, ensure_ascii=False)
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        "hayir henuz tamamlanmadi 456 numrali isle ilgili tespit ettigim hatalar var"
        f"\n\n[Secili is baglami: {selected_context}]"
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_manager_issue_discussion"
    assert bridge.calls == []
    assert "sadece ozetle gecmem yanlis" in result.content
    notes_path = output_dir / "manager_issue_notes.jsonl"
    assert notes_path.exists()
    note = json.loads(notes_path.read_text(encoding="utf-8").splitlines()[-1])
    assert note["status"] == "open"
    assert "hata bildirimi" in note["tags"]

    status = AgentOrchestrator(paths, bridge=FakeBridge("unused"), allow_codex=True).run(
        f"456 son durum\n\n[Secili is baglami: {selected_context}]"
    )
    assert status.fallback_reason == "local_job_status"
    assert "Acik mudur notu: 1" in status.content
    assert "tamamlanmis kabul edilmemeli" in status.content


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


def test_manager_geometry_issue_extracts_numbered_dxf_poz_without_poz_word(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "job_summary.json").write_text(
        json.dumps({"ok": True, "produced": [{"poz_no": "210"}], "manual_reviews": []}),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "completed"}, ensure_ascii=False)
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        "210 numarali uretilen dxf pdf icindeki cizimle ayni degil; "
        "sol ust kosedeki pah 10x120 olmali, 120 uzunlugu uzun kenar dogrultusunda olmali"
        f"\n\n[Secili is baglami: {selected_context}]"
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_manager_issue_discussion"
    assert "`210`" in result.content
    note = json.loads((output_dir / "manager_issue_notes.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert note["affected_pozs"] == ["210"]
    assert "120" not in note["affected_pozs"]
    assert "pah/kose eksigi" in note["tags"]


def test_manager_action_request_corrects_latest_poz_corner_and_reruns(tmp_path):
    base = _make_minimal_paths(tmp_path)
    paths = RuntimePaths(
        suite_root=base.suite_root,
        registry_path=base.registry_path,
        workspace_root=base.workspace_root,
        jobs_import_root=base.jobs_import_root,
        jobs_output_root=base.jobs_output_root,
        autocad_src=resolve_suite_root() / "mcp" / "autocad-mcp-server" / "src",
    )
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes(page_count=1))
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (job_dir / "approved_plate_specs.json").write_text(
        json.dumps(
            {
                "approved_by": "teknik-ofis-muduru",
                "plates": [
                    {
                        "poz_no": "210",
                        "width": 96,
                        "height": 150,
                        "thickness": 8,
                        "material": "S275J2",
                        "quantity": 1,
                        "holes": [],
                        "slots": [],
                        "corner_reliefs": [
                            {"corner": "top_left", "radius": 10, "relief_type": "chamfer"},
                            {"corner": "bottom_right", "radius": 10, "relief_type": "chamfer"},
                        ],
                        "source_page": 1,
                        "source_pdf": "input.pdf",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "manager_issue_notes.jsonl").write_text(
        json.dumps(
            {
                "status": "open",
                "tags": ["pah/kose eksigi"],
                "affected_pozs": ["210"],
                "message": "Poz 210 sol ust kosedeki pah 10x120 olmali, 120 uzun kenar dogrultusunda.",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "awaiting_approval"}, ensure_ascii=False)

    result = AgentOrchestrator(paths, bridge=FakeBridge("unused"), allow_codex=True).run(
        f"baska hata yok simdi aksiyon zamani bu pozu duzeltelim\n\n[Secili is baglami: {selected_context}]",
        session_id="agent:teknik-ofis-muduru:test-correction",
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_poz_correction_action"
    assert "Bana hatalari su formatta" not in result.content
    assert "Poz 210 icin aksiyonu uyguladim" in result.content
    approved = json.loads((job_dir / "approved_plate_specs.json").read_text(encoding="utf-8"))
    relief = approved["plates"][0]["corner_reliefs"][0]
    assert relief["corner"] == "top_left"
    assert relief["x_offset"] == 10.0
    assert relief["y_offset"] == 120.0
    qc = json.loads((output_dir / "210" / "210_qc.json").read_text(encoding="utf-8"))
    qc_relief = qc["plate_spec"]["corner_reliefs"][0]
    assert qc_relief["x_offset"] == 10.0
    assert qc_relief["y_offset"] == 120.0
    assert qc["ok"] is True
    fsm = json.loads((output_dir / "fsm_state.json").read_text(encoding="utf-8"))
    assert fsm["state"] == "completed"
    note = json.loads((output_dir / "manager_issue_notes.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert note["status"] == "resolved"


def test_manager_hole_coordinate_correction_is_local_and_reruns(tmp_path):
    base = _make_minimal_paths(tmp_path)
    paths = RuntimePaths(
        suite_root=base.suite_root,
        registry_path=base.registry_path,
        workspace_root=base.workspace_root,
        jobs_import_root=base.jobs_import_root,
        jobs_output_root=base.jobs_output_root,
        autocad_src=resolve_suite_root() / "mcp" / "autocad-mcp-server" / "src",
    )
    job_id = "456"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes(page_count=1))
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (job_dir / "approved_plate_specs.json").write_text(
        json.dumps(
            {
                "approved_by": "teknik-ofis-muduru",
                "plates": [
                    {
                        "poz_no": "4042",
                        "width": 156.5,
                        "height": 175,
                        "thickness": 10,
                        "material": "S355JR",
                        "quantity": 2,
                        "holes": [
                            {"x": 85, "y": 75, "diameter": 17},
                            {"x": 114.5, "y": 109, "diameter": 17},
                        ],
                        "slots": [],
                        "corner_reliefs": [],
                        "source_page": 1,
                        "source_pdf": "input.pdf",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "completed"}, ensure_ascii=False)
    bridge = FakeBridge("unused", ok=False, error="Codex CLI timed out after 180 seconds.")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        "4042 numarali pozun olusturulan dxf dosyasini inceledigimde delik konumlarinin hatali "
        "olarak cizildigini gordum koordinat vermek gerekirse sol alt nokta 0,0 kabul edildiginde "
        "cizilen konum alt delik icin X=85 Y=75 olmasi gereken degerler X=85 Y=98,5"
        f"\n\n[Secili is baglami: {selected_context}]",
        session_id="agent:teknik-ofis-muduru:test-hole-correction",
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_hole_coordinate_correction"
    assert bridge.calls == []
    assert "delik koordinati duzeltmesini uyguladim" in result.content
    approved = json.loads((job_dir / "approved_plate_specs.json").read_text(encoding="utf-8"))
    holes = approved["plates"][0]["holes"]
    assert holes[0]["x"] == 85.0
    assert holes[0]["y"] == 98.5
    qc = json.loads((output_dir / "4042" / "4042_qc.json").read_text(encoding="utf-8"))
    assert qc["plate_spec"]["holes"][0]["y"] == 98.5
    notes = [json.loads(line) for line in (output_dir / "manager_issue_notes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert notes[-1]["status"] == "resolved"
    assert "delik koordinati" in notes[-1]["tags"]


def test_manager_supplied_position_info_writes_positions_and_reruns(tmp_path):
    base = _make_minimal_paths(tmp_path)
    paths = RuntimePaths(
        suite_root=base.suite_root,
        registry_path=base.registry_path,
        workspace_root=base.workspace_root,
        jobs_import_root=base.jobs_import_root,
        jobs_output_root=base.jobs_output_root,
        autocad_src=resolve_suite_root() / "mcp" / "autocad-mcp-server" / "src",
    )
    job_id = "api-partlist-blocked"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_placeholder_pdf_bytes())
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": "API Partlist Blocked"}), encoding="utf-8")
    initial_review = {
        "reason": "poz_no_not_found",
        "page": 1,
        "poz_no": None,
        "detail": "Bu sayfada teknik poz/parca numarasi algilanamadi.",
        "source_pdf": "input.pdf",
    }
    (output_dir / "job_summary.json").write_text(
        json.dumps({"job_id": job_id, "produced": [], "manual_reviews": [initial_review], "ok": False}),
        encoding="utf-8",
    )
    (output_dir / "manual_review_required.json").write_text(json.dumps([initial_review]), encoding="utf-8")
    (output_dir / "fsm_state.json").write_text(
        json.dumps({"job_id": job_id, "state": "awaiting_approval"}),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "awaiting_approval"}, ensure_ascii=False)

    result = AgentOrchestrator(paths, bridge=FakeBridge("unused"), allow_codex=True).run(
        f'poz biligisi "api-1" olarak alinabilir\n\n[Secili is baglami: {selected_context}]',
        session_id="agent:teknik-ofis-muduru:test-position",
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_position_info_resolution"
    assert "poz bilgisini uyguladim" in result.content
    assert "plate_geometry_not_found" in result.content
    positions = json.loads((job_dir / "positions.json").read_text(encoding="utf-8"))
    assert positions == {"positions": [{"poz_no": "api-1", "page": 1}]}
    summary = json.loads((output_dir / "job_summary.json").read_text(encoding="utf-8"))
    assert summary["ok"] is False
    assert summary["manual_reviews"][0]["reason"] == "plate_geometry_not_found"
    assert summary["manual_reviews"][0]["poz_no"] == "api-1"
    fsm = json.loads((output_dir / "fsm_state.json").read_text(encoding="utf-8"))
    assert fsm["state"] == "awaiting_approval"


def test_manager_page_exclusion_writes_config_and_reruns(tmp_path):
    base = _make_minimal_paths(tmp_path)
    paths = RuntimePaths(
        suite_root=base.suite_root,
        registry_path=base.registry_path,
        workspace_root=base.workspace_root,
        jobs_import_root=base.jobs_import_root,
        jobs_output_root=base.jobs_output_root,
        autocad_src=resolve_suite_root() / "mcp" / "autocad-mcp-server" / "src",
    )
    job_id = "d-28-20260513155258"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(
        _multi_page_pdf_bytes(
            [
                ["POZ: COVER1", "PROJECT TITLE PAGE"],
                ["POZ: P100", "PLAKA 90x40x6 S235"],
            ]
        )
    )
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": "D-28"}), encoding="utf-8")
    initial_review = {
        "reason": "plate_geometry_not_found",
        "page": 1,
        "poz_no": "COVER1",
        "detail": "Poz bilgisi bulundu, ancak plaka olculeri/geometrisi otomatik uretim icin guvenilir cikarilamadi.",
        "source_pdf": "input.pdf",
    }
    (output_dir / "job_summary.json").write_text(
        json.dumps({"job_id": job_id, "produced": [], "manual_reviews": [initial_review], "ok": False}),
        encoding="utf-8",
    )
    (output_dir / "manual_review_required.json").write_text(json.dumps([initial_review]), encoding="utf-8")
    (output_dir / "fsm_state.json").write_text(
        json.dumps({"job_id": job_id, "state": "awaiting_approval"}),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "awaiting_approval"}, ensure_ascii=False)

    result = AgentOrchestrator(paths, bridge=FakeBridge("unused"), allow_codex=True).run(
        (
            "Sayfa No: 1\n"
            "Sistemin Urettigi Deger/Tespit: Poz COVER1 bulundu ama plaka geometrisi cikarilamadi.\n"
            "Olmasi Gereken Deger/Aksiyon: bu sayfa baslik sayfasidir, plaka cizimi bulunmayan sayfalar atlanmali"
            f"\n\n[Secili is baglami: {selected_context}]"
        ),
        session_id="agent:teknik-ofis-muduru:test-page-exclusion",
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_page_exclusion"
    assert "sayfa atlama kararini uyguladim" in result.content
    exclusions = json.loads((job_dir / "page_exclusions.json").read_text(encoding="utf-8"))
    assert exclusions["excluded_pages"][0]["page"] == 1
    summary = json.loads((output_dir / "job_summary.json").read_text(encoding="utf-8"))
    assert summary["ok"] is True
    assert summary["manual_reviews"] == []
    assert [item["poz_no"] for item in summary["produced"]] == ["P100"]
    applied = json.loads((output_dir / "page_exclusions_applied.json").read_text(encoding="utf-8"))
    assert applied["excluded_pages"][0]["page"] == 1


def test_manager_mark_column_hint_reruns_and_relabels_reviews(tmp_path):
    base = _make_minimal_paths(tmp_path)
    paths = RuntimePaths(
        suite_root=base.suite_root,
        registry_path=base.registry_path,
        workspace_root=base.workspace_root,
        jobs_import_root=base.jobs_import_root,
        jobs_output_root=base.jobs_output_root,
        autocad_src=resolve_suite_root() / "mcp" / "autocad-mcp-server" / "src",
    )
    job_id = "d-28-20260513155258"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(
        _multi_page_pdf_bytes(
            [
                [
                    "FRONT VIE",
                    "TOP VIE",
                    "402",
                    "PL1022",
                    "S55JR",
                    "5.5",
                    "0.1",
                    "5.",
                    "0.",
                    "5.",
                    "DANIELI CONSTRUCTION",
                    "MARK",
                    "PROFILE",
                    "MATERIAL",
                    ".T",
                    "LENGTH mm",
                    "AREA m2",
                    "EIGHT kg",
                    "TOTAL",
                ]
            ]
        )
    )
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": "D-28"}), encoding="utf-8")
    initial_review = {
        "reason": "poz_no_not_found",
        "page": 1,
        "poz_no": None,
        "detail": "Bu sayfada teknik poz/parca numarasi algilanamadi.",
        "source_pdf": "input.pdf",
    }
    (output_dir / "job_summary.json").write_text(
        json.dumps({"job_id": job_id, "produced": [], "manual_reviews": [initial_review], "ok": False}),
        encoding="utf-8",
    )
    (output_dir / "manual_review_required.json").write_text(json.dumps([initial_review]), encoding="utf-8")
    (output_dir / "fsm_state.json").write_text(
        json.dumps({"job_id": job_id, "state": "awaiting_approval"}),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "awaiting_approval"}, ensure_ascii=False)

    result = AgentOrchestrator(paths, bridge=FakeBridge("unused"), allow_codex=True).run(
        (
            "sayfa 4 ve devam eden diger sayfalarda poz bilgisi sayfalarin alt kisminda "
            f"bulunan tabloda mark sutununda yazmaktadir\n\n[Secili is baglami: {selected_context}]"
        ),
        session_id="agent:teknik-ofis-muduru:test-mark-column",
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_mark_column_position_hint"
    assert "Mark sutunu poz okuma bilgisini uyguladim" in result.content
    hints = json.loads((job_dir / "position_hints.json").read_text(encoding="utf-8"))
    assert hints["hints"][0]["type"] == "mark_column_bottom_table"
    summary = json.loads((output_dir / "job_summary.json").read_text(encoding="utf-8"))
    assert summary["ok"] is False
    assert summary["manual_reviews"][0]["reason"] == "plate_geometry_not_found"
    assert summary["manual_reviews"][0]["poz_no"] == "402"


def test_manager_issue_discussion_includes_job_learning_summary(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "job_summary.json").write_text(
        json.dumps({"ok": True, "produced": [{"poz_no": "210"}], "manual_reviews": []}),
        encoding="utf-8",
    )
    (output_dir / "retrospective.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "produced_count": 26,
                "partlist": {"ok": True, "rows": 26},
                "calculated_metrics": [{"poz_no": "210"}],
                "learning": [
                    {
                        "agent_or_skill": "partlist",
                        "proposal": "Eksik metrikleri geometri ile hesapla.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "completed"}, ensure_ascii=False)
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        "bu iste gordugum hatayi belirtecegim, ayrica agent sistemimiz bu projede neler ogrendi"
        f"\n\n[Secili is baglami: {selected_context}]"
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_manager_issue_discussion"
    assert "`danieli-1701` agent ogrenim ozeti" in result.content
    assert "Retrospektif: workspace/outputs/jobs/danieli-1701/retrospective.json" in result.content
    assert "partlist: Eksik metrikleri geometri ile hesapla." in result.content


def test_backfill_result_response_does_not_crash_without_written_artifacts():
    from technical_office_runtime.orchestrator import _format_backfill_result_response

    content = _format_backfill_result_response(
        "456",
        {
            "status": "awaiting_approval",
            "retrospective": {},
            "memory_bridge": {"ok": False},
            "vault_path": None,
        },
    )

    assert "Backfill tamamlandi" in content
    assert "awaiting_approval" in content


def test_manager_apply_decision_marks_selected_job_awaiting_approval_without_codex(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "fsm_state.json").write_text(json.dumps({"state": "completed"}), encoding="utf-8")
    (output_dir / "pdf_diagnostics.json").write_text(
        json.dumps({"pdfs": [{"source_pdf": "deneme.pdf", "page_count": 26, "classification": "visual_text_required"}]}),
        encoding="utf-8",
    )
    (output_dir / "job_summary.json").write_text(
        json.dumps({"ok": False, "produced": [{"poz_no": "205"}, {"poz_no": "206"}, {"poz_no": "207"}], "manual_reviews": []}),
        encoding="utf-8",
    )
    (output_dir / "manager_issue_notes.jsonl").write_text(
        json.dumps(
            {
                "status": "open",
                "tags": ["pah/kose eksigi"],
                "affected_pozs": ["206", "207", "1701"],
                "message": "206 ve 207 pahli ama dikdortgen uretilmis",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "completed"}, ensure_ascii=False)
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        f"bu soylediklerini yap\n\n[Secili is baglami: {selected_context}]"
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_manager_decision_apply"
    assert bridge.calls == []
    assert result.tool_results[0]["tool"] == "apply_manager_decisions"
    assert "FSM: awaiting_approval" in result.content
    assert "Etkilenen pozlar: 206, 207" in result.content
    assert "Etkilenen pozlar: 206, 207, 1701" not in result.content
    fsm = json.loads((output_dir / "fsm_state.json").read_text(encoding="utf-8"))
    assert fsm["state"] == "awaiting_approval"


def test_manager_continue_to_complete_job_is_local_plan_without_codex(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "fsm_state.json").write_text(json.dumps({"state": "awaiting_approval"}), encoding="utf-8")
    (output_dir / "pdf_diagnostics.json").write_text(
        json.dumps({"pdfs": [{"source_pdf": "deneme.pdf", "page_count": 26, "classification": "visual_text_required"}]}),
        encoding="utf-8",
    )
    (output_dir / "job_summary.json").write_text(
        json.dumps(
            {
                "ok": False,
                "produced": [{"poz_no": "205"}, {"poz_no": "206"}, {"poz_no": "207"}],
                "manual_reviews": [{"reason": "approved_specs_missing_visual_pages"}],
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "codex_candidates.json").write_text(
        json.dumps({"candidates": [{"poz_no": "205"}, {"poz_no": "206"}, {"poz_no": "207"}]}),
        encoding="utf-8",
    )
    (output_dir / "manager_issue_notes.jsonl").write_text(
        json.dumps({"status": "open", "tags": ["pah/kose eksigi"], "affected_pozs": ["206", "207", "1701"]}) + "\n",
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "needs_manager_approval"}, ensure_ascii=False)
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        f"devam edelim bu isi tamamlamak icin\n\n[Secili is baglami: {selected_context}]"
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_job_completion_plan"
    assert bridge.calls == []
    assert "dogrudan teslim/partlist" in result.content
    assert "Geometri notu olan pozlar: 206, 207" in result.content
    assert "Geometri notu olan pozlar: 206, 207, 1701" not in result.content
    assert "corner_reliefs" in result.content
    assert "awaiting_approval" in result.content


def test_manager_completion_step_one_is_local_without_codex(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "deneme.pdf").write_bytes(_minimal_pdf_bytes(page_count=26))
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "fsm_state.json").write_text(json.dumps({"state": "awaiting_approval"}), encoding="utf-8")
    (output_dir / "pdf_diagnostics.json").write_text(
        json.dumps({"pdfs": [{"source_pdf": "deneme.pdf", "page_count": 26, "classification": "visual_text_required"}]}),
        encoding="utf-8",
    )
    (output_dir / "job_summary.json").write_text(
        json.dumps(
            {
                "ok": False,
                "produced": [{"poz_no": "205"}, {"poz_no": "206"}, {"poz_no": "207"}],
                "manual_reviews": [{"reason": "approved_specs_missing_visual_pages"}],
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "codex_candidates.json").write_text(
        json.dumps({"candidates": [{"poz_no": "205"}, {"poz_no": "206"}, {"poz_no": "207"}]}),
        encoding="utf-8",
    )
    (output_dir / "manager_issue_notes.jsonl").write_text(
        json.dumps({"status": "open", "tags": ["pah/kose eksigi"], "affected_pozs": ["206", "207", "1701"]}) + "\n",
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "needs_manager_approval"}, ensure_ascii=False)
    bridge = FakeBridge('{"candidates":[]}')

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        f"1. adimdan bslayalim\n\n[Secili is baglami: {selected_context}]"
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_job_completion_step"
    assert bridge.calls == []
    assert "1. adimi baslattim" in result.content
    assert "sayfa 4-26" in result.content
    assert "Korunan geometri notlari: 206, 207" in result.content
    assert "Korunan geometri notlari: 206, 207, 1701" not in result.content

    pdf_control = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        f"pdf uzerinden sen kontrol et\n\n[Secili is baglami: {selected_context}]"
    )
    option_control = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        f"1 numarali secevnektan devam edelim\n\n[Secili is baglami: {selected_context}]"
    )

    assert pdf_control.used_llm is False
    assert pdf_control.fallback_reason == "local_missing_candidate_extraction"
    assert "PDF uzerinden" in pdf_control.content
    assert len(bridge.calls) == 1
    assert option_control.used_llm is False
    assert option_control.fallback_reason == "local_job_completion_step"
    assert "1. adimi baslattim" in option_control.content
    assert len(bridge.calls) == 1


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
    # gemini is an allowed exception for stateless manager chat (GEMINI_API_KEY env var).
    # Only truly forbidden engines are listed here.
    suite_root = Path(__file__).resolve().parents[2]
    removed_terms = ("ol" + "lama",)
    roots = [
        suite_root / "PLAN.md",
        suite_root / "README.md",
        suite_root / "Claude_Yetkinlikleri_Rehber.md",
        suite_root / ".claude",
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

    images, _evidence_meta = _render_candidate_pages(paths, job_id, ["input.pdf"])

    assert images
    assert images[0].suffix == ".png"
    assert images[0].exists()
    manifest_path = images[0].parent.parent / "_microzoom_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["microzoom_valid"] is True
    assert manifest["render_scale"] == 4.0
    assert manifest["full_page_images"]
    assert manifest["evidence_images"]
    assert all(Path(item["path"]).exists() for item in manifest["evidence_images"])


def test_render_candidate_pages_renders_all_pages_for_visual_cli(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "render-all"
    job_dir = paths.jobs_import_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes(page_count=4))

    images, _evidence_meta = _render_candidate_pages(paths, job_id, ["input.pdf"])

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

    assert resolved.name == "plate-candidates.v4.schema.json"
    assert candidate["additionalProperties"] is False
    assert set(candidate["properties"]) == set(candidate["required"])
    assert candidate["properties"]["holes"]["items"]["additionalProperties"] is False
    assert candidate["properties"]["slots"]["items"]["additionalProperties"] is False
    assert candidate["properties"]["corner_reliefs"]["items"]["additionalProperties"] is False
    assert "x_offset" in candidate["properties"]["corner_reliefs"]["items"]["required"]
    assert "y_offset" in candidate["properties"]["corner_reliefs"]["items"]["required"]
    assert candidate["properties"]["polygon_vertices"]["items"]["additionalProperties"] is False
    assert candidate["properties"]["source_trace"]["additionalProperties"] is False
    for field in ("source_trace", "analysis_confidence", "uncertainties", "microzoom_manifest_path", "evidence_images"):
        assert field in candidate["required"]


def test_approval_validation_blocks_visual_candidate_without_microzoom_manifest(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "approval-missing-manifest"
    job_dir = paths.jobs_import_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())
    rows = [
        {
            "candidate_id": "visual-1",
            "provider": "codex_cli",
            "approval_required": True,
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
            "analysis_confidence": 0.8,
            "uncertainties": [],
            "source_trace": {
                "source_pdf": "input.pdf",
                "source_page": 1,
                "method": "codex_cli",
                "microzoom_manifest_path": str(tmp_path / "missing_manifest.json"),
                "evidence_images": [str(tmp_path / "missing.png")],
            },
            "microzoom_manifest_path": str(tmp_path / "missing_manifest.json"),
            "evidence_images": [str(tmp_path / "missing.png")],
            "evidence": "page 1",
        }
    ]

    validated, errors = _validate_approved_rows(rows, paths, job_id)

    assert validated == []
    assert errors
    assert "microzoom manifest" in errors[0]["error"]


def test_approval_validation_blocks_stale_visual_evidence_image(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "approval-stale-evidence"
    job_dir = paths.jobs_import_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())
    images, _evidence_meta = _render_candidate_pages(paths, job_id, ["input.pdf"])
    manifest_path = images[0].parent.parent / "_microzoom_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_image = next(item["path"] for item in manifest["evidence_images"] if item["source_page"] == 1)
    Path(evidence_image).unlink()
    rows = [
        {
            "candidate_id": "visual-1",
            "provider": "codex_cli",
            "approval_required": True,
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
            "analysis_confidence": 0.8,
            "uncertainties": [],
            "source_trace": {
                "source_pdf": "input.pdf",
                "source_page": 1,
                "method": "codex_cli",
                "microzoom_manifest_path": str(manifest_path),
                "evidence_images": [evidence_image],
            },
            "microzoom_manifest_path": str(manifest_path),
            "evidence_images": [evidence_image],
            "evidence": "page 1",
        }
    ]

    validated, errors = _validate_approved_rows(rows, paths, job_id)

    assert validated == []
    assert errors
    assert "stale or missing" in errors[0]["error"]


def test_approval_validation_preserves_valid_visual_evidence(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "approval-valid-evidence"
    job_dir = paths.jobs_import_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())
    images, _evidence_meta = _render_candidate_pages(paths, job_id, ["input.pdf"])
    manifest_path = images[0].parent.parent / "_microzoom_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_images = [
        item["path"]
        for item in manifest["evidence_images"]
        if item["source_pdf"] == "input.pdf" and item["source_page"] == 1
    ]
    rows = [
        {
            "candidate_id": "visual-1",
            "provider": "codex_cli",
            "approval_required": True,
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
            "analysis_confidence": 0.8,
            "uncertainties": [],
            "source_trace": {
                "source_pdf": "input.pdf",
                "source_page": 1,
                "method": "codex_cli",
                "microzoom_manifest_path": str(manifest_path),
                "evidence_images": evidence_images,
            },
            "microzoom_manifest_path": str(manifest_path),
            "evidence_images": evidence_images,
            "evidence": "page 1",
        }
    ]

    validated, errors = _validate_approved_rows(rows, paths, job_id)

    assert errors == []
    assert validated[0]["source_trace"]["method"] == "codex_cli"
    assert validated[0]["microzoom_manifest_path"] == str(manifest_path)


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
            "polygon_vertices": [
                {"x": 0, "y": 0},
                {"x": 240, "y": 0},
                {"x": 240, "y": 150},
                {"x": 0, "y": 150},
            ],
            "contour_type": "polygon",
            "confidence": 0.78,
            "evidence": "drawing shows 240 bottom width and 150 height with 30 mm side offsets",
        }
    ]

    validated, errors = _validate_approved_rows(rows, paths, job_id)

    assert validated == []
    assert errors
    assert "corner_reliefs is empty" in errors[0]["error"]


def test_approval_validation_blocks_polygon_without_vertices_even_with_reliefs(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "approval-polygon-missing-vertices"
    job_dir = paths.jobs_import_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())
    rows = [
        {
            "candidate_id": "codex_cli-1",
            "source_pdf": "input.pdf",
            "source_page": 1,
            "poz_no": "4042",
            "width": 156.5,
            "height": 175,
            "thickness": 10,
            "material": "S355JR",
            "quantity": 2,
            "holes": [],
            "slots": [],
            "corner_reliefs": [{"corner": "bottom_left", "radius": 10, "relief_type": "chamfer"}],
            "polygon_vertices": None,
            "contour_type": "polygon",
            "confidence": 0.42,
            "evidence": "top/right polygon contour is visible",
        }
    ]

    validated, errors = _validate_approved_rows(rows, paths, job_id)

    assert validated == []
    assert errors
    assert "polygon_vertices" in errors[0]["error"]


def test_approval_validation_preserves_polygon_vertices_and_relief_offsets(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "approval-polygon-valid"
    job_dir = paths.jobs_import_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())
    rows = [
        {
            "candidate_id": "codex_cli-1",
            "source_pdf": "input.pdf",
            "source_page": 1,
            "poz_no": "4042",
            "width": 156.5,
            "height": 175,
            "thickness": 10,
            "material": "S355JR",
            "quantity": 2,
            "holes": [],
            "slots": [],
            "corner_reliefs": [
                {"corner": "bottom_left", "radius": 10, "relief_type": "chamfer", "x_offset": 10, "y_offset": 10}
            ],
            "polygon_vertices": [
                {"x": 0, "y": 10},
                {"x": 10, "y": 0},
                {"x": 146.5, "y": 0},
                {"x": 156.5, "y": 10.5},
                {"x": 156.5, "y": 145},
                {"x": 120, "y": 175},
                {"x": 0, "y": 175},
            ],
            "contour_type": "polygon",
            "confidence": 0.74,
            "evidence": "polygon contour fully dimensioned",
        }
    ]

    validated, errors = _validate_approved_rows(rows, paths, job_id)

    assert errors == []
    assert validated[0]["polygon_vertices"][0] == {"x": 0.0, "y": 10.0}
    assert validated[0]["corner_reliefs"][0]["x_offset"] == 10.0


def test_approval_validation_blocks_invalid_polygon_geometry(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "approval-polygon-invalid"
    job_dir = paths.jobs_import_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())
    base = {
        "candidate_id": "codex_cli-1",
        "source_pdf": "input.pdf",
        "source_page": 1,
        "poz_no": "4042",
        "width": 100,
        "height": 100,
        "thickness": 10,
        "material": "S355JR",
        "quantity": 1,
        "holes": [],
        "slots": [],
        "corner_reliefs": [],
        "contour_type": "polygon",
        "confidence": 0.8,
        "evidence": "polygon contour fully dimensioned",
    }

    cases = [
        ([{"x": 0, "y": 0}, {"x": 110, "y": 0}, {"x": 100, "y": 100}, {"x": 0, "y": 100}], "outside plate bounds"),
        ([{"x": 0, "y": 0}, {"x": 0, "y": 0}, {"x": 100, "y": 100}, {"x": 0, "y": 100}], "duplicates an adjacent point"),
        ([{"x": 0, "y": 0}, {"x": 100, "y": 100}, {"x": 100, "y": 0}, {"x": 0, "y": 100}], "self-intersect"),
    ]
    for vertices, expected in cases:
        row = dict(base, polygon_vertices=vertices)
        validated, errors = _validate_approved_rows([row], paths, job_id)
        assert validated == []
        assert expected in errors[0]["error"]


def test_extract_codex_candidates_preserves_partial_timeout_candidates(tmp_path, monkeypatch):
    paths = _make_minimal_paths(tmp_path)
    job_id = "extract-partial-timeout"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())
    (output_dir / "pdf_diagnostics.json").write_text(
        json.dumps({"pdfs": [{"source_pdf": "input.pdf", "classification": "visual_text_required"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        app.state,
        "codex_bridge",
        FakeBridge(
        json.dumps(
            {
                "candidates": [
                    {
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
                        "polygon_vertices": None,
                        "contour_type": "rectangle",
                        "confidence": 0.7,
                        "analysis_confidence": 0.7,
                        "uncertainties": [],
                        "source_trace": {
                            "source_pdf": "input.pdf",
                            "source_page": 1,
                            "method": "codex_cli",
                            "microzoom_manifest_path": None,
                            "evidence_images": [],
                        },
                        "microzoom_manifest_path": None,
                        "evidence_images": [],
                        "evidence": "visible rectangle",
                    }
                ]
            }
        ),
        ok=False,
        error="Codex CLI timed out after 120 seconds.",
        ),
        raising=False,
    )

    result = _extract_codex_candidates(paths, job_id)

    assert result["ok"] is True
    assert result["extraction_status"] == "partial_timeout"
    data = json.loads((output_dir / "codex_candidates.json").read_text(encoding="utf-8"))
    assert data["extraction_status"] == "partial_timeout"
    assert data["candidates"][0]["extraction_status"] == "partial_timeout"
    assert data["candidates"][0]["quality_status"] in {"ready_for_approval", "needs_review"}


def test_extract_codex_candidates_timeout_without_json_writes_manual_review(tmp_path, monkeypatch):
    paths = _make_minimal_paths(tmp_path)
    job_id = "extract-timeout-empty"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())
    (output_dir / "pdf_diagnostics.json").write_text(
        json.dumps({"pdfs": [{"source_pdf": "input.pdf", "classification": "visual_text_required"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        app.state,
        "codex_bridge",
        FakeBridge("", ok=False, error="Codex CLI timed out after 120 seconds."),
        raising=False,
    )

    result = _extract_codex_candidates(paths, job_id)

    assert result["ok"] is True
    assert result["extraction_status"] == "visual_extraction_failed"
    reviews = json.loads((output_dir / "manual_review_required.json").read_text(encoding="utf-8"))
    assert reviews[0]["reason"] == "visual_extraction_failed"
    data = json.loads((output_dir / "codex_candidates.json").read_text(encoding="utf-8"))
    assert data["candidates"] == []


def test_tool_approval_uses_shared_polygon_validation(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "tool-approval-polygon-missing"
    job_dir = paths.jobs_import_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(_minimal_pdf_bytes())
    result = ToolRegistry(paths).approve_candidates(
        {
            "job_id": job_id,
            "rows": [
                {
                    "candidate_id": "manager-1",
                    "source_pdf": "input.pdf",
                    "source_page": 1,
                    "poz_no": "4042",
                    "width": 156.5,
                    "height": 175,
                    "thickness": 10,
                    "material": "S355JR",
                    "quantity": 2,
                    "holes": [],
                    "slots": [],
                    "corner_reliefs": [{"corner": "bottom_left", "radius": 10, "relief_type": "chamfer"}],
                    "polygon_vertices": None,
                    "contour_type": "polygon",
                    "confidence": 0.42,
                }
            ],
        }
    )

    assert result["ok"] is False
    assert "polygon_vertices" in result["validation_errors"][0]["error"]
    assert not (job_dir / "approved_plate_specs.json").exists()


def test_corner_relief_approval_error_routes_to_manager_question(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "corner-chat"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "codex_candidates.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
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
                        "evidence": "drawing shows 30 mm side offsets",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "needs_manager_approval"}, ensure_ascii=False)
    bridge = FakeBridge("unused")

    result = AgentOrchestrator(paths, bridge=bridge, allow_codex=True).run(
        f"Mudur, aday onayi kose bosaltma bilgisi eksik oldugu icin durdu. "
        f"Lutfen eksik corner_reliefs bilgisini almak icin bana sor.\n\n[Secili is baglami: {selected_context}]"
    )

    assert result.used_llm is False
    assert result.fallback_reason == "local_corner_reliefs"
    assert bridge.calls == []
    assert "206" in result.content
    assert "Hangi koseler bosaltilacak" in result.content


def test_corner_relief_answer_after_clarification_is_not_recorded_as_generic_issue(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "codex_candidates.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
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
                        "evidence": "drawing shows side offsets",
                    },
                    {
                        "source_pdf": "input.pdf",
                        "source_page": 3,
                        "poz_no": "207",
                        "width": 116,
                        "height": 150,
                        "thickness": 8,
                        "material": "S275J2",
                        "quantity": 20,
                        "holes": [],
                        "slots": [],
                        "corner_reliefs": [],
                        "contour_type": "polygon",
                        "confidence": 0.76,
                        "evidence": "drawing shows side offsets",
                    },
                    {
                        "source_pdf": "input.pdf",
                        "source_page": 5,
                        "poz_no": "209",
                        "width": 300,
                        "height": 150,
                        "thickness": 8,
                        "material": "S275J2",
                        "quantity": 30,
                        "holes": [],
                        "slots": [],
                        "corner_reliefs": [],
                        "contour_type": "chamfered",
                        "confidence": 0.82,
                        "evidence": "iki ust kose pahli",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "needs_manager_approval"}, ensure_ascii=False)
    history = [
        {
            "role": "assistant",
            "content": (
                "Kose bilgisini anlayamadim. Lutfen konum (alt-sol, alt-sag, ust-sol, ust-sag veya hepsi), "
                "tip (pah/round/cugul) ve boyut (mm) belirterek tekrar yaz."
            ),
        }
    ]

    result = AgentOrchestrator(paths, bridge=FakeBridge("unused"), allow_codex=True).run(
        f"206 numarali pozda sol ve sag ust koselerde 30x120 pah var 120 uzunlugu kisa kenar boyunca"
        f"\n\n[Secili is baglami: {selected_context}]",
        history=history,
    )

    assert result.fallback_reason == "local_corner_reliefs"
    assert "Kose bosaltma bilgisini kaydettim: 206" in result.content
    assert "207" in result.content
    assert "209" in result.content
    assert not (output_dir / "manager_issue_notes.jsonl").exists()
    updated = json.loads((output_dir / "codex_candidates.json").read_text(encoding="utf-8"))
    reliefs = updated["candidates"][0]["corner_reliefs"]
    assert sorted(relief["corner"] for relief in reliefs) == ["top_left", "top_right"]
    assert all(relief["relief_type"] == "chamfer" for relief in reliefs)
    assert all(relief["x_offset"] == 30 for relief in reliefs)
    assert all(relief["y_offset"] == 120 for relief in reliefs)


def test_corner_relief_meta_question_lists_missing_candidate_details(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "codex_candidates.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "poz_no": "206",
                        "width": 240,
                        "height": 150,
                        "thickness": 8,
                        "corner_reliefs": [],
                        "contour_type": "polygon",
                        "evidence": "side offsets",
                    },
                    {
                        "poz_no": "207",
                        "width": 116,
                        "height": 150,
                        "thickness": 8,
                        "corner_reliefs": [],
                        "contour_type": "polygon",
                        "evidence": "side offsets",
                    },
                    {
                        "poz_no": "209",
                        "width": 300,
                        "height": 150,
                        "thickness": 8,
                        "corner_reliefs": [],
                        "contour_type": "chamfered",
                        "evidence": "ust koseler pahli",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "needs_manager_approval"}, ensure_ascii=False)
    history = [
        {
            "role": "assistant",
            "content": (
                "Kose bilgisini anlayamadim. Lutfen konum (alt-sol, alt-sag, ust-sol, ust-sag veya hepsi), "
                "tip (pah/round/cugul) ve boyut (mm) belirterek tekrar yaz."
            ),
        }
    ]

    result = AgentOrchestrator(paths, bridge=FakeBridge("unused"), allow_codex=True).run(
        f"hangi kose bilgilerini anlamadin\n\n[Secili is baglami: {selected_context}]",
        history=history,
    )

    assert result.fallback_reason == "local_corner_reliefs"
    assert "kose bosaltma bilgisini su adaylar icin soruyorum" in result.content
    assert "Poz 206" in result.content
    assert "Poz 207" in result.content
    assert "Poz 209" in result.content
    assert "206: sol ve sag ust pah 30x120" in result.content
    assert "Kose bilgisini anlayamadim" not in result.content


def test_corner_relief_scope_question_lists_requested_parts(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "codex_candidates.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {"poz_no": "206", "width": 240, "height": 150, "thickness": 8, "corner_reliefs": [], "contour_type": "polygon"},
                    {"poz_no": "207", "width": 116, "height": 150, "thickness": 8, "corner_reliefs": [], "contour_type": "polygon"},
                    {"poz_no": "209", "width": 300, "height": 150, "thickness": 8, "corner_reliefs": [], "contour_type": "chamfered"},
                    {"poz_no": "210", "width": 96, "height": 150, "thickness": 8, "corner_reliefs": [], "contour_type": "chamfered"},
                ]
            }
        ),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "needs_manager_approval"}, ensure_ascii=False)
    history = [
        {
            "role": "assistant",
            "content": "Hangi koseler bosaltilacak ve ne tip/boyut?",
        }
    ]

    result = AgentOrchestrator(paths, bridge=FakeBridge("unused"), allow_codex=True).run(
        f"hangi parcalar icin soruyorsun\n\n[Secili is baglami: {selected_context}]",
        history=history,
    )

    assert result.fallback_reason == "local_corner_reliefs"
    assert "Poz 206" in result.content
    assert "Poz 207" in result.content
    assert "Poz 209" in result.content
    assert "Eksik kalan kisim" in result.content
    assert "Kose bilgisini anlayamadim" not in result.content


def test_corner_relief_confirmation_applies_manager_suggested_format(tmp_path):
    paths = _make_minimal_paths(tmp_path)
    job_id = "danieli-1701"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": job_id}), encoding="utf-8")
    (output_dir / "codex_candidates.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {"poz_no": "206", "width": 240, "height": 150, "thickness": 8, "corner_reliefs": [], "contour_type": "polygon"},
                    {"poz_no": "207", "width": 116, "height": 150, "thickness": 8, "corner_reliefs": [], "contour_type": "polygon"},
                    {"poz_no": "209", "width": 300, "height": 150, "thickness": 8, "corner_reliefs": [], "contour_type": "chamfered"},
                    {"poz_no": "210", "width": 96, "height": 150, "thickness": 8, "corner_reliefs": [], "contour_type": "chamfered"},
                ]
            }
        ),
        encoding="utf-8",
    )
    selected_context = json.dumps({"selected_job_id": job_id, "status": "needs_manager_approval"}, ensure_ascii=False)
    history = [
        {
            "role": "assistant",
            "content": (
                "`danieli-1701` icin kose bosaltma bilgisini su adaylar icin soruyorum:\n"
                "- Poz 206: satir 2, 240x150x8mm\n"
                "- Poz 207: satir 3, 116x150x8mm\n"
                "- Poz 209: satir 5, 300x150x8mm\n"
                "Eksik kalan kisim, her poz icin kose-konum ve pah olcusunun net eslesmesi.\n"
                "Benden beklenen net format su:\n"
                "- `206: sol ve sag ust pah 30x120`\n"
                "- `207: sol ust pah 30x120, sag alt pah 10x10`\n"
                "- `209: sol ve sag ust pah 60x120`"
            ),
        }
    ]

    result = AgentOrchestrator(paths, bridge=FakeBridge("unused"), allow_codex=True).run(
        f"evet bu sekilde ilerle\n\n[Secili is baglami: {selected_context}]",
        history=history,
    )

    assert result.fallback_reason == "local_corner_reliefs"
    assert "Kose bosaltma bilgisini kaydettim: 206, 207, 209" in result.content
    assert "210" in result.content
    assert "Kose bilgisini anlayamadim" not in result.content
    updated = json.loads((output_dir / "codex_candidates.json").read_text(encoding="utf-8"))
    assert updated["candidates"][0]["corner_reliefs"]
    assert updated["candidates"][1]["corner_reliefs"]
    assert updated["candidates"][2]["corner_reliefs"]
    assert updated["candidates"][3]["corner_reliefs"] == []


def test_corner_relief_parser_handles_multiple_positions_and_asymmetric_chamfers():
    pending = [
        {"_row_index": 2, "poz_no": "206"},
        {"_row_index": 3, "poz_no": "207"},
        {"_row_index": 5, "poz_no": "209"},
    ]
    parsed = _parse_corner_reliefs_by_pending_candidate(
        (
            "206 numarali pozda sol ve sag ust koselerde 30x120 pah var 120 uzunlugu kisa kenar boyunca\n"
            "207 numarali pozda sol ust kosede 30x120 pah var ayrica sag alt kosede 10x10 pah var\n"
            "209 numarali pozda sol ve sag ust kosede 60x120 pah var"
        ),
        pending,
    )

    assert sorted(relief["corner"] for relief in parsed[2]) == ["top_left", "top_right"]
    assert all(relief["x_offset"] == 30 and relief["y_offset"] == 120 for relief in parsed[2])
    assert {(relief["corner"], relief["x_offset"], relief["y_offset"]) for relief in parsed[3]} == {
        ("top_left", 30.0, 120.0),
        ("bottom_right", 10.0, 10.0),
    }
    assert sorted(relief["corner"] for relief in parsed[5]) == ["top_left", "top_right"]
    assert all(relief["x_offset"] == 60 and relief["y_offset"] == 120 for relief in parsed[5])


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


def _placeholder_pdf_bytes() -> bytes:
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
    ]
    stream = "BT /F1 12 Tf 10 180 Td (placeholder) Tj ET\n0 0 m 200 0 l 200 100 l 0 100 l h S\n"
    objects.append("3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R >> endobj")
    objects.append(f"4 0 obj << /Length {len(stream)} >> stream\n{stream}\nendstream endobj")
    pdf = "%PDF-1.4\n" + "\n".join(objects) + "\ntrailer << /Root 1 0 R >>\n%%EOF\n"
    return pdf.encode("latin-1")


def _multi_page_pdf_bytes(pages: list[list[str]]) -> bytes:
    objects = ["1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj"]
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(len(pages)))
    objects.append(f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {len(pages)} >> endobj")
    for index, text_lines in enumerate(pages):
        page_obj = 3 + index * 2
        content_obj = page_obj + 1
        text_commands = "\n".join(f"({line}) Tj T*" for line in text_lines)
        stream = (
            "q\n"
            "0 0 m 200 0 l 200 100 l 0 100 l h S\n"
            "BT\n"
            "/F1 12 Tf\n"
            "10 10 Td\n"
            f"{text_commands}\n"
            "ET\n"
            "Q\n"
        )
        objects.append(
            f"{page_obj} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents {content_obj} 0 R >> endobj"
        )
        objects.append(f"{content_obj} 0 obj << /Length {len(stream)} >> stream\n{stream}\nendstream endobj")
    pdf = "%PDF-1.4\n" + "\n".join(objects) + "\ntrailer << /Root 1 0 R >>\n%%EOF\n"
    return pdf.encode("latin-1")


# ---------------------------------------------------------------------------
# Unit tests: _extract_page_numbers_for_exclusion (yeni regex pattern'ları)
# ---------------------------------------------------------------------------

def test_extract_page_numbers_single():
    from technical_office_runtime.orchestrator import _extract_page_numbers_for_exclusion
    assert _extract_page_numbers_for_exclusion("sayfa 3 atlansin") == [3]
    assert _extract_page_numbers_for_exclusion("sayfa no: 5 profil sayfasi") == [5]


def test_extract_page_numbers_multi_ve():
    from technical_office_runtime.orchestrator import _extract_page_numbers_for_exclusion
    result = _extract_page_numbers_for_exclusion("sayfa 2 ve 3 profil detaylari")
    assert 2 in result and 3 in result


def test_extract_page_numbers_multi_comma():
    from technical_office_runtime.orchestrator import _extract_page_numbers_for_exclusion
    result = _extract_page_numbers_for_exclusion("sayfa 1, 2 ve 3 atlanmali")
    assert set(result) >= {1, 2, 3}


def test_extract_page_numbers_range():
    from technical_office_runtime.orchestrator import _extract_page_numbers_for_exclusion
    result = _extract_page_numbers_for_exclusion("sayfa 2-4 plaka degil gecilsin")
    assert result == [2, 3, 4]


def test_extract_page_numbers_ordinal():
    from technical_office_runtime.orchestrator import _extract_page_numbers_for_exclusion
    result = _extract_page_numbers_for_exclusion("1. sayfa baslik sayfasi")
    assert 1 in result


def test_extract_page_numbers_dedup():
    from technical_office_runtime.orchestrator import _extract_page_numbers_for_exclusion
    result = _extract_page_numbers_for_exclusion("sayfa 3 ve 3 atla")
    assert result.count(3) == 1


# ---------------------------------------------------------------------------
# Unit tests: _looks_like_page_exclusion_request (yeni terimler)
# ---------------------------------------------------------------------------

def _page_exclusion_msg(phrase: str, job_id: str = "test-001") -> str:
    ctx = json.dumps({"selected_job_id": job_id})
    return f"sayfa 1 {phrase}\n\n[Secili is baglami: {ctx}]"


def test_detection_plaka_degil():
    from technical_office_runtime.orchestrator import _looks_like_page_exclusion_request
    assert _looks_like_page_exclusion_request(_page_exclusion_msg("plaka degil"))


def test_detection_plaka_icermiyor():
    from technical_office_runtime.orchestrator import _looks_like_page_exclusion_request
    assert _looks_like_page_exclusion_request(_page_exclusion_msg("plaka icermiyor"))


def test_detection_profil_detaylari():
    from technical_office_runtime.orchestrator import _looks_like_page_exclusion_request
    assert _looks_like_page_exclusion_request(_page_exclusion_msg("profil detaylari"))


def test_detection_gecilmeli():
    from technical_office_runtime.orchestrator import _looks_like_page_exclusion_request
    assert _looks_like_page_exclusion_request(_page_exclusion_msg("gecilmeli"))


def test_detection_cizilmeyecek():
    from technical_office_runtime.orchestrator import _looks_like_page_exclusion_request
    assert _looks_like_page_exclusion_request(_page_exclusion_msg("cizilmeyecek"))


def test_detection_dahil_edilmesin():
    from technical_office_runtime.orchestrator import _looks_like_page_exclusion_request
    assert _looks_like_page_exclusion_request(_page_exclusion_msg("dahil edilmesin"))


# ---------------------------------------------------------------------------
# Unit tests: MemoryBridge.record_page_exclusion + find_page_hints
# ---------------------------------------------------------------------------

def test_memory_bridge_record_and_find_page_hints(tmp_path):
    from technical_office_runtime.memory_bridge import MemoryBridge
    bridge = MemoryBridge(tmp_path / "test.db")
    sha = "a" * 64
    bridge.record_page_exclusion(sha, "job-001", [1, 3], note="baslik sayfasi")
    hints = bridge.find_page_hints(sha)
    assert hints == [1, 3]


def test_memory_bridge_find_page_hints_empty(tmp_path):
    from technical_office_runtime.memory_bridge import MemoryBridge
    bridge = MemoryBridge(tmp_path / "test.db")
    assert bridge.find_page_hints("nonexistent" * 4) == []


def test_memory_bridge_page_hints_latest_wins(tmp_path):
    from technical_office_runtime.memory_bridge import MemoryBridge
    bridge = MemoryBridge(tmp_path / "test.db")
    sha = "b" * 64
    bridge.record_page_exclusion(sha, "job-001", [1], note="ilk")
    bridge.record_page_exclusion(sha, "job-002", [2, 3], note="son")
    hints = bridge.find_page_hints(sha)
    assert hints == [2, 3]


def test_memory_bridge_page_hints_cross_job(tmp_path):
    """Bir PDF hash'i job-A'da kaydedilir; farklı bir sorguda da bulunur."""
    from technical_office_runtime.memory_bridge import MemoryBridge
    bridge = MemoryBridge(tmp_path / "test.db")
    sha = "c" * 64
    bridge.record_page_exclusion(sha, "job-A", [5], note="profil")
    assert 5 in bridge.find_page_hints(sha)


# ---------------------------------------------------------------------------
# Unit tests: _summary_requires_visual_candidates (plate_geometry_not_found)
# ---------------------------------------------------------------------------

def test_visual_candidates_plate_geometry_not_found():
    from technical_office_runtime.app import _summary_requires_visual_candidates
    summary = {"manual_reviews": [{"reason": "plate_geometry_not_found"}]}
    assert _summary_requires_visual_candidates(summary) is True


def test_visual_candidates_visual_text_required():
    from technical_office_runtime.app import _summary_requires_visual_candidates
    summary = {"manual_reviews": [{"reason": "visual_text_required"}]}
    assert _summary_requires_visual_candidates(summary) is True


def test_visual_candidates_no_trigger_reason():
    from technical_office_runtime.app import _summary_requires_visual_candidates
    summary = {"manual_reviews": [{"reason": "missing_poz_count"}]}
    assert _summary_requires_visual_candidates(summary) is False


def test_visual_candidates_empty():
    from technical_office_runtime.app import _summary_requires_visual_candidates
    assert _summary_requires_visual_candidates({}) is False


# ---------------------------------------------------------------------------
# Unit tests: _reconcile_manual_reviews
# ---------------------------------------------------------------------------

def test_reconcile_removes_covered_by_candidate(tmp_path):
    from technical_office_runtime.app import _reconcile_manual_reviews
    import json
    reviews = [{"reason": "plate_geometry_not_found", "source_pdf": "test.pdf", "page": 3}]
    candidates = {"candidates": [{"source_pdf": "test.pdf", "source_page": 3}]}
    (tmp_path / "manual_review_required.json").write_text(json.dumps(reviews))
    (tmp_path / "codex_candidates.json").write_text(json.dumps(candidates))
    active = _reconcile_manual_reviews(tmp_path)
    assert active == []


def test_reconcile_removes_excluded_page(tmp_path):
    from technical_office_runtime.app import _reconcile_manual_reviews
    import json
    reviews = [{"reason": "plate_geometry_not_found", "source_pdf": "test.pdf", "page": 5}]
    exclusions = {"excluded_pages": [{"page": 5}]}
    (tmp_path / "manual_review_required.json").write_text(json.dumps(reviews))
    (tmp_path / "page_exclusions_applied.json").write_text(json.dumps(exclusions))
    active = _reconcile_manual_reviews(tmp_path)
    assert active == []


def test_reconcile_keeps_uncovered_review(tmp_path):
    from technical_office_runtime.app import _reconcile_manual_reviews
    import json
    reviews = [{"reason": "plate_geometry_not_found", "source_pdf": "test.pdf", "page": 7}]
    candidates = {"candidates": [{"source_pdf": "test.pdf", "source_page": 3}]}
    (tmp_path / "manual_review_required.json").write_text(json.dumps(reviews))
    (tmp_path / "codex_candidates.json").write_text(json.dumps(candidates))
    active = _reconcile_manual_reviews(tmp_path)
    assert len(active) == 1 and active[0]["page"] == 7


def test_reconcile_empty_files(tmp_path):
    from technical_office_runtime.app import _reconcile_manual_reviews
    active = _reconcile_manual_reviews(tmp_path)
    assert active == []


# ---------------------------------------------------------------------------
# Unit tests: Turkish greeting detection
# ---------------------------------------------------------------------------

def test_greeting_nasilsin():
    from technical_office_runtime.orchestrator import _looks_like_lightweight_manager_chat
    assert _looks_like_lightweight_manager_chat("nasılsın") is True


def test_greeting_naber():
    from technical_office_runtime.orchestrator import _looks_like_lightweight_manager_chat
    assert _looks_like_lightweight_manager_chat("naber") is True


def test_greeting_gunaydin():
    from technical_office_runtime.orchestrator import _looks_like_lightweight_manager_chat
    assert _looks_like_lightweight_manager_chat("günaydın") is True


def test_greeting_iyi_misin():
    from technical_office_runtime.orchestrator import _looks_like_lightweight_manager_chat
    assert _looks_like_lightweight_manager_chat("iyi misin") is True


def test_greeting_sagol():
    from technical_office_runtime.orchestrator import _looks_like_lightweight_manager_chat
    assert _looks_like_lightweight_manager_chat("sağol") is True


# ---------------------------------------------------------------------------
# Unit tests: Skill update / promote detection
# ---------------------------------------------------------------------------

def test_skill_update_detection_hafizasina_ekle():
    from technical_office_runtime.orchestrator import _looks_like_skill_update_request
    assert _looks_like_skill_update_request("autocad uzman hafizasina ekle: polygon plakalarda koordinat kullan")

def test_skill_update_detection_uzman_memorye():
    from technical_office_runtime.orchestrator import _looks_like_skill_update_request
    assert _looks_like_skill_update_request("kalite kontrol memorye yaz: ok=false teslim kapali")

def test_skill_update_detection_negative():
    from technical_office_runtime.orchestrator import _looks_like_skill_update_request
    assert not _looks_like_skill_update_request("bu isin durumunu ozetle")

def test_skill_promote_detection():
    from technical_office_runtime.orchestrator import _looks_like_skill_promote_request
    assert _looks_like_skill_promote_request("proposal autocad-uzman-1-memory-20260514123456 onayla")

def test_skill_promote_detection_negative():
    from technical_office_runtime.orchestrator import _looks_like_skill_promote_request
    assert not _looks_like_skill_promote_request("pipeline calistir")

def test_extract_target_agent_autocad1():
    from technical_office_runtime.orchestrator import _extract_target_agent_from_text
    assert _extract_target_agent_from_text("autocad uzman 1 hafizasina ekle") == "autocad-uzman-1"

def test_extract_target_agent_autocad2():
    from technical_office_runtime.orchestrator import _extract_target_agent_from_text
    assert _extract_target_agent_from_text("uzman2 bunu ogrenmeli") == "autocad-uzman-2"

def test_extract_target_agent_kalite():
    from technical_office_runtime.orchestrator import _extract_target_agent_from_text
    assert _extract_target_agent_from_text("kalite kontrol memorye yaz") == "kalite-kontrol"

# ---------------------------------------------------------------------------
# Unit tests: load_expert_agent_memories
# ---------------------------------------------------------------------------

def test_load_expert_agent_memories_returns_content(tmp_path):
    from technical_office_runtime.agent_context import load_expert_agent_memories

    # tmp_path altında sahte agent yapısı kur
    agents_root = tmp_path / "agents"
    (agents_root / "autocad-uzman-1").mkdir(parents=True)
    (agents_root / "autocad-uzman-1" / "MEMORY.md").write_text(
        "# autocad-uzman-1 Memory\n\nPolygon tipi plakalar contour çizgisi ile çizilir.", encoding="utf-8"
    )
    (agents_root / "kalite-kontrol").mkdir(parents=True)
    (agents_root / "kalite-kontrol" / "RULES.md").write_text(
        "# Kurallar\n\nok=false ise teslim yapma.", encoding="utf-8"
    )

    class _FakePaths:
        suite_root = tmp_path
        workspace_root = tmp_path / "workspace"

    result = load_expert_agent_memories(_FakePaths())
    assert "autocad-uzman-1 / MEMORY.md" in result
    assert "kalite-kontrol / RULES.md" in result
    assert "Polygon tipi" in result

def test_load_expert_agent_memories_empty(tmp_path):
    from technical_office_runtime.agent_context import load_expert_agent_memories

    class _FakePaths:
        suite_root = tmp_path
        workspace_root = tmp_path / "workspace"

    result = load_expert_agent_memories(_FakePaths())
    assert result == ""

# ---------------------------------------------------------------------------
# Unit tests: skill update handler (proposal dosyası oluşuyor mu)
# ---------------------------------------------------------------------------

def test_skill_update_handler_creates_proposal(tmp_path):
    import json
    from technical_office_runtime.orchestrator import AgentOrchestrator

    # Minimal workspace yapısı
    (tmp_path / "agents" / "autocad-uzman-1").mkdir(parents=True)
    (tmp_path / "workspace" / "outputs" / "jobs").mkdir(parents=True)
    (tmp_path / "workspace" / "memory").mkdir(parents=True)
    (tmp_path / "workspace" / "sessions").mkdir(parents=True)
    (tmp_path / "journal" / "skill_proposals").mkdir(parents=True)
    registry = {
        "agents": [{"id": "teknik-ofis-muduru", "name": "Mudur", "role": "manager", "brain": "autocad-uzman-1/AGENT.md", "skills": ["_shared"]}]
    }
    (tmp_path / "agents" / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    (tmp_path / "agents" / "autocad-uzman-1" / "AGENT.md").write_text("# AGENT", encoding="utf-8")

    from technical_office_runtime.config import RuntimePaths as _RP
    paths = _RP(
        suite_root=tmp_path,
        registry_path=tmp_path / "agents" / "registry.json",
        workspace_root=tmp_path / "workspace",
        jobs_import_root=tmp_path / "workspace" / "imports" / "jobs",
        jobs_output_root=tmp_path / "workspace" / "outputs" / "jobs",
        autocad_src=tmp_path / "mcp" / "autocad-mcp-server" / "src",
    )
    orch = AgentOrchestrator(paths=paths, agent_id="teknik-ofis-muduru")
    result = orch._handle_skill_update_request(
        "autocad uzman hafizasina ekle: polygon_contour icin corner_reliefs bos birakilabilir",
        [],
    )
    proposals = list((tmp_path / "journal" / "skill_proposals").glob("autocad-uzman-1-memory-*.md"))
    assert len(proposals) == 1
    assert "local_skill_update_proposal" in result.fallback_reason

# ---------------------------------------------------------------------------
# Unit tests: _extract_all_poz_nos_from_text — çoklu poz numarası
# ---------------------------------------------------------------------------

def test_extract_all_poz_nos_single():
    from technical_office_runtime.orchestrator import _extract_all_poz_nos_from_text
    assert _extract_all_poz_nos_from_text("poz 4043 poligon") == ["4043"]

def test_extract_all_poz_nos_multiple():
    from technical_office_runtime.orchestrator import _extract_all_poz_nos_from_text
    result = _extract_all_poz_nos_from_text("4043 4047 ve 4058 poligon olarak ciz")
    assert set(result) == {"4043", "4047", "4058"}

def test_extract_all_poz_nos_empty():
    from technical_office_runtime.orchestrator import _extract_all_poz_nos_from_text
    assert _extract_all_poz_nos_from_text("poligon olarak ciz hepsini") == []

# ---------------------------------------------------------------------------
# Unit tests: polygon instruction escapes to all pending when multiple pozlar
# ---------------------------------------------------------------------------

def test_looks_like_polygon_draw_instruction_with_multiple_poz():
    from technical_office_runtime.orchestrator import _looks_like_polygon_draw_instruction
    assert _looks_like_polygon_draw_instruction("4043 4047 ve 4058 poligon olarak cizeceksin")


# ---------------------------------------------------------------------------
# Unit tests: LLM-first architecture helpers
# ---------------------------------------------------------------------------

def test_build_live_job_context_empty_for_missing_job(tmp_path):
    """Job klasörü yoksa boş string döner."""
    from technical_office_runtime.orchestrator import _build_live_job_context

    paths = _make_minimal_paths(tmp_path)
    result = _build_live_job_context(paths, "nonexistent-job-xyz")
    assert result == ""


def test_build_live_job_context_returns_fsm_state(tmp_path):
    """Job klasörü varsa FSM state okunur."""
    from technical_office_runtime.orchestrator import _build_live_job_context

    paths = _make_minimal_paths(tmp_path)
    job_id = "test-live-ctx-001"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "fsm_state.json").write_text(json.dumps({"state": "failed"}), encoding="utf-8")
    (job_dir / "job.json").write_text(json.dumps({"project_name": "Test Projesi"}), encoding="utf-8")

    result = _build_live_job_context(paths, job_id)
    assert "FSM: failed" in result
    assert "Test Projesi" in result
    assert job_id in result


def test_synthesize_query_without_gemini_returns_raw(tmp_path, monkeypatch):
    """GEMINI_API_KEY yoksa _synthesize_query_with_gemini ham veriyi döndürür."""
    from technical_office_runtime.orchestrator import AgentOrchestrator

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    paths = _make_minimal_paths(tmp_path)
    orch = AgentOrchestrator(paths=paths, allow_codex=False)

    result = orch._synthesize_query_with_gemini(
        "ham veri bloğu", "kullanıcı sorusu", [], fallback_reason="test_fallback"
    )
    assert result.content == "ham veri bloğu"
    assert result.fallback_reason == "test_fallback"


def test_selected_job_id_from_context_parses_hidden_payload():
    """Gizli bağlam bloğundan job ID doğru çıkarılır."""
    from technical_office_runtime.orchestrator import _selected_job_id_from_context
    text = 'bu isin durumu nedir\n\n[Secili is baglami:{"selected_job_id": "danieli-20260514"}]'
    assert _selected_job_id_from_context(text) == "danieli-20260514"


def test_selected_job_id_from_context_returns_none_when_absent():
    from technical_office_runtime.orchestrator import _selected_job_id_from_context
    assert _selected_job_id_from_context("sadece normal metin") is None


# ---------------------------------------------------------------------------
# Unit tests: corner name normalization in patch
# ---------------------------------------------------------------------------

def test_patch_normalizes_inner_corner_suffix(tmp_path):
    """_try_patch_approved_spec_corner_reliefs: bottom_left_inner → bottom_left."""
    import json
    from technical_office_runtime.orchestrator import _try_patch_approved_spec_corner_reliefs

    job_dir = tmp_path
    spec = {"plates": [
        {
            "poz_no": "4039",
            "width": 200.0, "height": 100.0, "thickness": 10.0,
            "material": "S235",
            "corner_reliefs": [
                {"corner": "bottom_left_inner", "radius": 10.0, "relief_type": "round"},
                {"corner": "bottom_right_inner", "radius": 10.0, "relief_type": "round"},
            ]
        }
    ]}
    (job_dir / "approved_plate_specs.json").write_text(json.dumps(spec), encoding="utf-8")

    result = _try_patch_approved_spec_corner_reliefs(job_dir)
    assert result is not None
    patched = json.loads((job_dir / "approved_plate_specs.json").read_text(encoding="utf-8"))
    corners = {r["corner"] for r in patched["plates"][0]["corner_reliefs"]}
    assert "bottom_left_inner" not in corners
    assert "bottom_left" in corners
    assert "bottom_right" in corners


def test_patch_removes_unknown_corner(tmp_path):
    """Tanımsız köşe adları (ör. 'inner_mid') tamamen kaldırılır."""
    import json
    from technical_office_runtime.orchestrator import _try_patch_approved_spec_corner_reliefs

    job_dir = tmp_path
    spec = {"plates": [
        {
            "poz_no": "R4-11-314",
            "width": 300.0, "height": 150.0, "thickness": 8.0,
            "material": "S355",
            "corner_reliefs": [
                {"corner": "bend_side_inner", "radius": 5.0, "relief_type": "round"},
                {"corner": "bottom_left", "radius": 5.0, "relief_type": "round"},
            ]
        }
    ]}
    (job_dir / "approved_plate_specs.json").write_text(json.dumps(spec), encoding="utf-8")

    _try_patch_approved_spec_corner_reliefs(job_dir)
    patched = json.loads((job_dir / "approved_plate_specs.json").read_text(encoding="utf-8"))
    reliefs = patched["plates"][0]["corner_reliefs"]
    assert len(reliefs) == 1
    assert reliefs[0]["corner"] == "bottom_left"


def test_patch_removes_duplicate_corner(tmp_path):
    """Aynı köşe iki kez listelenirse ikincisi kaldırılır."""
    import json
    from technical_office_runtime.orchestrator import _try_patch_approved_spec_corner_reliefs

    job_dir = tmp_path
    spec = {"plates": [
        {
            "poz_no": "4050",
            "width": 200.0, "height": 100.0, "thickness": 6.0,
            "material": "S235",
            "corner_reliefs": [
                {"corner": "bottom_left", "radius": 8.0, "relief_type": "round"},
                {"corner": "bottom_left", "radius": 8.0, "relief_type": "round"},
            ]
        }
    ]}
    (job_dir / "approved_plate_specs.json").write_text(json.dumps(spec), encoding="utf-8")

    _try_patch_approved_spec_corner_reliefs(job_dir)
    patched = json.loads((job_dir / "approved_plate_specs.json").read_text(encoding="utf-8"))
    reliefs = patched["plates"][0]["corner_reliefs"]
    assert len(reliefs) == 1


def test_repair_normalizes_round_relief_alias(tmp_path):
    import json
    from technical_office_runtime.orchestrator import _repair_approved_spec_corner_reliefs

    spec = {
        "plates": [
            {
                "poz_no": "4038",
                "width": 200,
                "height": 100,
                "thickness": 8,
                "corner_reliefs": [{"corner": "bottom_left", "radius": 10, "relief_type": "round_relief"}],
            }
        ]
    }
    (tmp_path / "approved_plate_specs.json").write_text(json.dumps(spec), encoding="utf-8")

    result = _repair_approved_spec_corner_reliefs(tmp_path, apply=True)

    assert result["changed"] is True
    patched = json.loads((tmp_path / "approved_plate_specs.json").read_text(encoding="utf-8"))
    assert patched["plates"][0]["corner_reliefs"][0]["relief_type"] == "round"


def test_repair_removes_large_offset_that_is_not_radius(tmp_path):
    import json
    from technical_office_runtime.orchestrator import _repair_approved_spec_corner_reliefs

    spec = {
        "plates": [
            {
                "poz_no": "R4-11-314",
                "width": 340,
                "height": 50,
                "thickness": 5,
                "evidence": "Top view shows 50 mm upstand/offset both ends and 240 mm middle.",
                "corner_reliefs": [
                    {"corner": "top_left", "radius": 50, "relief_type": "round"},
                    {"corner": "top_right", "radius": 50, "relief_type": "round"},
                ],
            }
        ]
    }
    (tmp_path / "approved_plate_specs.json").write_text(json.dumps(spec), encoding="utf-8")

    result = _repair_approved_spec_corner_reliefs(tmp_path, apply=True)

    assert result["changed"] is True
    assert any(change["kind"] == "remove_non_relief_offset" for change in result["changes"])
    patched = json.loads((tmp_path / "approved_plate_specs.json").read_text(encoding="utf-8"))
    assert patched["plates"][0]["corner_reliefs"] == []
    assert any("manager_repair_removed_non_relief_offset" in note for note in patched["plates"][0]["notes"])


def test_repair_keeps_ambiguous_large_radius_for_user_decision(tmp_path):
    import json
    from technical_office_runtime.orchestrator import _repair_approved_spec_corner_reliefs

    spec = {
        "plates": [
            {
                "poz_no": "X-1",
                "width": 80,
                "height": 50,
                "thickness": 5,
                "evidence": "Front view shows a rounded end.",
                "corner_reliefs": [{"corner": "top_left", "radius": 50, "relief_type": "round"}],
            }
        ]
    }
    (tmp_path / "approved_plate_specs.json").write_text(json.dumps(spec), encoding="utf-8")

    result = _repair_approved_spec_corner_reliefs(tmp_path, apply=True)

    assert result["changed"] is False
    assert result["ambiguous"] is True
    patched = json.loads((tmp_path / "approved_plate_specs.json").read_text(encoding="utf-8"))
    assert patched == spec


def test_repair_marks_equal_size_relief_as_chamfer(tmp_path):
    import json
    from technical_office_runtime.orchestrator import _repair_approved_spec_corner_reliefs

    spec = {
        "plates": [
            {
                "poz_no": "P-10",
                "width": 100,
                "height": 80,
                "thickness": 8,
                "evidence": "Corner relief is 10x10 at both bottom corners.",
                "corner_reliefs": [{"corner": "bottom_left", "radius": 10, "relief_type": "round"}],
            }
        ]
    }
    (tmp_path / "approved_plate_specs.json").write_text(json.dumps(spec), encoding="utf-8")

    result = _repair_approved_spec_corner_reliefs(tmp_path, apply=True)

    assert result["changed"] is True
    patched = json.loads((tmp_path / "approved_plate_specs.json").read_text(encoding="utf-8"))
    relief = patched["plates"][0]["corner_reliefs"][0]
    assert relief["relief_type"] == "chamfer"
    assert relief["x_offset"] == 10
    assert relief["y_offset"] == 10


def test_deep_output_inspection_detector():
    """'notlar giderildi mi' ve 'tüm çıktıları incele' deep inspection yakalanır."""
    from technical_office_runtime.orchestrator import _looks_like_deep_output_inspection_request
    context = '\n\n[Secili is baglami:{"selected_job_id": "test-job"}]'
    assert _looks_like_deep_output_inspection_request("bu notlar giderilip giderilmedi mi" + context)
    assert _looks_like_deep_output_inspection_request("tum ciktilari incele" + context)
    assert _looks_like_deep_output_inspection_request("ne kaldi" + context)
    assert not _looks_like_deep_output_inspection_request("merhaba nasılsın")


def test_format_deep_output_inspection_lists_produced_pozs(tmp_path):
    """_format_deep_output_inspection: üretilen DXF klasörlerini raporlar."""
    import json
    from technical_office_runtime.orchestrator import _format_deep_output_inspection

    paths = _make_minimal_paths(tmp_path)
    job_id = "deep-insp-001"
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Onaylı spec
    spec = {"plates": [{"poz_no": "4001", "width": 100.0, "height": 50.0, "thickness": 5.0, "material": "S235"}]}
    (job_dir / "approved_plate_specs.json").write_text(json.dumps(spec), encoding="utf-8")

    # Üretilmiş DXF poz klasörü
    poz_dir = output_dir / "4001"
    poz_dir.mkdir()
    (poz_dir / "4001.dxf").write_text("MOCK DXF", encoding="utf-8")

    result = _format_deep_output_inspection(paths, job_id)
    assert "4001" in result
    assert "Üretilen DXF" in result or "Uretilen DXF" in result
