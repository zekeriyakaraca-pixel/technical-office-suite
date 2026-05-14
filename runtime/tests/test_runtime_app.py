from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from technical_office_runtime.app import app


client = TestClient(app)


def test_health_is_ready():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_state_lists_active_technical_office_agents():
    response = client.get("/state")

    assert response.status_code == 200
    active = response.json()["active"]
    assert list(active) == [
        "teknik-ofis-muduru",
        "autocad-uzman-1",
        "autocad-uzman-2",
        "kalite-kontrol",
        "dokuman-kontrol",
    ]


def test_registry_exposes_agents_and_local_model():
    response = client.get("/registry")

    assert response.status_code == 200
    payload = response.json()
    assert "technical-office/codex-cli" in payload["models"]
    assert {agent["id"] for agent in payload["agents"]} >= {
        "teknik-ofis-muduru",
        "autocad-uzman-1",
        "autocad-uzman-2",
        "kalite-kontrol",
        "dokuman-kontrol",
    }


def test_normal_chat_can_use_codex_bridge(monkeypatch):
    class FakeBridge:
        def run(self, request, *, job_id=None, on_event=None):
            return type("Result", (), {"ok": True, "content": "Merhaba, buradayim.", "error": None})()

    app.state.codex_bridge = FakeBridge()
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "merhaba"}]},
    )

    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    assert content.strip()
    assert "Merhaba" in content


def test_job_chat_runs_autocad_pipeline_in_isolated_workspace():
    job_id = "chat-001"
    suite_root = Path(__file__).resolve().parents[2]
    job_dir = suite_root / "workspace" / "imports" / "jobs" / job_id
    output_dir = suite_root / "workspace" / "outputs" / "jobs" / job_id
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)
    create = client.post(
        "/api/jobs",
        data={"project_name": "Chat Pipeline Test", "job_id": job_id},
        files={"pdf_files": ("input.pdf", _vector_pdf_bytes(["POZ: CHAT100", "PLAKA 200x100x10 S355"]), "application/pdf")},
    )
    assert create.status_code == 200

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "technical-office/codex-cli",
            "messages": [{"role": "user", "content": f"run job {job_id} autocad off"}],
        },
    )

    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    assert job_id in content
    assert "ok=true" in content
    assert f"workspace/outputs/jobs/{job_id}" in content
    assert f"DXF=workspace/outputs/jobs/{job_id}" in content
    assert f"NC1=workspace/outputs/jobs/{job_id}" in content
    assert f"QC=workspace/outputs/jobs/{job_id}" in content

    summary = suite_root / "workspace" / "outputs" / "jobs" / job_id / "job_summary.json"
    assert summary.exists()


def test_session_endpoints_and_streaming_chat_roundtrip():
    session_id = "agent:teknik-ofis-muduru:test-runtime"
    client.post("/sessions/reset", json={"session_id": session_id})

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "technical-office/codex-cli",
            "stream": True,
            "idempotencyKey": "runtime-test-run",
            "session_id": session_id,
            "conversation_id": session_id,
            "messages": [{"role": "user", "content": "hangi isler var"}],
        },
    ) as response:
        assert response.status_code == 200
        text = "".join(response.iter_text())

    assert "data:" in text
    assert '"event": "agent"' in text
    assert '"event": "chat"' in text
    assert "Mevcut" in text

    sessions = client.get("/sessions", params={"agent_id": "teknik-ofis-muduru"}).json()["sessions"]
    assert any(item["key"] == session_id for item in sessions)

    history = client.get("/sessions/history", params={"session_id": session_id}).json()
    assert history["sessionKey"] == session_id
    assert [message["role"] for message in history["messages"][-2:]] == ["user", "assistant"]

    preview = client.post(
        "/sessions/preview",
        json={"keys": [session_id], "limit": 4, "maxChars": 120},
    ).json()
    assert preview["previews"][0]["status"] == "ok"

    reset = client.post("/sessions/reset", json={"session_id": session_id}).json()
    assert reset["ok"] is True


def test_dashboard_root_renders_2d_runtime():
    response = client.get("/")

    assert response.status_code == 200
    assert "Technical Office 2D Runtime" in response.text
    assert "EventSource" in response.text
    assert "PDF Onizleme" in response.text
    assert "Secili Adaylari Onayla" in response.text
    assert "Partlist Uret" in response.text
    assert "Learning Health" in response.text
    assert "/api/learning/health" in response.text
    assert "/api/learning/backfill" in response.text
    assert "managerHistory" in response.text
    assert "loadManagerSession" in response.text
    assert "/sessions/history" in response.text
    assert "Mudur dusunuyor" in response.text
    assert "AbortController" in response.text
    assert "CHAT_TIMEOUT_MS = 210000" in response.text
    assert "Mudur cevabi gecikti" in response.text


def test_manager_chat_persists_dashboard_session(monkeypatch):
    class FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, message, history=None, **kwargs):
            return type(
                "Result",
                (),
                {
                    "content": f"Dogal cevap: {message.splitlines()[0]}",
                    "used_llm": True,
                    "fallback_reason": None,
                    "tool_results": [],
                },
            )()

    monkeypatch.setattr("technical_office_runtime.app.AgentOrchestrator", FakeOrchestrator)
    session_id = "agent:teknik-ofis-muduru:test-dashboard-chat"
    client.post("/sessions/reset", json={"session_id": session_id})

    response = client.post(
        "/api/manager/chat",
        json={"message": "merhaba mudurum", "session_id": session_id, "history": []},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["used_codex"] is True
    assert payload["session_id"] == session_id
    history = client.get("/sessions/history", params={"session_id": session_id}).json()
    assert [message["role"] for message in history["messages"][-2:]] == ["user", "assistant"]


def test_manager_chat_strips_current_message_from_incoming_history(monkeypatch):
    class FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, message, history=None, **kwargs):
            assert history == []
            return type(
                "Result",
                (),
                {
                    "content": "Mudur cevabi",
                    "used_llm": True,
                    "fallback_reason": None,
                    "tool_results": [],
                },
            )()

    monkeypatch.setattr("technical_office_runtime.app.AgentOrchestrator", FakeOrchestrator)
    session_id = "agent:teknik-ofis-muduru:test-current-message-strip"
    client.post("/sessions/reset", json={"session_id": session_id})

    response = client.post(
        "/api/manager/chat",
        json={
            "message": "1. adimdan baslayalim",
            "session_id": session_id,
            "history": [{"role": "user", "content": "1. adimdan baslayalim"}],
        },
    )

    assert response.status_code == 200
    history = client.get("/sessions/history", params={"session_id": session_id}).json()["messages"]
    assert [message["role"] for message in history] == ["user", "assistant"]


def test_manager_memory_endpoint_exposes_persisted_turn(monkeypatch):
    class FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, message, history=None, **kwargs):
            return type(
                "Result",
                (),
                {
                    "content": "Karar: Bu is tamamlanmis kabul edilmemeli.",
                    "used_llm": False,
                    "fallback_reason": "local_manager_issue_discussion",
                    "tool_results": [],
                },
            )()

    monkeypatch.setattr("technical_office_runtime.app.AgentOrchestrator", FakeOrchestrator)
    session_id = "agent:teknik-ofis-muduru:test-memory-api"
    client.post("/sessions/reset", json={"session_id": session_id})

    response = client.post(
        "/api/manager/chat",
        json={
            "message": "danieli-1701 isinde pdf 26 sayfa ama 3 poz uretti",
            "session_id": session_id,
            "history": [],
        },
    )
    assert response.status_code == 200

    memory = client.get(
        "/api/manager/memory",
        params={"job_id": "danieli-1701", "session_id": session_id},
    ).json()

    assert memory["ok"] is True
    assert memory["facts"]
    assert memory["recent_events"]
    assert memory["stats"]["events"] >= 2


def test_manager_chat_rejects_non_utf8_json_without_500():
    response = client.post(
        "/api/manager/chat",
        content=b'{"message":"\xfc","session_id":"agent:teknik-ofis-muduru:bad-encoding"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert "UTF-8" in response.json()["detail"]


def test_jobs_api_upload_detail_and_run():
    job_id = "api-upload-test"
    session_id = "agent:teknik-ofis-muduru:test-direct-run"
    suite_root = Path(__file__).resolve().parents[2]
    job_dir = suite_root / "workspace" / "imports" / "jobs" / job_id
    output_dir = suite_root / "workspace" / "outputs" / "jobs" / job_id
    session_file = suite_root / "workspace" / "sessions" / "agent_teknik-ofis-muduru_test-direct-run.json"
    vault_file = suite_root / "workspace" / "manager_vault" / "jobs" / "api-upload-test.md"
    proposal_dir = suite_root / "journal" / "skill_proposals"
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)
    if session_file.exists():
        session_file.unlink()
    if vault_file.exists():
        vault_file.unlink()
    for path in proposal_dir.glob("*api-upload-test_auto_close.md"):
        path.unlink()

    response = client.post(
        "/api/jobs",
        data={"project_name": "API Upload Test", "job_id": job_id},
        files={"pdf_files": ("input.pdf", _vector_pdf_bytes(["POZ: API100", "PLAKA 200x100x10 S355"]), "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["job"]["job_id"] == job_id
    assert payload["job"]["pdf_count"] == 1

    run = client.post(f"/api/jobs/{job_id}/run", json={"autocad_live_policy": "off", "session_id": session_id})
    assert run.status_code == 200
    detail = run.json()["job"]
    assert detail["summary"]["ok"] is True
    assert detail["produced_count"] == 1
    assert detail["fsm_state"] == "completed"
    assert detail["partlist"]["ok"] is True
    assert (output_dir / "retrospective.json").exists()
    assert detail["pdfs"][0]["download_url"].endswith("/api/jobs/api-upload-test/files/input.pdf")
    assert detail["pdfs"][0]["preview_url"].endswith("/api/jobs/api-upload-test/files/input.pdf?inline=true")
    for path in proposal_dir.glob("*api-upload-test_auto_close.md"):
        path.unlink()
    if session_file.exists():
        session_file.unlink()
    if vault_file.exists():
        vault_file.unlink()


def test_approve_candidates_validates_before_writing_specs():
    job_id = "api-approval-test"
    session_id = "agent:teknik-ofis-muduru:test-auto-close"
    suite_root = Path(__file__).resolve().parents[2]
    job_dir = suite_root / "workspace" / "imports" / "jobs" / job_id
    output_dir = suite_root / "workspace" / "outputs" / "jobs" / job_id
    session_file = suite_root / "workspace" / "sessions" / "agent_teknik-ofis-muduru_test-auto-close.json"
    vault_file = suite_root / "workspace" / "manager_vault" / "jobs" / "api-approval-test.md"
    proposal_dir = suite_root / "journal" / "skill_proposals"
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)
    if session_file.exists():
        session_file.unlink()
    if vault_file.exists():
        vault_file.unlink()
    for path in proposal_dir.glob("*api-approval-test_auto_close.md"):
        path.unlink()
    client.post(
        "/api/jobs",
        data={"project_name": "API Approval Test", "job_id": job_id},
        files={"pdf_files": ("input.pdf", _vector_pdf_bytes(["placeholder"]), "application/pdf")},
    )

    invalid = client.post(
        f"/api/jobs/{job_id}/approve-candidates",
        json={"plates": [{"source_pdf": "input.pdf", "poz_no": "BAD", "width": -1, "height": 100, "thickness": 10}]},
    )
    assert invalid.status_code == 200
    assert invalid.json()["ok"] is False
    assert not (job_dir / "approved_plate_specs.json").exists()

    valid = client.post(
        f"/api/jobs/{job_id}/approve-candidates",
        json={
            "session_id": session_id,
            "plates": [
                {
                    "source_pdf": "input.pdf",
                    "source_page": 1,
                    "poz_no": "APP100",
                    "width": 200,
                    "height": 100,
                    "thickness": 10,
                    "material": "S355",
                    "quantity": 1,
                    "holes": [{"x": 50, "y": 25, "diameter": 18}],
                }
            ]
        },
    )

    assert valid.status_code == 200
    valid_payload = valid.json()
    assert valid_payload["ok"] is True
    assert valid_payload["job"]["fsm_state"] == "completed"
    assert valid_payload["partlist"]["ok"] is True
    assert valid_payload["partlist"]["rows"] == 1
    assert (job_dir / "approved_plate_specs.json").exists()
    assert (output_dir / "APP100" / "APP100.dxf").exists()
    assert Path(valid_payload["partlist"]["path"]).exists()
    assert (output_dir / "retrospective.json").exists()

    session_data = json.loads(session_file.read_text(encoding="utf-8"))
    assert any(
        message.get("role") == "assistant" and "`api-approval-test` isi tamamlandi" in message.get("content", "")
        for message in session_data.get("messages", [])
    )
    event_types = [
        json.loads(line)["type"]
        for line in (output_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for expected in [
        "production_started",
        "qc_completed",
        "partlist_started",
        "partlist_completed",
        "retrospective_written",
        "manager_notification",
        "completed",
    ]:
        assert expected in event_types
    proposals = list(proposal_dir.glob("*api-approval-test_auto_close.md"))
    assert proposals
    for path in proposals:
        path.unlink()
    if session_file.exists():
        session_file.unlink()
    if vault_file.exists():
        vault_file.unlink()


def test_approve_candidates_repairs_source_pdf_for_single_pdf_job():
    job_id = "api-approval-source-pdf-repair"
    session_id = "agent:teknik-ofis-muduru:test-source-pdf-repair"
    suite_root = Path(__file__).resolve().parents[2]
    job_dir = suite_root / "workspace" / "imports" / "jobs" / job_id
    output_dir = suite_root / "workspace" / "outputs" / "jobs" / job_id
    session_file = suite_root / "workspace" / "sessions" / "agent_teknik-ofis-muduru_test-source-pdf-repair.json"
    vault_file = suite_root / "workspace" / "manager_vault" / "jobs" / "api-approval-source-pdf-repair.md"
    proposal_dir = suite_root / "journal" / "skill_proposals"
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)
    if session_file.exists():
        session_file.unlink()
    if vault_file.exists():
        vault_file.unlink()
    for path in proposal_dir.glob("*api-approval-source-pdf-repair_auto_close.md"):
        path.unlink()
    client.post(
        "/api/jobs",
        data={"project_name": "API Approval Source PDF Repair", "job_id": job_id},
        files={"pdf_files": ("input.pdf", _vector_pdf_bytes(["placeholder"]), "application/pdf")},
    )

    response = client.post(
        f"/api/jobs/{job_id}/approve-candidates",
        json={
            "session_id": session_id,
            "plates": [
                {
                    "source_pdf": "wrong-name.pdf",
                    "source_page": 1,
                    "poz_no": "FIX100",
                    "width": 200,
                    "height": 100,
                    "thickness": 10,
                    "material": "S355",
                    "quantity": 1,
                    "holes": [],
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    approved = (job_dir / "approved_plate_specs.json").read_text(encoding="utf-8")
    assert '"source_pdf": "input.pdf"' in approved
    for path in proposal_dir.glob("*api-approval-source-pdf-repair_auto_close.md"):
        path.unlink()
    if session_file.exists():
        session_file.unlink()
    if vault_file.exists():
        vault_file.unlink()


def test_partlist_endpoint_blocks_until_job_summary_exists():
    job_id = "api-partlist-blocked"
    session_id = "agent:teknik-ofis-muduru:test-partlist-blocked"
    suite_root = Path(__file__).resolve().parents[2]
    job_dir = suite_root / "workspace" / "imports" / "jobs" / job_id
    output_dir = suite_root / "workspace" / "outputs" / "jobs" / job_id
    session_file = suite_root / "workspace" / "sessions" / "agent_teknik-ofis-muduru_test-partlist-blocked.json"
    vault_file = suite_root / "workspace" / "manager_vault" / "jobs" / "api-partlist-blocked.md"
    proposal_dir = suite_root / "journal" / "skill_proposals"
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)
    if session_file.exists():
        session_file.unlink()
    if vault_file.exists():
        vault_file.unlink()
    for path in proposal_dir.glob("*api-partlist-blocked_auto_close.md"):
        path.unlink()
    client.post(
        "/api/jobs",
        data={"project_name": "API Partlist Blocked", "job_id": job_id},
        files={"pdf_files": ("input.pdf", _vector_pdf_bytes(["placeholder"]), "application/pdf")},
    )

    response = client.post(f"/api/jobs/{job_id}/partlist", json={"session_id": session_id})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["partlist"]["manual_reviews"][0]["reason"] == "job_summary_missing"
    assert (output_dir / "partlist_manual_review_required.json").exists()
    assert (output_dir / "retrospective.json").exists()
    assert payload["job"]["fsm_state"] == "awaiting_approval"
    for path in proposal_dir.glob("*api-partlist-blocked_auto_close.md"):
        path.unlink()
    if session_file.exists():
        session_file.unlink()
    if vault_file.exists():
        vault_file.unlink()


def test_learning_health_and_job_backfill_create_retrospective():
    job_id = "api-learning-backfill"
    session_id = "agent:teknik-ofis-muduru:test-learning-backfill"
    suite_root = Path(__file__).resolve().parents[2]
    job_dir = suite_root / "workspace" / "imports" / "jobs" / job_id
    output_dir = suite_root / "workspace" / "outputs" / "jobs" / job_id
    session_file = suite_root / "workspace" / "sessions" / "agent_teknik-ofis-muduru_test-learning-backfill.json"
    vault_file = suite_root / "workspace" / "manager_vault" / "jobs" / "api-learning-backfill.md"
    proposal_dir = suite_root / "journal" / "skill_proposals"
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)
    if session_file.exists():
        session_file.unlink()
    if vault_file.exists():
        vault_file.unlink()
    for path in proposal_dir.glob("*api-learning-backfill_auto_close.md"):
        path.unlink()
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": "API Learning Backfill"}), encoding="utf-8")
    (output_dir / "job_summary.json").write_text(
        json.dumps({"ok": True, "produced": [{"poz_no": "L100", "ok": True}], "manual_reviews": []}),
        encoding="utf-8",
    )
    (output_dir / "API_Learning_Backfill_partlist.xlsx").write_text("placeholder", encoding="utf-8")

    health = client.get("/api/learning/health")
    assert health.status_code == 200
    assert any(item["job_id"] == job_id for item in health.json()["jobs_missing_retrospective"])

    dry_run = client.post(f"/api/jobs/{job_id}/learning/backfill", json={"dry_run": True, "session_id": session_id})
    assert dry_run.status_code == 200
    assert dry_run.json()["dry_run"] is True
    assert "write_retrospective" in dry_run.json()["actions"]
    assert not (output_dir / "retrospective.json").exists()

    response = client.post(f"/api/jobs/{job_id}/learning/backfill", json={"session_id": session_id})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["retrospective"]["path"].endswith("retrospective.json")
    assert (output_dir / "retrospective.json").exists()
    assert vault_file.exists()
    event_types = [
        json.loads(line)["type"]
        for line in (output_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert "retrospective_written" in event_types
    assert "manager_notification" in event_types

    health_after = client.get("/api/learning/health").json()
    assert not any(item["job_id"] == job_id for item in health_after["jobs_missing_retrospective"])
    for path in proposal_dir.glob("*api-learning-backfill_auto_close.md"):
        path.unlink()
    if session_file.exists():
        session_file.unlink()
    if vault_file.exists():
        vault_file.unlink()


def test_learning_backfill_ignores_nonterminal_summary_without_partlist():
    job_id = "api-learning-nonterminal"
    suite_root = Path(__file__).resolve().parents[2]
    job_dir = suite_root / "workspace" / "imports" / "jobs" / job_id
    output_dir = suite_root / "workspace" / "outputs" / "jobs" / job_id
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(
        "\ufeff" + json.dumps({"job_id": job_id, "project_name": "API Learning Nonterminal"}),
        encoding="utf-8",
    )
    (output_dir / "job_summary.json").write_text(
        json.dumps({"ok": True, "produced": [{"poz_no": "N100", "ok": True}], "manual_reviews": []}),
        encoding="utf-8",
    )

    health = client.get("/api/learning/health").json()
    assert not any(item["job_id"] == job_id for item in health["jobs_missing_retrospective"])

    response = client.post(f"/api/jobs/{job_id}/learning/backfill", json={"dry_run": False})
    assert response.status_code == 200
    assert response.json()["eligible"] is False
    assert not (output_dir / "retrospective.json").exists()
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)


def test_learning_health_reports_open_note_state_violation():
    job_id = "api-learning-open-note"
    suite_root = Path(__file__).resolve().parents[2]
    job_dir = suite_root / "workspace" / "imports" / "jobs" / job_id
    output_dir = suite_root / "workspace" / "outputs" / "jobs" / job_id
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": job_id, "project_name": "API Learning Open Note"}), encoding="utf-8")
    (output_dir / "fsm_state.json").write_text(json.dumps({"state": "completed"}), encoding="utf-8")
    (output_dir / "manager_issue_notes.jsonl").write_text(
        json.dumps({"status": "open", "tags": ["pah/kose eksigi"], "affected_pozs": ["210"], "message": "pah eksik"}) + "\n",
        encoding="utf-8",
    )

    health = client.get("/api/learning/health").json()

    assert any(item["job_id"] == job_id for item in health["jobs_with_open_notes"])
    assert any(item["job_id"] == job_id for item in health["open_note_state_violations"])
    assert health["ok"] is False
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)


def test_job_file_endpoint_serves_output_files():
    job_id = "api-file-output"
    suite_root = Path(__file__).resolve().parents[2]
    job_dir = suite_root / "workspace" / "imports" / "jobs" / job_id
    output_dir = suite_root / "workspace" / "outputs" / "jobs" / job_id
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text('{"job_id":"api-file-output","project_name":"API File Output"}', encoding="utf-8")
    nested = output_dir / "P100"
    nested.mkdir(parents=True)
    (nested / "P100.dxf").write_text("0\nEOF\n", encoding="utf-8")

    response = client.get(f"/api/jobs/{job_id}/files/P100/P100.dxf")

    assert response.status_code == 200
    assert "EOF" in response.text


def test_pdf_file_endpoint_supports_inline_preview():
    job_id = "api-inline-pdf-preview"
    suite_root = Path(__file__).resolve().parents[2]
    job_dir = suite_root / "workspace" / "imports" / "jobs" / job_id
    output_dir = suite_root / "workspace" / "outputs" / "jobs" / job_id
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text('{"job_id":"api-inline-pdf-preview","project_name":"API Inline PDF Preview"}', encoding="utf-8")
    (job_dir / "input.pdf").write_bytes(_vector_pdf_bytes(["POZ: P100"]))

    inline = client.get(f"/api/jobs/{job_id}/files/input.pdf?inline=true")
    download = client.get(f"/api/jobs/{job_id}/files/input.pdf")

    assert inline.status_code == 200
    assert inline.headers["content-disposition"].startswith("inline;")
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")


def test_job_events_replay_terminal_stream_finishes():
    job_id = "api-events-terminal"
    suite_root = Path(__file__).resolve().parents[2]
    job_dir = suite_root / "workspace" / "imports" / "jobs" / job_id
    output_dir = suite_root / "workspace" / "outputs" / "jobs" / job_id
    _cleanup_dir(job_dir)
    _cleanup_dir(output_dir)
    job_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text('{"job_id":"api-events-terminal","project_name":"API Events"}', encoding="utf-8")
    (output_dir / "events.jsonl").write_text(
        '{"type":"started","timestamp":1,"payload":{"message":"start"}}\n'
        '{"type":"completed","timestamp":2,"payload":{"status":"completed"}}\n',
        encoding="utf-8",
    )

    with client.stream("GET", f"/api/events/{job_id}") as response:
        assert response.status_code == 200
        text = "".join(response.iter_text())

    assert '"type": "started"' in text
    assert '"type": "completed"' in text


def _vector_pdf_bytes(text_lines: list[str]) -> bytes:
    text_commands = "\n".join(f"({line}) Tj" for line in text_lines)
    stream = (
        "q\n"
        "BT /F1 12 Tf 10 180 Td\n"
        f"{text_commands}\n"
        "ET\n"
        "0 0 m 200 0 l 200 100 l 0 100 l h S\n"
        "Q\n"
    )
    pdf = (
        "%PDF-1.4\n"
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R >> endobj\n"
        f"4 0 obj << /Length {len(stream)} >> stream\n{stream}\nendstream endobj\n"
        "xref\n0 5\n0000000000 65535 f \n"
        "trailer << /Root 1 0 R >>\n%%EOF\n"
    )
    return pdf.encode("latin-1")


def _cleanup_dir(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    path.rmdir()
