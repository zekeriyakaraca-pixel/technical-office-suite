from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent_context import build_system_prompt, load_agent_context
from .codex_bridge import CodexBridge, CodexRunRequest
from .config import RuntimePaths, get_paths
from .gemini_bridge import GeminiBridge, get_gemini_bridge
from .guided_flows import (
    FLOW_CORNER_RELIEF,
    GuidedFlowState,
    corner_relief_state,
    get_guided_flow_store,
)
from .job_fsm import JobState, get_fsm
from .text_normalization import normalize_search_text, repair_text
from .tools import ToolRegistry
from .completion import append_job_event, complete_approved_job


MANAGER_CODEX_READ_TIMEOUT_SECONDS = 90
MANAGER_CODEX_WRITE_TIMEOUT_SECONDS = 180
MANAGER_VISUAL_CANDIDATE_MAX_PAGES = 80
_SELECTED_CONTEXT_MARKER = "[Secili is baglami:"
_MEMORY_CONTEXT_MARKER = "[Mudur hafiza baglami:"


@dataclass
class AgentRunResult:
    content: str
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    used_llm: bool = False
    fallback_reason: str | None = None


class AgentOrchestrator:
    def __init__(
        self,
        paths: RuntimePaths | None = None,
        *,
        bridge: CodexBridge | None = None,
        agent_id: str = "teknik-ofis-muduru",
        allow_codex: bool = True,
    ) -> None:
        self.paths = paths or get_paths()
        self.bridge = bridge or CodexBridge(self.paths)
        self.agent_id = agent_id
        self.allow_codex = allow_codex
        self.tools = ToolRegistry(self.paths)

    def run(
        self,
        user_text: str,
        history: list[dict[str, str]] | None = None,
        *,
        session_id: str | None = None,
        selected_job_id: str | None = None,
        trigger: dict[str, Any] | None = None,
    ) -> AgentRunResult:
        history = history or []
        text = repair_text(user_text.strip())
        if self.agent_id == "teknik-ofis-muduru":
            triggered = self._handle_manager_trigger(
                trigger=trigger,
                session_id=session_id,
                selected_job_id=selected_job_id,
            )
            if triggered is not None:
                return triggered
            open_flow = self._open_corner_relief_flow_state(
                session_id=session_id,
                selected_job_id=selected_job_id,
                visible_text=_visible_user_text(text),
            )
            if open_flow is not None:
                # "gorsel analiz yap/tekrar" corner relief loop'tan kacis — visual analysis'e git
                if _looks_like_missing_candidate_extraction_request(text):
                    return self._handle_missing_candidate_extraction(text, history)
                return self._handle_corner_reliefs_conversation(
                    text,
                    history,
                    session_id=session_id,
                    flow_state=open_flow,
                )
        if not text:
            return AgentRunResult(content="Bir is veya soru yazarsan teknik ofis adina ilerletebilirim.")
        if self.agent_id != "teknik-ofis-muduru":
            return self._run_codex_manager(text, history)
        if _looks_like_lightweight_manager_chat(text):
            return AgentRunResult(
                content=_format_lightweight_manager_response(self._available_job_ids()),
                fallback_reason="local_manager_chat",
            )
        if _looks_like_job_restart_request(text):
            return self._handle_job_restart_request(text, history)
        if _looks_like_apply_manager_decision_request(text):
            return self._handle_apply_manager_decisions(text, history)
        if _looks_like_missing_candidate_extraction_request(text):
            return self._handle_missing_candidate_extraction(text, history)
        if _looks_like_poz_correction_action_request(text):
            return self._handle_poz_correction_action(text, history, session_id=session_id)
        if _looks_like_job_completion_step_request(text):
            return self._handle_job_completion_step(text, history)
        if _looks_like_job_completion_continue_request(text):
            return self._handle_job_completion_continue(text, history)
        if _looks_like_position_info_resolution_request(text):
            return self._handle_position_info_resolution(text, history, session_id=session_id)
        if _looks_like_page_exclusion_request(text):
            return self._handle_page_exclusion_request(text, history, session_id=session_id)
        if _looks_like_mark_column_position_hint_request(text):
            return self._handle_mark_column_position_hint(text, history, session_id=session_id)
        if _looks_like_deep_output_inspection_request(text):
            return self._handle_deep_output_inspection(text, history)
        if _looks_like_manual_review_detail_request(text):
            return self._handle_manual_review_detail_request(text, history)
        if _looks_like_job_status_request(text):
            return self._handle_job_status_request(text, history)
        if _looks_like_skill_promote_request(text):
            return self._handle_skill_promote_request(text)
        if _looks_like_skill_update_request(text):
            return self._handle_skill_update_request(text, history, session_id=session_id)
        if _looks_like_project_edit_request(text):
            if self.allow_codex:
                return self._run_codex_manager(text, history or [])
            return self._run_fallback(text, reason="codex_disabled")
        lower_visible = _normalize_turkish(_visible_user_text(text))
        if _looks_like_corner_reliefs_help_request(lower_visible) or _last_message_was_corner_reliefs_question(history):
            return self._handle_corner_reliefs_conversation(
                text,
                history,
                session_id=session_id,
            )
        if _looks_like_issue_discussion_request(text):
            raw = _format_issue_discussion_response(self.paths, text)
            return self._synthesize_query_with_gemini(raw, text, history, fallback_reason="local_manager_issue_discussion")
        if _looks_like_job_learning_request(text):
            return self._handle_job_learning_request(text, history)
        if _should_route_locally(text):
            routed = self._run_fallback(text, reason="local_tool_router")
            if routed.tool_results:
                return routed
        if _looks_like_runtime_ready_request(text) and not getattr(self.bridge, "executable", None):
            return AgentRunResult(content=_format_runtime_ready_response(), fallback_reason="local_runtime_status")
        if _looks_like_agent_creation_request(_normalize_turkish(_visible_user_text(text))):
            title = _extract_agent_title(_visible_user_text(text))
            if title and title != "Yeni Teknik Ofis Ajani":
                return self._run_local_agent_draft(title, text)
        if self.allow_codex:
            gemini = get_gemini_bridge()
            if gemini:
                return self._run_gemini_manager(gemini, text, history)
            return self._run_codex_manager(text, history)
        return self._run_fallback(text, reason="codex_disabled")

    def _run_codex_manager(self, user_text: str, history: list[dict[str, str]]) -> AgentRunResult:
        if self.agent_id != "teknik-ofis-muduru":
            return AgentRunResult(
                content=(
                    "Bu runtime'da kullanici chat'i yalnizca `teknik-ofis-muduru` uzerinden aciktir. "
                    "Diger ajanlar is akisi eventi uretir; dogrudan chat cevabi vermez."
                ),
                fallback_reason="agent_chat_disabled",
            )
        context = load_agent_context(self.paths, "teknik-ofis-muduru")
        project_edit = _looks_like_project_edit_request(user_text)
        base_system = build_system_prompt(context)
        try:
            from .memory_bridge import get_memory_bridge as _get_bridge
            _bridge = _get_bridge(self.paths.workspace_root)
            _recent = _bridge.get_recent_patterns(limit=3)
            if _recent:
                _lines = "\n".join(
                    f"- {p['job_id']}: {p['pattern_count']} plaka, güven {p['confidence']:.2f} ({p['source']})"
                    for p in _recent
                )
                base_system += f"\n\n## Son Başarılı Çizim Desenleri (Hafıza)\n{_lines}"
        except Exception:
            pass
        try:
            from .agent_context import load_expert_agent_memories
            _expert_mem = load_expert_agent_memories(self.paths)
            if _expert_mem:
                base_system += f"\n\n## Uzman Hafızaları ve Kuralları\n{_expert_mem}"
        except Exception:
            pass
        prompt = _manager_prompt(base_system, user_text, history, project_edit=project_edit)
        sandbox = "workspace-write" if project_edit else "read-only"
        timeout_seconds = MANAGER_CODEX_WRITE_TIMEOUT_SECONDS if project_edit else MANAGER_CODEX_READ_TIMEOUT_SECONDS
        result = self.bridge.run(
            CodexRunRequest(
                prompt=prompt,
                agent_id="teknik-ofis-muduru",
                sandbox=sandbox,
                timeout_seconds=timeout_seconds,
            )
        )
        if result.ok and result.content.strip():
            return AgentRunResult(content=result.content.strip(), used_llm=True)
        reason = result.error or "Codex CLI cevap uretmedi."
        if result.content.strip():
            return AgentRunResult(
                content=(
                    f"{result.content.strip()}\n\n"
                    f"Not: Codex CLI calismasi tamamlanmadan kesildi ({reason}). "
                    "Bu ara sonucu kayda alip bir sonraki adimda daha dar kapsamli devam etmeliyim."
                ),
                used_llm=True,
                fallback_reason=reason,
            )
        return self._run_fallback(user_text, reason=reason)

    def _run_local_agent_draft(self, title: str, user_text: str) -> AgentRunResult:
        mission = _extract_agent_mission(user_text)
        args: dict = {"title": title}
        if mission:
            args["mission"] = mission
        result = self.tools.run("draft_agent", args)
        if result.get("ok"):
            return AgentRunResult(
                content=(
                    f"Ajan taslagini hazirladim: **{result['draft_id']}**\n"
                    f"Dosya: `{result['path']}`\n\n"
                    f"Aktif etmek icin terminalde su komutu calistir:\n"
                    f"`toffice agent approve {result['draft_id']}`"
                ),
                tool_results=[{"tool": "draft_agent", "result": result}],
            )
        return AgentRunResult(
            content=f"Ajan taslagini olusturamadim: {result.get('error', 'bilinmeyen hata')}",
            tool_results=[{"tool": "draft_agent", "result": result}],
            fallback_reason="draft_agent_failed",
        )

    def _run_gemini_manager(
        self, gemini: GeminiBridge, user_text: str, history: list[dict[str, str]]
    ) -> AgentRunResult:
        context = load_agent_context(self.paths, "teknik-ofis-muduru")
        system_prompt = _gemini_manager_system_prompt(build_system_prompt(context))

        # Expert agent memories (MEMORY.md + RULES.md)
        try:
            from .agent_context import load_expert_agent_memories
            expert_mem = load_expert_agent_memories(self.paths)
            if expert_mem:
                system_prompt += f"\n\n## Uzman Hafızaları ve Kuralları\n{expert_mem}"
        except Exception:
            pass

        # Memory bridge — son 3 başarılı çizim deseni
        try:
            from .memory_bridge import get_memory_bridge as _gb
            _bridge = _gb(self.paths.workspace_root)
            _recent = _bridge.get_recent_patterns(limit=3)
            if _recent:
                _lines = "\n".join(
                    f"- {p['job_id']}: {p['pattern_count']} plaka, güven {p['confidence']:.2f}"
                    for p in _recent
                )
                system_prompt += f"\n\n## Son Başarılı Çizim Desenleri\n{_lines}"
        except Exception:
            pass

        visible_message = _visible_user_text(user_text)

        # Seçili iş live context
        selected_job_id = _selected_job_id_from_context(user_text)
        if selected_job_id:
            job_ctx = _build_live_job_context(self.paths, selected_job_id)
            if job_ctx:
                system_prompt += f"\n\n{job_ctx}"

        memory_block = _manager_memory_context_block(user_text)
        if memory_block.strip() and memory_block.strip() != "(yok)":
            visible_message = f"{visible_message}\n\n[Mudur Hafizasi]\n{memory_block}"
        result = gemini.run(system_prompt, history, visible_message)
        if result.ok and result.content.strip():
            return AgentRunResult(content=result.content.strip(), used_llm=True)
        # Gemini basarisiz → Codex CLI varsa ona gecis yap
        if self.bridge and getattr(self.bridge, "executable", None):
            return self._run_codex_manager(user_text, history)
        # Codex da yoksa Gemini hata mesajini goster
        return AgentRunResult(
            content=result.content or "Gemini API cevap uretmedi.",
            used_llm=False,
            fallback_reason=result.error or "gemini_error",
        )

    def _synthesize_query_with_gemini(
        self,
        data_block: str,
        user_text: str,
        history: list[dict[str, str]],
        *,
        fallback_reason: str,
    ) -> AgentRunResult:
        """Ham veri bloğunu Gemini'ye vererek doğal yönetici yanıtı üretir.
        Gemini yoksa data_block'u olduğu gibi döndürür (mevcut davranış).
        """
        try:
            from .gemini_bridge import get_gemini_bridge as _get_gemini
            gemini = _get_gemini()
        except Exception:
            gemini = None
        if not gemini:
            return AgentRunResult(content=data_block, fallback_reason=fallback_reason)

        context = load_agent_context(self.paths, "teknik-ofis-muduru")
        system_prompt = _gemini_manager_system_prompt(build_system_prompt(context))

        selected_job_id = _selected_job_id_from_context(user_text)
        if selected_job_id:
            job_ctx = _build_live_job_context(self.paths, selected_job_id)
            if job_ctx:
                system_prompt += f"\n\n{job_ctx}"

        synthesis_message = (
            f"[Sistem okuması]\n{data_block}\n\n"
            f"[Kullanıcı sorusu]\n{_visible_user_text(user_text)}\n\n"
            "Yukarıdaki sistem verisini kullanarak, teknik-ofis-müdürü olarak "
            "doğal, karar odaklı ve Türkçe bir yanıt ver. "
            "Sistem verisini olduğu gibi kopyalama; müdür gibi yorum yap ve sonraki adımı belirt."
        )

        result = gemini.run(system_prompt, history, synthesis_message)
        if result.ok and result.content.strip():
            return AgentRunResult(content=result.content.strip(), used_llm=True, fallback_reason=fallback_reason)
        return AgentRunResult(content=data_block, fallback_reason=fallback_reason)

    def _run_fallback(self, user_text: str, reason: str) -> AgentRunResult:
        text = user_text.strip()
        visible_text = _visible_user_text(text)
        lower = _normalize_turkish(visible_text)
        selected_job_id = _selected_job_id_from_context(text)
        memory_job_id = _job_id_from_memory_context(text)
        job_id = (
            _extract_job_id(visible_text)
            or (selected_job_id if _looks_like_selected_job_reference(lower) else None)
            or (memory_job_id if _looks_like_selected_job_reference(lower) else None)
        )
        tool_results: list[dict[str, Any]] = []

        if _looks_like_project_edit_request(text):
            return AgentRunResult(
                content=_format_project_edit_unavailable_response(reason),
                tool_results=tool_results,
                fallback_reason=reason,
            )

        if _looks_like_list_jobs_request(lower):
            result = self.tools.run("list_jobs", {})
            tool_results.append({"tool": "list_jobs", "result": result})
            return AgentRunResult(content=_format_jobs_response(result), tool_results=tool_results, fallback_reason=reason)

        if _looks_like_create_job_request(lower) and job_id:
            project_name = _extract_project_name(text) or job_id
            result = self.tools.run("create_job", {"job_id": job_id, "project_name": project_name})
            tool_results.append({"tool": "create_job", "result": result})
            if result.get("ok"):
                return AgentRunResult(content=result["message"], tool_results=tool_results, fallback_reason=reason)
            return AgentRunResult(content=f"Is klasoru olusturulamadi: {result.get('error')}", tool_results=tool_results, fallback_reason=reason)

        possible_job_id = job_id or (_extract_numeric_reference(visible_text) if _looks_like_job_reference(lower) else None)
        if possible_job_id and _looks_like_run_request(lower):
            result = self.tools.run(
                "run_autocad_job",
                {"job_id": possible_job_id, "autocad_live_policy": _policy_from_text(lower)},
            )
            tool_results.append({"tool": "run_autocad_job", "result": result})
            if result.get("ok"):
                return AgentRunResult(content=result["message"], tool_results=tool_results, fallback_reason=reason)
            return AgentRunResult(content=_format_tool_error("Is calistirilamadi", result), tool_results=tool_results, fallback_reason=reason)

        if possible_job_id and "partlist" in lower:
            result = self.tools.run("create_partlist", {"job_id": possible_job_id})
            tool_results.append({"tool": "create_partlist", "result": result})
            if result.get("ok"):
                partlist = result.get("partlist", {})
                return AgentRunResult(
                    content=f"Partlist hazir: {partlist.get('path')} ({partlist.get('rows', 0)} satir)",
                    tool_results=tool_results,
                    fallback_reason=reason,
                )
            partlist = result.get("partlist", {})
            reviews = partlist.get("manual_reviews") if isinstance(partlist, dict) else None
            return AgentRunResult(
                content=f"Partlist acilmadi: QC/manual review kapisi kapanmadi. Detay: {reviews or result.get('error')}",
                tool_results=tool_results,
                fallback_reason=reason,
            )

        if "qc" in lower and (job_id or _extract_poz_no(text)):
            poz_no = _extract_poz_no(visible_text)
            if not job_id and poz_no:
                job_id = self._find_job_for_poz(poz_no)
            if not job_id:
                result = self.tools.run("list_jobs", {})
                tool_results.append({"tool": "list_jobs", "result": result})
                return AgentRunResult(
                    content=f"QC raporu icin hangi ise bakacagimi bulamadim.\n{_format_jobs_response(result)}",
                    tool_results=tool_results,
                    fallback_reason=reason,
                )
            if poz_no:
                result = self.tools.run("read_qc_report", {"job_id": job_id, "poz_no": poz_no})
                tool_results.append({"tool": "read_qc_report", "result": result})
                if result.get("ok"):
                    return AgentRunResult(content=_format_qc_response(result), tool_results=tool_results, fallback_reason=reason)
            result = self.tools.run("read_job_summary", {"job_id": job_id})
            tool_results.append({"tool": "read_job_summary", "result": result})
            return AgentRunResult(content=_format_summary_response(result), tool_results=tool_results, fallback_reason=reason)

        if _looks_like_agent_creation_request(lower):
            title = _extract_agent_title(visible_text)
            result = self.tools.run("draft_agent", {"title": title, "mission": f"{title} icin teknik ofis gorevlerini yurutmek."})
            tool_results.append({"tool": "draft_agent", "result": result})
            if result.get("ok"):
                return AgentRunResult(
                    content=(
                        f"Yeni ajan taslagini hazirladim: {result['draft_id']}\n"
                        f"Dosya: {result['path']}\n"
                        f"Aktif etmek icin CLI'da `toffice agent approve {result['draft_id']}` komutunu kullan."
                    ),
                    tool_results=tool_results,
                    fallback_reason=reason,
                )

        return AgentRunResult(
            content=(
                "Technical Office Runtime hazir, fakat Codex CLI cevabi su anda kullanilamiyor "
                f"({reason}). Dogrudan isler icin sunlari kullanabilirsin:\n"
                "- `toffice job run test-001 --autocad off`\n"
                "- `run job test-001 autocad off`\n"
                "- `toffice doctor` ile Codex CLI durumunu kontrol et."
            ),
            tool_results=tool_results,
            fallback_reason=reason,
        )

    def _available_job_ids(self) -> list[str]:
        result = self.tools.run("list_jobs", {})
        jobs = result.get("jobs", []) if result.get("ok") else []
        return [str(job.get("job_id")) for job in jobs if isinstance(job, dict) and job.get("job_id")]

    def _find_job_for_poz(self, poz_no: str) -> str | None:
        for job_id in self._available_job_ids():
            qc_path = self.paths.jobs_output_root / job_id / poz_no / f"{poz_no}_qc.json"
            if qc_path.exists():
                return job_id
        job_ids = self._available_job_ids()
        return job_ids[0] if len(job_ids) == 1 else None

    def _handle_job_restart_request(self, text: str, history: list[dict[str, str]]) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        lower = _normalize_turkish(visible_text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = _extract_job_id(visible_text) or selected_job_id or _job_id_from_memory_context(text) or _job_id_from_recent_history(history)
        if not job_id:
            return AgentRunResult(
                content="Hangi isi bastan baslatacagimi belirt. Ornek: `danieli-1701 temiz baslat`.",
                fallback_reason="local_job_restart_plan",
            )
        # Onaylanmis spec'te gecersiz corner_reliefs varsa → tam sifirlama yerine sadece o alanı düzelt ve yeniden çalıştır
        if _looks_like_confirmed_job_reset(lower):
            patch_summary = _try_patch_approved_spec_corner_reliefs(self.paths.jobs_import_root / job_id)
            if patch_summary is not None:
                fsm = get_fsm(self.paths.jobs_output_root)
                fsm.force_transition(job_id, JobState.PRODUCING, reason="corner_relief_patch_rerun")
                run_result = self.tools.run("run_autocad_job", {"job_id": job_id, "autocad_live_policy": "off"})
                if run_result.get("ok"):
                    raw = (
                        f"`{job_id}` — `approved_plate_specs.json` duzeltildi ({patch_summary}). "
                        "Pipeline yeniden baslatildi."
                    )
                    result_obj = self._synthesize_query_with_gemini(raw, text, history, fallback_reason="local_approved_spec_patch")
                    result_obj.tool_results = [{"tool": "run_autocad_job", "result": run_result}]
                    return result_obj
                raw = (
                    f"`{job_id}` — `approved_plate_specs.json` duzeltildi ({patch_summary}) "
                    f"ancak pipeline baslatma hatasi: {run_result.get('error') or 'bilinmeyen hata'}"
                )
                result_obj = self._synthesize_query_with_gemini(raw, text, history, fallback_reason="local_approved_spec_patch")
                result_obj.tool_results = [{"tool": "run_autocad_job", "result": run_result}]
                return result_obj
            result = self.tools.run("reset_job_for_rerun", {"job_id": job_id})
            if result.get("ok"):
                return AgentRunResult(
                    content=str(result.get("message") or f"`{job_id}` temiz baslangic icin hazirlandi."),
                    tool_results=[{"tool": "reset_job_for_rerun", "result": result}],
                    fallback_reason="local_job_reset",
                )
            return AgentRunResult(
                content=f"`{job_id}` temiz baslatilamadi: {result.get('error')}",
                tool_results=[{"tool": "reset_job_for_rerun", "result": result}],
                fallback_reason="local_job_reset",
            )
        return AgentRunResult(
            content=_format_job_restart_plan(self.paths, job_id),
            fallback_reason="local_job_restart_plan",
        )

    def _handle_job_status_request(self, text: str, history: list[dict[str, str]]) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = _extract_job_id(visible_text) or selected_job_id or _job_id_from_memory_context(text) or _job_id_from_recent_history(history)
        if not job_id:
            return AgentRunResult(
                content="Hangi isin durumunu okuyacagimi belirt. Ornek: `danieli-1701 ne durumdayiz`.",
                fallback_reason="local_job_status",
            )
        raw = _format_job_status_response(self.paths, job_id)
        return self._synthesize_query_with_gemini(raw, text, history, fallback_reason="local_job_status")

    def _handle_apply_manager_decisions(self, text: str, history: list[dict[str, str]]) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = _extract_job_id(visible_text) or selected_job_id or _job_id_from_memory_context(text) or _job_id_from_recent_history(history)
        if not job_id:
            return AgentRunResult(
                content="Hangi is icin mudur kararini uygulayacagimi bulamadim. Bir job ID sec veya yaz.",
                fallback_reason="local_manager_decision_apply",
            )
        result = _apply_manager_decisions(self.paths, self.tools, job_id)
        return AgentRunResult(
            content=_format_apply_manager_decision_response(result),
            tool_results=[{"tool": "apply_manager_decisions", "result": result}],
            fallback_reason="local_manager_decision_apply",
        )

    def _handle_missing_candidate_extraction(self, text: str, history: list[dict[str, str]]) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = _extract_job_id(visible_text) or selected_job_id or _job_id_from_memory_context(text) or _job_id_from_recent_history(history)
        if not job_id:
            return AgentRunResult(
                content="Hangi is icin gorsel analizi baslataacagimi bulamadim. Bir job ID sec veya yaz.",
                fallback_reason="local_missing_candidate_extraction",
            )
        scan = _extract_missing_pdf_candidates(self.paths, self.bridge, job_id)
        tool_results = [{"tool": "extract_missing_pdf_candidates", "result": scan}]
        # Eksik sayfa varsa pipeline'i tetikle (gorsel Codex cikarma)
        run_result = self.tools.run("run_autocad_job", {"job_id": job_id, "autocad_live_policy": "off"})
        tool_results.append({"tool": "run_autocad_job", "result": run_result})
        content = _format_missing_candidate_extraction_response(scan)
        if run_result.get("ok"):
            content += (
                f"\n\nGorsel analiz pipeline'i baslatildi (`{job_id}`)."
                " Codex eksik sayfalari tarayacak ve yeni aday pozlar uretecek."
                " Islem tamamlaninca dashboard'dan adaylari inceleyip onaylayabilirsin."
            )
        else:
            content += f"\n\nUyari: Pipeline baslatma hatasi: {run_result.get('error') or run_result.get('message')}"
        return AgentRunResult(
            content=content,
            tool_results=tool_results,
            fallback_reason="local_missing_candidate_extraction",
        )

    def _handle_skill_update_request(
        self,
        text: str,
        history: list[dict[str, str]],
        *,
        session_id: str | None = None,
    ) -> AgentRunResult:
        import datetime as _dt
        visible = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        target_agent = _extract_target_agent_from_text(visible)
        ts = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
        proposal_id = f"{target_agent}-memory-{ts}"
        proposal_path = self.paths.suite_root / "journal" / "skill_proposals" / f"{proposal_id}.md"
        proposal_path.parent.mkdir(parents=True, exist_ok=True)
        proposal_path.write_text(
            f"# Skill Proposal: {proposal_id}\n\n"
            f"**Hedef Agent**: {target_agent}\n"
            f"**Hedef Dosya**: MEMORY.md\n"
            f"**Ilgili Is**: {selected_job_id or 'belirtilmedi'}\n\n"
            f"## Ogrenme Notu\n{visible.strip()}\n\n"
            f"## Onay\nBu notu `agents/{target_agent}/MEMORY.md` dosyasina eklemek icin "
            f"mudur chat'te 'proposal {proposal_id} onayla' yaz.\n",
            encoding="utf-8",
        )
        return AgentRunResult(
            content=(
                f"Ogrenme notu `journal/skill_proposals/{proposal_id}.md` olarak kaydedildi. "
                f"Hedef: `agents/{target_agent}/MEMORY.md`. "
                f"Gercek dosyaya eklemek icin: **'proposal {proposal_id} onayla'** yaz."
            ),
            tool_results=[{"tool": "skill_update_proposal", "proposal_id": proposal_id, "target": target_agent}],
            fallback_reason="local_skill_update_proposal",
        )

    def _handle_skill_promote_request(self, text: str) -> AgentRunResult:
        import datetime as _dt
        visible = _visible_user_text(text)
        match = re.search(r"proposal\s+([a-z0-9\-]+)", _normalize_turkish(visible.lower()))
        if not match:
            return AgentRunResult(
                content=(
                    "Onaylanacak proposal ID'sini bulamadim. "
                    "Ornek: 'proposal autocad-uzman-1-memory-20260514123456 onayla'"
                ),
                fallback_reason="local_skill_promote",
            )
        proposal_id = match.group(1)
        proposal_path = self.paths.suite_root / "journal" / "skill_proposals" / f"{proposal_id}.md"
        if not proposal_path.exists():
            return AgentRunResult(
                content=f"Proposal dosyasi bulunamadi: `{proposal_path.name}`",
                fallback_reason="local_skill_promote",
            )
        proposal_text = proposal_path.read_text(encoding="utf-8")
        target_match = re.search(r"\*\*Hedef Agent\*\*:\s*(.+)", proposal_text)
        target_agent = target_match.group(1).strip() if target_match else None
        if not target_agent or target_agent in ("belirtilmedi", ""):
            return AgentRunResult(
                content="Proposal'daki hedef agent okunamadi. Elle duzenle ve tekrar dene.",
                fallback_reason="local_skill_promote",
            )
        note_match = re.search(r"## Ogrenme Notu\n(.+?)(?=\n## |\Z)", proposal_text, re.DOTALL)
        note_content = note_match.group(1).strip() if note_match else proposal_text.strip()
        memory_path = self.paths.suite_root / "agents" / target_agent / "MEMORY.md"
        if not memory_path.exists():
            memory_path.write_text(f"# {target_agent} Memory\n\n", encoding="utf-8")
        existing = memory_path.read_text(encoding="utf-8")
        ts = _dt.datetime.now().strftime("%Y-%m-%d")
        addition = f"\n## {ts} — Mudur Onaylidir\n{note_content}\n"
        memory_path.write_text(existing + addition, encoding="utf-8")
        return AgentRunResult(
            content=(
                f"`agents/{target_agent}/MEMORY.md` guncellendi. "
                f"Proposal `{proposal_id}` onaylandi ve kalici hafizaya eklendi."
            ),
            tool_results=[{"tool": "skill_promote", "target": str(memory_path), "proposal": proposal_id}],
            fallback_reason="local_skill_promote",
        )

    def _handle_poz_correction_action(
        self,
        text: str,
        history: list[dict[str, str]],
        *,
        session_id: str | None = None,
    ) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = _extract_job_id(visible_text) or selected_job_id or _job_id_from_memory_context(text) or _job_id_from_recent_history(history)
        if not job_id:
            return AgentRunResult(
                content="Hangi is icin poz duzeltmesi yapacagimi bulamadim. Bir job ID sec veya yaz.",
                fallback_reason="local_poz_correction_action",
            )
        known_pozs = _known_job_pozs(self.paths, job_id)
        poz_nos = _extract_poz_numbers(visible_text, allowed_pozs=known_pozs)
        poz_no = poz_nos[-1] if poz_nos else _latest_referenced_poz_from_notes(self.paths, job_id, allowed_pozs=known_pozs)
        if not poz_no:
            return AgentRunResult(
                content=f"`{job_id}` icin duzeltilecek pozu netlestiremedim. Ornek: `Poz 210 yeniden uret`.",
                fallback_reason="local_poz_correction_action",
            )
        correction = _latest_corner_correction_for_poz(self.paths, job_id, poz_no, visible_text, history)
        if correction is None:
            return AgentRunResult(
                content=(
                    f"`{job_id}` Poz {poz_no} icin aksiyon istegini aldim; fakat uygulanacak pah/kose olcusunu "
                    "notlardan net okuyamadim. Beklenen degeri `sol ust pah 10x120, 120 uzun kenar dogrultusunda` gibi yaz."
                ),
                fallback_reason="local_poz_correction_action",
            )

        apply_result = _apply_corner_relief_correction(self.paths, self.tools, job_id, poz_no, correction, session_id=session_id)
        return AgentRunResult(
            content=_format_poz_correction_response(apply_result),
            tool_results=[{"tool": "apply_corner_relief_correction", "result": apply_result}],
            fallback_reason="local_poz_correction_action",
        )

    def _handle_position_info_resolution(
        self,
        text: str,
        history: list[dict[str, str]],
        *,
        session_id: str | None = None,
    ) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = selected_job_id or _job_id_from_memory_context(text) or _extract_job_id(visible_text) or _job_id_from_recent_history(history)
        if not job_id:
            return AgentRunResult(
                content="Hangi is icin poz bilgisini uygulayacagimi bulamadim. Bir job ID sec veya yaz.",
                fallback_reason="local_position_info_resolution",
            )
        poz_no = _extract_supplied_position_poz_no(visible_text, job_id=job_id)
        if not poz_no:
            return AgentRunResult(
                content=f"`{job_id}` icin kullanilacak poz numarasini okuyamadim. Ornek: `poz bilgisi \"api-1\" olarak alinabilir`.",
                fallback_reason="local_position_info_resolution",
            )
        result = _apply_supplied_position_info(self.paths, self.tools, job_id, poz_no, session_id=session_id)
        return AgentRunResult(
            content=_format_supplied_position_info_response(result),
            tool_results=[{"tool": "apply_supplied_position_info", "result": result}],
            fallback_reason="local_position_info_resolution",
        )

    def _handle_page_exclusion_request(
        self,
        text: str,
        history: list[dict[str, str]],
        *,
        session_id: str | None = None,
    ) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = selected_job_id or _job_id_from_memory_context(text) or _extract_job_id(visible_text) or _job_id_from_recent_history(history)
        if not job_id:
            return AgentRunResult(
                content="Hangi is icin sayfa atlama karari uygulayacagimi bulamadim. Bir job ID sec veya yaz.",
                fallback_reason="local_page_exclusion",
            )
        pages = _extract_page_numbers_for_exclusion(visible_text)
        if not pages:
            return AgentRunResult(
                content=f"`{job_id}` icin atlanacak sayfa numarasini okuyamadim. Ornek: `Sayfa No: 1 - baslik sayfasi, plaka yok, atlansin`.",
                fallback_reason="local_page_exclusion",
            )
        result = _apply_page_exclusion_decision(self.paths, self.tools, job_id, pages, visible_text, session_id=session_id)
        raw = _format_page_exclusion_response(result)
        synth = self._synthesize_query_with_gemini(raw, text, history, fallback_reason="local_page_exclusion")
        synth.tool_results = [{"tool": "apply_page_exclusion", "result": result}]
        return synth

    def _handle_mark_column_position_hint(
        self,
        text: str,
        history: list[dict[str, str]],
        *,
        session_id: str | None = None,
    ) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = selected_job_id or _job_id_from_memory_context(text) or _extract_job_id(visible_text) or _job_id_from_recent_history(history)
        if not job_id:
            return AgentRunResult(
                content="Hangi is icin Mark sutunu poz okuma kuralini uygulayacagimi bulamadim. Bir job ID sec veya yaz.",
                fallback_reason="local_mark_column_position_hint",
            )
        result = _apply_mark_column_position_hint(self.paths, self.tools, job_id, visible_text, session_id=session_id)
        return AgentRunResult(
            content=_format_mark_column_position_hint_response(result),
            tool_results=[{"tool": "apply_mark_column_position_hint", "result": result}],
            fallback_reason="local_mark_column_position_hint",
        )

    def _handle_job_completion_continue(self, text: str, history: list[dict[str, str]]) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = _extract_job_id(visible_text) or selected_job_id or _job_id_from_memory_context(text) or _job_id_from_recent_history(history)
        if not job_id:
            return AgentRunResult(
                content="Hangi isi tamamlamak icin devam edecegimizi bulamadim. Bir job ID sec veya yaz.",
                fallback_reason="local_job_completion_plan",
            )
        return AgentRunResult(
            content=_format_job_completion_plan(self.paths, job_id),
            fallback_reason="local_job_completion_plan",
        )

    def _handle_job_completion_step(self, text: str, history: list[dict[str, str]]) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = _extract_job_id(visible_text) or selected_job_id or _job_id_from_memory_context(text) or _job_id_from_recent_history(history)
        step = _completion_step_number_for_request(text)
        if not job_id:
            return AgentRunResult(
                content="Hangi is icin bu adimi baslatacagimi bulamadim. Bir job ID sec veya yaz.",
                fallback_reason="local_job_completion_step",
            )
        return AgentRunResult(
            content=_format_job_completion_step(paths=self.paths, job_id=job_id, step=step),
            fallback_reason="local_job_completion_step",
        )

    def _handle_manual_review_detail_request(self, text: str, history: list[dict[str, str]]) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = _extract_job_id(visible_text) or selected_job_id or _job_id_from_memory_context(text) or _job_id_from_recent_history(history)
        if not job_id:
            return AgentRunResult(
                content="Hangi is icin manuel inceleme notlarini gosterecegimi bulamadim. Bir job ID sec veya yaz.",
                fallback_reason="local_manual_review_details",
            )
        raw = _format_manual_review_detail_response(self.paths, job_id, visible_text)
        return self._synthesize_query_with_gemini(raw, text, history, fallback_reason="local_manual_review_details")

    def _handle_deep_output_inspection(self, text: str, history: list[dict[str, str]]) -> AgentRunResult:
        """'notlar giderildi mi', 'tüm çıktıları incele' gibi dosya-düzeyinde audit soruları."""
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = _extract_job_id(visible_text) or selected_job_id or _job_id_from_memory_context(text) or _job_id_from_recent_history(history)
        if not job_id:
            return AgentRunResult(
                content="Hangi is icin cikti incelemesi yapacagimi bulamadim. Bir job ID sec veya yaz.",
                fallback_reason="local_deep_output_inspection",
            )
        raw = _format_deep_output_inspection(self.paths, job_id)
        return self._synthesize_query_with_gemini(raw, text, history, fallback_reason="local_deep_output_inspection")

    def _handle_job_learning_request(self, text: str, history: list[dict[str, str]]) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = _extract_job_id(visible_text) or selected_job_id or _job_id_from_memory_context(text) or _job_id_from_recent_history(history)
        if not job_id:
            return AgentRunResult(
                content="Hangi isin ogrenimlerini ozetleyecegimi bulamadim. Bir job ID sec veya yaz.",
                fallback_reason="local_job_learning",
            )
        raw = _format_job_learning_summary(self.paths, job_id)
        return self._synthesize_query_with_gemini(raw, text, history, fallback_reason="local_job_learning")

    def _handle_manager_trigger(
        self,
        *,
        trigger: dict[str, Any] | None,
        session_id: str | None,
        selected_job_id: str | None,
    ) -> AgentRunResult | None:
        if not isinstance(trigger, dict):
            return None
        if str(trigger.get("type") or "").strip() != "candidate_validation_blocked":
            return None
        job_id = str(trigger.get("job_id") or selected_job_id or "").strip()
        if not job_id:
            return AgentRunResult(
                content="Aday onayi blokajini yonetmek icin once ilgili isi secmem gerekiyor.",
                fallback_reason="local_corner_reliefs_trigger",
            )
        output_dir = self.paths.jobs_output_root / job_id
        codex_data = _read_json_file(output_dir / "codex_candidates.json") or {}
        all_candidates: list[Any] = codex_data.get("candidates", []) if isinstance(codex_data, dict) else []
        pending = _candidates_needing_corner_reliefs(all_candidates)
        if not pending:
            return AgentRunResult(
                content=f"`{job_id}` isinde kose bosaltma gerektiren eksik aday bulunamadi.",
                fallback_reason="local_corner_reliefs_trigger",
            )
        prompt = _format_corner_reliefs_question(job_id, pending)
        if session_id:
            get_guided_flow_store(self.paths.workspace_root).upsert(
                corner_relief_state(
                    session_id=session_id,
                    job_id=job_id,
                    pending_candidate_rows=pending,
                    prompt=prompt,
                )
            )
        return AgentRunResult(
            content=prompt,
            fallback_reason="local_corner_reliefs_trigger",
        )

    def _open_corner_relief_flow_state(
        self,
        *,
        session_id: str | None,
        selected_job_id: str | None,
        visible_text: str,
    ) -> GuidedFlowState | None:
        if not session_id:
            return None
        job_id = selected_job_id or _extract_job_id(visible_text)
        return get_guided_flow_store(self.paths.workspace_root).get_open(
            session_id=session_id,
            job_id=job_id,
            flow_type=FLOW_CORNER_RELIEF,
        )

    def _handle_corner_reliefs_conversation(
        self,
        text: str,
        history: list[dict[str, str]],
        *,
        session_id: str | None = None,
        flow_state: GuidedFlowState | None = None,
    ) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = (
            (flow_state.job_id if flow_state is not None else None)
            or _extract_job_id(visible_text)
            or selected_job_id
            or _job_id_from_memory_context(text)
            or _job_id_from_recent_history(history)
        )
        if not job_id:
            return AgentRunResult(
                content="Hangi is icin kose bosaltma bilgisi duzenlemek istedigini bulamadim. Lutfen bir job ID sec veya yaz.",
                fallback_reason="local_corner_reliefs",
            )
        output_dir = self.paths.jobs_output_root / job_id
        codex_data = _read_json_file(output_dir / "codex_candidates.json") or {}
        all_candidates: list[Any] = codex_data.get("candidates", []) if isinstance(codex_data, dict) else []
        pending = _candidates_needing_corner_reliefs(all_candidates)
        if flow_state is not None and pending:
            pending = _merge_flow_pending(flow_state.pending_candidate_rows, pending)
        elif flow_state is not None and not pending:
            get_guided_flow_store(self.paths.workspace_root).resolve(flow_state)

        # If previous assistant turn was the corner_reliefs question, treat this turn as the answer
        if (flow_state is not None or _last_message_was_corner_reliefs_question(history)) and pending:
            if flow_state is not None and _looks_like_guided_flow_cancel(visible_text):
                get_guided_flow_store(self.paths.workspace_root).cancel(flow_state)
                return AgentRunResult(
                    content=(
                        f"`{job_id}` icin kose bosaltma netlestirme akisini kapattim. "
                        "Adaylar mevcut haliyle onaya alinmayacak; yeni teknik talimat geldiginde tekrar acabilirim."
                    ),
                    fallback_reason="local_corner_reliefs_cancelled",
                )
            if _looks_like_corner_relief_meta_question(visible_text):
                detail = _format_corner_relief_missing_detail_response(job_id, pending)
                if flow_state is not None:
                    get_guided_flow_store(self.paths.workspace_root).replace_pending(
                        flow_state,
                        pending,
                        last_manager_prompt=detail,
                        suggested_resolution_text=_corner_relief_suggestion_from_prompt(detail),
                    )
                return AgentRunResult(
                    content=detail,
                    fallback_reason="local_corner_reliefs",
                )
            if _looks_like_corner_relief_confirmation(visible_text):
                suggested_text = (
                    flow_state.suggested_resolution_text
                    if flow_state is not None and flow_state.suggested_resolution_text.strip()
                    else _corner_relief_suggestion_from_history(history)
                )
                suggested_reliefs = _parse_corner_reliefs_by_pending_candidate(suggested_text, pending) if suggested_text else {}
                if suggested_reliefs:
                    return self._apply_corner_relief_updates(
                        job_id=job_id,
                        output_dir=output_dir,
                        codex_data=codex_data,
                        all_candidates=all_candidates,
                        reliefs_by_row=suggested_reliefs,
                        flow_state=flow_state,
                        session_id=session_id,
                    )
            reliefs_by_row = _parse_corner_reliefs_by_pending_candidate(visible_text, pending)
            if not reliefs_by_row:
                corner_reliefs = _parse_corner_reliefs_from_text(visible_text)
                if corner_reliefs:
                    pending_indices = {item["_row_index"] for item in pending}
                    reliefs_by_row = {index: corner_reliefs for index in pending_indices}
            if reliefs_by_row:
                return self._apply_corner_relief_updates(
                    job_id=job_id,
                    output_dir=output_dir,
                    codex_data=codex_data,
                    all_candidates=all_candidates,
                    reliefs_by_row=reliefs_by_row,
                    flow_state=flow_state,
                    session_id=session_id,
                )
            # "gorsel analiz yap/tekrar" → corner relief loop'tan cik, gorsel analizi yeniden calistir
            if _looks_like_missing_candidate_extraction_request(text):
                return self._handle_missing_candidate_extraction(text, history)
            # Kullanici "poligon olarak ciz" dediyse — kose bosaltma yerine polygon kontur isle
            if _looks_like_polygon_draw_instruction(visible_text):
                target_pozs = _extract_all_poz_nos_from_text(visible_text)
                if target_pozs:
                    target_pending = [p for p in pending if str(p.get("poz_no", "")) in target_pozs]
                    if not target_pending:
                        target_pending = pending
                else:
                    target_pending = pending
                polygon_reliefs: dict[int, list[dict]] = {
                    item["_row_index"]: [{"type": "polygon_contour"}]
                    for item in target_pending
                }
                polygon_pozs = [str(p.get("poz_no", p["_row_index"])) for p in target_pending]
                apply_result = self._apply_corner_relief_updates(
                    job_id=job_id,
                    output_dir=output_dir,
                    codex_data=codex_data,
                    all_candidates=all_candidates,
                    reliefs_by_row=polygon_reliefs,
                    flow_state=flow_state,
                    session_id=session_id,
                )
                confirm = (
                    f"`{job_id}` — poz {'ve '.join(polygon_pozs)} poligon kontur olarak isaretlen"
                    "di. Kose bosaltma yerine gorsel analizden elde edilen koordinatlar kullanilacak. "
                    "Kalan adaylarda baska eksik varsa bildirin."
                )
                return AgentRunResult(
                    content=confirm,
                    tool_results=apply_result.tool_results,
                    fallback_reason="local_corner_reliefs_polygon",
                )
            # Couldn't parse corner_reliefs — ask again with clarification
            detail = _format_corner_relief_ambiguity_response(job_id, pending)
            if flow_state is not None:
                get_guided_flow_store(self.paths.workspace_root).replace_pending(
                    flow_state,
                    pending,
                    last_manager_prompt=detail,
                )
            return AgentRunResult(content=detail, fallback_reason="local_corner_reliefs")

        if not pending:
            return AgentRunResult(
                content=f"`{job_id}` isinde kose bosaltma gerektiren eksik aday bulunamadi.",
                fallback_reason="local_corner_reliefs",
            )
        prompt = _format_corner_reliefs_question(job_id, pending)
        if session_id:
            get_guided_flow_store(self.paths.workspace_root).upsert(
                corner_relief_state(
                    session_id=session_id,
                    job_id=job_id,
                    pending_candidate_rows=pending,
                    prompt=prompt,
                )
            )
        return AgentRunResult(content=prompt, fallback_reason="local_corner_reliefs")

    def _apply_corner_relief_updates(
        self,
        *,
        job_id: str,
        output_dir: Path,
        codex_data: dict[str, Any],
        all_candidates: list[Any],
        reliefs_by_row: dict[int, list[dict[str, Any]]],
        flow_state: GuidedFlowState | None = None,
        session_id: str | None = None,
    ) -> AgentRunResult:
        updated_rows = _apply_corner_reliefs_to_candidates(all_candidates, reliefs_by_row)
        _write_codex_candidates(output_dir, codex_data, updated_rows)
        remaining = _candidates_needing_corner_reliefs(updated_rows)
        if remaining:
            parsed_pozs = _pozs_for_relief_rows(updated_rows, reliefs_by_row)
            next_question = _format_corner_reliefs_question(job_id, remaining)
            if flow_state is not None:
                get_guided_flow_store(self.paths.workspace_root).replace_pending(
                    flow_state,
                    remaining,
                    last_manager_prompt=next_question,
                    suggested_resolution_text="",
                )
            return AgentRunResult(
                content=(
                    f"Kose bosaltma bilgisini kaydettim: {', '.join(parsed_pozs)}.\n\n"
                    f"{next_question}"
                ),
                fallback_reason="local_corner_reliefs",
            )
        if flow_state is not None:
            get_guided_flow_store(self.paths.workspace_root).resolve(flow_state)
        notification_session_id = session_id or (flow_state.session_id if flow_state is not None else None)
        result = self.tools.run(
            "approve_candidates",
            {"job_id": job_id, "rows": updated_rows, "session_id": notification_session_id or ""},
        )
        if result.get("ok"):
            return AgentRunResult(
                content=(
                    f"Kose bosaltma bilgilerini ekledim ve {len(updated_rows)} aday onaylandi.\n"
                    f"{result.get('message') or f'`{job_id}` isi otomatik kapanis zincirinde tamamlandi.'}"
                ),
                tool_results=[{"tool": "approve_candidates", "result": result}],
            )
        return AgentRunResult(
            content=f"Onay sirasinda hata: {result.get('error', 'bilinmeyen hata')}",
            tool_results=[{"tool": "approve_candidates", "result": result}],
            fallback_reason="approve_failed",
        )


_PROACTIVE_AUTHORITY_BLOCK = (
    "## Proaktif Yetki ve Skill Secimi (KRITIK)\n"
    "Sen bir teknik ofis mudurususn. Asagidaki kararlari kullanici istemeden, durumu degerlendirerek kendin alirsin:\n"
    "- **Gorsel analiz**: Sayfa kapsami eksikse veya `plate_geometry_not_found` varsa, kullaniciya sormadan gorsel pipeline'i devreye al.\n"
    "- **QC tetiklemesi**: DXF/NC1 uretimi bittiyse, kullanici sormadan QC ozetini hazirla ve paylas.\n"
    "- **Partlist**: QC `ok=true` ise, kullanici sormadan partlist uretim adimini baslat.\n"
    "- **Skill secimi**: Hangi skill'in uygulanacagini kendin karar ver; kullanici skill adi vermek zorunda degil.\n"
    "  - Is dagitimi gereken durumda IS_DAGITIMI skill'ini uygula.\n"
    "  - QC kontrolu gereken durumda CIZIM_NC_KALITE_KONTROLU skill'ini uygula.\n"
    "  - Ogrenme firsati gorunde OGRENME_VE_HAFIZA_YONETIMI skill'ini devreye al.\n"
    "- **Karari raporla**: Hangi skill'i neden uyguladigini kisa acikla, sonucu ver. Onay bekleme.\n"
    "- **Belirsiz durumda**: Eksik bilgiyi sor ama sor-cevap al-uygula dongusunde kal; pasif kalma.\n"
)


def _gemini_manager_system_prompt(system_prompt: str) -> str:
    """Gemini system_instruction icin statik sistem promptu olusturur."""
    return (
        f"{system_prompt}\n\n"
        "## Runtime Kisitlari\n"
        "- Bu, teknik-ofis-muduru ile dogal sohbet oturumudur. Cevaplar mudur gibi karar odakli ve konuskan olsun.\n"
        "- Belirsiz islerde once hangi job/PDF/poz icin karar verilecegini sor. Veri uydurma.\n"
        "- Son cevabi yalnizca Turkce ver.\n\n"
        "## Aktif Ajanlar ve Rolleri\n"
        "- **teknik-ofis-muduru** (sen): Is dagitimi, QC karari, corner_reliefs toplama, partlist kapisi.\n"
        "- **autocad-uzman-1 / autocad-uzman-2**: PDF'den poz+geometri cikarma, DXF+NC1 uretimi.\n"
        "  Gorsel PDF analizinde Codex CLI bu agentlarin AGENT.md + skill dosyalarini kullanarak calisir.\n"
        "- **kalite-kontrol**: DXF+NC1 dogrulama, QC raporu uretme.\n"
        "- **dokuman-kontrol**: ERT Excel partlist, arsivleme.\n\n"
        "## Eksik Bilgi Toplama Kurali (KRITIK)\n"
        "- Bilgi eksikse (corner_reliefs, sayfa kapsama, tip/boyut): konusmal sorular sor, cevabi al, sisteme ilet.\n"
        "- ASLA kullaniciyi manuel JSON doldurmaya veya elle veri girmeye yonlendirme.\n"
        "- corner_reliefs eksikse: 'Hangi koseler bosaltilacak, ne tip (pah/round/cugul) ve kac mm?' diye sor.\n\n"
        "## Sohbet ve Inceleme Modu\n"
        "- Bu modda repo yazma yetkisi yoktur; genel sohbet, teknik karar, durum yorumu ve okuma agirlikli inceleme yap.\n"
        "- Kod veya dosya degisikligi gerekiyorsa kullanicidan bunu acikca istemesini bekle.\n\n"
        "## Gorsel PDF Analizinde Delegasyon Kurali (KRITIK)\n"
        "- Bir is gorsel PDF iceriyorsa ve sayfa kapsami eksikse (ornegin 26 sayfa var ama 3 poz uretilmis), ASLA kullanicidan elle poz bilgisi isteme.\n"
        "- Bu durumda mudur olarak gorsel analiz pipeline'ini (Codex gorsel cikarma) devreye alman gerekir.\n"
        "- Kullaniciya soyle: 'Eksik sayfalar icin gorsel analiz pipeline'ini baslatiyorum; Codex PDF sayfalarini tarayacak ve aday pozlari uretecek.' De.\n"
        "- Kullanicinin rolu: yalnizca sistem tarafindan uretilen adaylari gozden gecirip onaylamak. Elle veri girmesi beklenmez.\n\n"
        "## Ajan Taslak Olusturma Yetenegi\n"
        "- Kullanici yeni bir ajan olusturmak istediginde once ajan adi (title), misyon ve rol bilgisini ogren.\n"
        "- Title/isim net verilmemisse mutlaka sor: 'Bu yeni ajan icin bir isim ve kisa bir gorev tanimi verir misin?'\n"
        "- Yeterli bilgi toplandiktan sonra kullaniciya 'Tamam, isim ve misyon aldim; sistem taslagini otomatik olusturacak' de.\n"
        "- Taslaklar agents/_drafts/ altinda olusturulur. Aktif etmek icin CLI'da toffice agent approve <draft_id> gerekir.\n"
        "- Aktif etme yetkisi yalnizca proje yoneticisindedir.\n\n"
        f"{_PROACTIVE_AUTHORITY_BLOCK}"
        "\n\n## Yanıt Kalitesi\n"
        "- Bilgi şablonunu olduğu gibi tekrarlama; müdür gibi değerlendir ve yorum yap.\n"
        "- Kullanıcının sorusuna odaklan, fazladan bilgi verme.\n"
        "- Karar cümlesi açık ve eyleme geçilebilir olsun: 'Şunu yapmalısın:' değil, 'Şunu yapıyorum:'\n"
        "- Hata varsa: nedeni, hangi adım ve sonraki aksiyon — bu üçünü ver.\n"
        "- Onaylı spec, QC, partlist durumunu özetleyerek 'iş nerede?' sorusunu yanıtla.\n"
    )


def _manager_prompt(system_prompt: str, user_text: str, history: list[dict[str, str]], *, project_edit: bool = False) -> str:
    history_lines = []
    for item in history[-12:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            history_lines.append(f"{role}: {content.strip()}")
    history_block = "\n".join(history_lines) if history_lines else "(yok)"
    mode_block = (
        "## Proje Duzeltme Modu\n"
        "- Kullanici repo/kod/dosya uzerinde acik bir degisiklik istiyor. Bu modda proje dosyalarini incele, gerekli dosyalari duzenle ve makul testleri calistir.\n"
        "- Geniş repo taramalarini sinirli tut: dar `rg` sorgulari kullan, buyuk log/test dosyalarini komple dokme ve ciktiyi aksiyon alacak kadar oku.\n"
        "- Istek birden fazla sistemi kapsiyorsa once en kritik guvenlik/QC kapisini duzelt, sonra kisa takip listesi ver.\n"
        "- Degisiklikleri dar kapsamli tut; kullanicinin istemedigi yikici git veya dosya islemlerini yapma.\n"
        "- Son cevapta degisen dosyalari ve calistirilan dogrulamalari kisa ozetle.\n"
        "- Eger istek belirsiz veya riskliyse once netlestirme sorusu sor.\n"
        if project_edit
        else
        "## Sohbet ve Inceleme Modu\n"
        "- Bu modda repo yazma yetkisi yoktur; genel sohbet, teknik karar, durum yorumu ve okuma agirlikli inceleme yap.\n"
        "- Kod/dosya degisikligi gerekiyorsa kullanicidan bunu acikca istemesini bekle.\n"
    )
    return (
        f"{system_prompt}\n\n"
        "## Runtime Kisitlari\n"
        "- Bu, teknik-ofis-muduru ile dogal sohbet oturumudur. Cevaplar mekanik komut listesi gibi degil, mudur gibi karar odakli ve konuskan olsun.\n"
        "- Kullanici is veya ajan olusturma gibi eylem isterse, net niyeti tekrar et ve sistemin bunu guvenli tool/router uzerinden yapabilecegini soyle. Eger eylem zaten gerceklesmisse sonucu ozetle.\n"
        "- Belirsiz islerde once hangi job/PDF/poz icin karar verilecegini sor. Veri uydurma.\n"
        "- Son cevabi yalnizca Turkce ver.\n\n"
        f"{_PROACTIVE_AUTHORITY_BLOCK}\n"
        f"{mode_block}\n"
        f"## Onceki Konusma\n{history_block}\n\n"
        f"## Mudur Hafizasi\n{_manager_memory_context_block(user_text)}\n\n"
        f"## Kullanici Istegi\n{_visible_user_text(user_text)}\n"
    )


def _extract_job_id(text: str) -> str | None:
    match = re.search(r"\b[A-Za-z]+-[0-9][A-Za-z0-9_.-]*\b", text)
    return match.group(0) if match else None


def _should_route_locally(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if _looks_like_list_jobs_request(lower):
        return True
    if _looks_like_create_job_request(lower) and _extract_job_id(visible_text):
        return True
    selected_job_id = _selected_job_id_from_context(text)
    has_explicit_or_selected_job = bool(_extract_job_id(visible_text) or (selected_job_id and _looks_like_selected_job_reference(lower)))
    if has_explicit_or_selected_job and (_looks_like_run_request(lower) or "qc" in lower):
        return True
    if has_explicit_or_selected_job and "partlist" in lower:
        return True
    if _extract_numeric_reference(visible_text) and _looks_like_job_reference(lower) and _looks_like_run_request(lower):
        return True
    if "qc" in lower and _extract_poz_no(visible_text):
        return True
    if _looks_like_agent_creation_request(lower):
        return True
    return False


def _looks_like_lightweight_manager_chat(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text).strip(" .!?")
    if not lower:
        return False
    direct_phrases = {
        "merhaba",
        "selam",
        "selamlar",
        "hey",
        "alo",
        "burada misin",
        "orada misin",
        "hey orada misin",
        "hey oradamisin",
        "mudurum merhaba",
        "mudur merhaba",
        "nasilsin",
        "nasil misin",
        "iyi misin",
        "ne var ne yok",
        "naber",
        "ne haber",
        "gunaydin",
        "iyi gunler",
        "iyi aksamlar",
        "iyi geceler",
        "gorusuruz",
        "hosca kal",
        "sagol",
        "tesekkurler",
        "tamam anladim",
    }
    if lower in direct_phrases:
        return True
    return any(
        phrase in lower
        for phrase in (
            "ne yapabiliriz",
            "neler yapabiliriz",
            "ne yapabilirsin",
            "neler yapabilirsin",
            "ne is yapabiliriz",
            "ne isler yapabiliriz",
            "seninle ne is yapabilir",
            "seninle ne yapabilir",
            "nasil sohbet",
            "sohbet edebilir",
            "dogal sohbet",
            "orada misin",
            "burada misin",
        )
    )


def _format_lightweight_manager_response(job_ids: list[str]) -> str:
    if job_ids:
        jobs = ", ".join(f"`{job_id}`" for job_id in job_ids[:6])
        job_hint = f" Su an gordugum isler: {jobs}."
    else:
        job_hint = " Su an kayitli is gormuyorum; PDF yukleyerek yeni is acabiliriz."
    return (
        "Merhaba, buradayim. Teknik ofis muduru olarak PDF yukleme, pipeline calistirma, aday onayi, "
        "QC kontrolu, partlist kapisi, is hata notlari, yeni ajan taslaklari ve acik proje/kod duzeltme "
        "isteklerini yonetebilirim. Kod/dosya degisikligi acikca istendiginde Codex CLI'yi proje yazma modunda calistiririm."
        f"{job_hint} Dogrudan bir is icin `test-001 isini calistir`, karar icin de "
        "`bu isin durumunu ozetle` gibi yazabilirsin."
    )


def _looks_like_runtime_ready_request(text: str) -> bool:
    lower = _normalize_turkish(text)
    asks_ready = any(phrase in lower for phrase in ("hazir", "ayakta", "calisiyor", "durum", "status", "ready"))
    return asks_ready and any(word in lower for word in ("sistem", "runtime", "agent", "ajan", "ofis"))


def _format_runtime_ready_response() -> str:
    return (
        "Merhaba, Technical Office Runtime hazir. 2D dashboard, Codex CLI bridge ve deterministic DXF/NC1/QC pipeline ayni runtime'a bagli.\n"
        "Mudur normal sohbette read-only, acik proje/kod duzeltme isteginde workspace-write Codex CLI modunu kullanir.\n"
        "Dogrudan is calistirmak icin `run job test-001 autocad off` yazabilirsin; Codex durumunu kontrol etmek icin `toffice doctor` kullanilir."
    )


def _looks_like_job_restart_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower:
        return False
    restart_phrases = (
        "bastan baslamak",
        "bastan baslayalim",
        "bastan basla",
        "bastan baslat",
        "bastan baslayacagiz",
        "temiz baslat",
        "temiz basla",
        "sifirdan basla",
        "sifirdan baslat",
        "sifirdan baslayacagiz",
        "sifirla",
        "resetle",
        "reset at",
        "yeniden baslamak",
        "yeniden baslayalim",
        "yeniden basla",
        "yeniden baslat",
        "yeniden baslayacagiz",
    )
    if not any(phrase in lower for phrase in restart_phrases):
        return False
    return bool(_extract_job_id(visible_text) or _selected_job_id_from_context(text) or _looks_like_selected_job_reference(lower))


def _looks_like_job_status_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    has_job = bool(_extract_job_id(visible_text) or _selected_job_id_from_context(text))
    has_selected_job_phrase = any(phrase in lower for phrase in ("bu isin", "bu isi", "secili is", "secilen is"))
    if not has_job and not has_selected_job_phrase:
        return False
    phrases = (
        "ne durumdayiz",
        "ne durumdayız",
        "ne durum",
        "son durum",
        "durum nedir",
        "durumu nedir",
        "durumunu",
        "durumdayiz",
        "neredeyiz",
        "hangi asamada",
        "ne asamada",
        "ozetle",
        "ozet",
        # hata sorguları — soru kipinde veya pipeline bağlamında
        "hata nedir",
        "hata ne",
        "ne hatasi",
        "hatayi goster",
        "hata goster",
        "neden basarisiz",
        "neden calismadi",
        "neden hata",
        "basarisiz neden",
        "neden fail",
        "pipeline hata",
        "pipeline basarisiz",
        "hata var mi",
        "hata aldi",
        "ne hata verdi",
        "hata verdi mi",
    )
    return any(phrase in lower for phrase in phrases)


def _looks_like_manual_review_detail_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower:
        return False
    has_job = bool(
        _extract_job_id(visible_text)
        or _selected_job_id_from_context(text)
        or _job_id_from_memory_context(text)
        or any(phrase in lower for phrase in ("bu isin", "bu isi", "secili is", "secilen is"))
    )
    if not has_job:
        return False
    review_terms = (
        "manuel inceleme",
        "manual review",
        "mudur not",
        "mudur notu",
        "mudur notlari",
        "acik not",
        "blokaj",
        "hangi poz",
    )
    action_terms = (
        "nereden",
        "gorebilirim",
        "goster",
        "listele",
        "hangileri",
        "hangi",
        "nedir",
        "nelerdir",
        "neler",
        "gerektiren",
        "gereken",
        "nasil tamam",
        "nasil kapat",
        "nasil cozer",
        "tamamlayacagim",
        "tamamlayalim",
        "kapatacagim",
    )
    return any(term in lower for term in review_terms) and any(term in lower for term in action_terms)


def _looks_like_deep_output_inspection_request(text: str) -> bool:
    """'notlar giderildi mi', 'çıktıları incele', 'kalan ne var' gibi derin audit sorguları."""
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower:
        return False
    has_job = bool(
        _extract_job_id(visible_text)
        or _selected_job_id_from_context(text)
        or _job_id_from_memory_context(text)
        or any(phrase in lower for phrase in ("bu isin", "bu isi", "secili is", "secilen is"))
    )
    if not has_job:
        return False
    deep_phrases = (
        "giderilip giderilmedi",
        "giderildi mi",
        "giderilmis mi",
        "kapandi mi",
        "kapanmis mi",
        "gercekten kapandi",
        "gercekten tamam",
        "gercekten giderildi",
        "tum ciktilari incele",
        "tum proje cikti",
        "ciktilari incele",
        "ciktilara bak",
        "kalan ne var",
        "kalan isler",
        "kalan sorunlar",
        "eksik ne var",
        "ne kaldi",
        "ne eksik",
        "hepsi tamam mi",
        "hepsi giderildi mi",
        "notlar giderildi",
        "notlar kapandi",
    )
    return any(phrase in lower for phrase in deep_phrases)


def _looks_like_mark_column_position_hint_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower:
        return False
    has_job = bool(
        _extract_job_id(visible_text)
        or _selected_job_id_from_context(text)
        or _job_id_from_memory_context(text)
        or any(phrase in lower for phrase in ("bu isin", "bu isi", "secili is", "secilen is"))
    )
    if not has_job:
        return False
    return (
        "poz" in lower
        and "mark" in lower
        and any(term in lower for term in ("sutun", "sutn", "kolon", "tablo", "alt kisim", "alt kism"))
    )


def _looks_like_position_info_resolution_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower:
        return False
    has_job = bool(
        _extract_job_id(visible_text)
        or _selected_job_id_from_context(text)
        or _job_id_from_memory_context(text)
        or any(phrase in lower for phrase in ("bu isin", "bu isi", "secili is", "secilen is"))
    )
    if not has_job:
        return False
    poz_terms = (
        "poz bilgisi",
        "poz biligisi",
        "poz no",
        "poz numarasi",
        "poznosu",
        "poz no olarak",
        "poz olarak",
    )
    resolution_terms = (
        "olarak alinabilir",
        "olarak al",
        "olarak kullan",
        "alinabilir",
        "guncelle",
    )
    return any(term in lower for term in poz_terms) and any(term in lower for term in resolution_terms)


def _looks_like_page_exclusion_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower or "sayfa" not in lower:
        return False
    has_job = bool(
        _extract_job_id(visible_text)
        or _selected_job_id_from_context(text)
        or _job_id_from_memory_context(text)
        or any(phrase in lower for phrase in ("bu isin", "bu isi", "secili is", "secilen is"))
    )
    if not has_job:
        return False
    no_plate_terms = (
        "baslik sayfasi",
        "kapak sayfasi",
        "plaka yok",
        "plaka degil",
        "plaka icermiyor",
        "plaka bulunmuyor",
        "plaka cizimi yok",
        "plaka cizimi bulunmayan",
        "cizilecek bir plaka yok",
        "profil detayi",
        "profil detaylari",
        "profil sayfasi",
        "kesit sayfasi",
        "detay sayfasi",
        "plaka olarak cizilmesin",
        "plaka olarak islenmesin",
    )
    action_terms = (
        "atlanmali",
        "atlansin",
        "atla",
        "skip",
        "isleme alinmasin",
        "isleme alma",
        "cizilmesin",
        "cizilmeyecek",
        "alinmasin",
        "dahil etme",
        "dahil edilmesin",
        "gecilmeli",
        "gecilsin",
        "islemeyecek",
    )
    return any(term in lower for term in no_plate_terms) or any(term in lower for term in action_terms)


def _looks_like_apply_manager_decision_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower:
        return False
    # Yetenek/soru ifadeleri bu handler'a girmemeli
    capability_phrases = ("yapabilir misin", "yapabilir miyim", "yapabilirsen", "yapabilir mi", "nasil yapilir", "nasil yaparsın", "nasil yaparim")
    if any(p in lower for p in capability_phrases):
        return False
    apply_phrases = (
        "bu soylediklerini yap",
        "soylediklerini yap",
        "dediklerini yap",
        "bunlari yap",
        "bunu yap",
        "geregini yap",
        "gerekeni yap",
        "karari uygula",
        "bunu uygula",
        "bunlari uygula",
        "uygula",
        "qc blokla",
        "teslim kapisini kapat",
        "partlist kapisini kapat",
        "eksik olarak isaretle",
        "yarim olarak isaretle",
        "durumunu eksik",
        "durumunu yarim",
    )
    return any(phrase in lower for phrase in apply_phrases)


def _looks_like_job_completion_continue_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower:
        return False
    has_job = bool(_extract_job_id(visible_text) or _selected_job_id_from_context(text) or _looks_like_selected_job_reference(lower))
    if not has_job:
        return False
    continue_words = (
        "devam edelim",
        "devam et",
        "ilerleyelim",
        "tamamlamak icin",
        "tamamlamak uzere",
        "isi tamamlamak",
        "bu isi tamamla",
        "bu isi bitirelim",
        "tamamlayalim",
        "bitirelim",
    )
    return any(phrase in lower for phrase in continue_words)


def _looks_like_missing_candidate_extraction_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower:
        return False
    has_job = bool(
        _extract_job_id(visible_text)
        or _selected_job_id_from_context(text)
        or _job_id_from_memory_context(text)
        or _looks_like_selected_job_reference(lower)
    )
    if not has_job:
        return False
    # Açık delegasyon/görsel analiz isteği — pdf kelimesi olmadan da geçerli
    delegation_phrases = (
        "gorsel analiz",
        "gorsel analize",
        "agenta aktar",
        "ajana aktar",
        "agent'a aktar",
        "gorsel agent",
        "sayfalar analiz",
        "sayfa analiz",
        "sayfalari analiz",
        "eksik sayfalari analiz",
        "pdf analiz",
    )
    if any(phrase in lower for phrase in delegation_phrases):
        return True
    # PDF kelimesi varsa eski pattern'lar da geçerli
    if "pdf" in lower:
        extraction_phrases = (
            "pdf uzerinden sen kontrol et",
            "pdfyi sen kontrol et",
            "pdf'yi sen kontrol et",
            "pdfyi oku",
            "pdf'yi oku",
            "pdf oku",
            "pdf uzerinden kontrol",
            "pdf uzerinden incele",
            "eksik aday listesini cikar",
            "aday listesini cikar",
            "eksik pozlari cikar",
            "eksik sayfalari oku",
        )
        if any(phrase in lower for phrase in extraction_phrases):
            return True
        return any(word in lower for word in ("oku", "kontrol", "incele")) and any(word in lower for word in ("aday", "eksik", "sayfa", "poz"))
    return False


def _looks_like_poz_correction_action_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower:
        return False
    action_phrases = (
        "aksiyon zamani",
        "aksiyon zamanı",
        "bu pozu duzelt",
        "bu pozu düzelt",
        "pozu duzelt",
        "pozu düzelt",
        "duzeltelim",
        "düzeltelim",
        "tekrar uret",
        "tekrar üret",
        "yeniden uret",
        "yeniden üret",
        "regenerate",
    )
    if not any(phrase in lower for phrase in action_phrases):
        return False
    return bool(
        _extract_job_id(visible_text)
        or _selected_job_id_from_context(text)
        or _job_id_from_memory_context(text)
        or _looks_like_selected_job_reference(lower)
        or "poz" in lower
    )


def _looks_like_corner_reliefs_help_request(lower: str) -> bool:
    return any(p in lower for p in (
        "kose bosaltma",
        "corner relief",
        "corner_reliefs",
        "kose bilgisi",
        "onay hatasi",
        "kose tipi",
        "pah bilgisi",
        "kose ekle",
        "kose doldurmak",
    ))


_CORNER_RELIEFS_QUESTION_MARKER = "hangi koseler bosaltilacak"


def _last_message_was_corner_reliefs_question(history: list[dict]) -> bool:
    for item in reversed(history[-6:]):
        if item.get("role") == "assistant":
            content = _normalize_turkish(str(item.get("content") or ""))
            if (
                _CORNER_RELIEFS_QUESTION_MARKER in content
                or "kose bosaltma bilgisi gerektiriyor" in content
                or "kose bosaltma bilgisini su adaylar icin soruyorum" in content
                or "benden beklenen net format su" in content
                or "kose bilgisini anlayamadim" in content
                or "konum (alt-sol" in content
            ):
                return True
    return False


def _looks_like_corner_relief_meta_question(text: str) -> bool:
    lower = _normalize_turkish(text)
    asks_what = any(
        phrase in lower
        for phrase in (
            "hangi kose bilgisini",
            "hangi kose bilgilerini",
            "hangi bilgiyi anlamadin",
            "hangi bilgileri anlamadin",
            "neyi anlamadin",
            "ne anlamadin",
            "ne eksik",
            "hangi kisim eksik",
            "hangi detay eksik",
            "hangi parcalar icin soruyorsun",
            "hangi parca icin soruyorsun",
            "hangi pozlar icin soruyorsun",
            "hangi poz icin soruyorsun",
            "hangi adaylar icin soruyorsun",
            "hangi aday icin soruyorsun",
            "hangi parcalar icin",
            "hangi pozlar icin",
            "hangi adaylar icin",
        )
    )
    mentions_corner = any(word in lower for word in ("kose", "pah", "corner"))
    asks_scope = "hangi" in lower and any(word in lower for word in ("parca", "poz", "aday")) and "icin" in lower
    return asks_what or asks_scope or ("hangi" in lower and mentions_corner and "anlamadin" in lower)


def _looks_like_corner_relief_confirmation(text: str) -> bool:
    lower = _normalize_turkish(text)
    return any(
        phrase in lower
        for phrase in (
            "evet bu sekilde ilerle",
            "evet bu sekilde devam et",
            "bu sekilde ilerle",
            "bu sekilde devam et",
            "bu formatla ilerle",
            "aynen bu sekilde",
            "tamam bu sekilde",
            "dogru bu sekilde",
        )
    )


def _looks_like_guided_flow_cancel(text: str) -> bool:
    lower = _normalize_turkish(text)
    return any(
        phrase in lower
        for phrase in (
            "iptal et",
            "bu akisi kapat",
            "bu konuyu kapat",
            "simdilik gec",
            "sonra bakariz",
            "bosver",
            "vazgectim",
        )
    )


def _corner_relief_suggestion_from_history(history: list[dict[str, str]]) -> str:
    for item in reversed(history[-8:]):
        if item.get("role") != "assistant":
            continue
        content = str(item.get("content") or "")
        suggested = _corner_relief_suggestion_from_prompt(content)
        if suggested:
            return suggested
    return ""


def _corner_relief_suggestion_from_prompt(content: str) -> str:
    if "Benden beklenen net format su:" not in content:
        return ""
    suggestion_lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("- `") and stripped.endswith("`"):
            suggestion_lines.append(stripped[3:-1])
    return "\n".join(suggestion_lines)


def _looks_like_skill_promote_request(text: str) -> bool:
    normalized = _normalize_turkish(_visible_user_text(text).lower())
    # "proposal <id> onayla" — hem "proposal" hem "onayla" aynı ifadede olmalı
    has_proposal = "proposal" in normalized
    has_onayla = "onayla" in normalized or "terfiet" in normalized or "uygula" in normalized
    has_skill_onay = any(t in normalized for t in ("skill onayla", "memory onayla", "skill terfiet", "memory terfiet"))
    return (has_proposal and has_onayla) or has_skill_onay


def _looks_like_skill_update_request(text: str) -> bool:
    normalized = _normalize_turkish(_visible_user_text(text).lower())
    update_terms = (
        "memory kaydet",
        "hafizaya kaydet",
        "skill guncelle",
        "kurala ekle",
        "kurali guncelle",
        "uzman memorye",
        "uzman hafizasina",
        "autocad uzman memorye",
        "kalite kontrol memorye",
        "bunu kaydet",
        "bu kurali ekle",
        "memorye yaz",
        "agentin hafizasina",
        "hafizasina ekle",
        "memory ekle",
    )
    agent_terms = ("uzman", "autocad", "kalite kontrol", "dokuman kontrol")
    has_update = any(t in normalized for t in update_terms)
    has_agent = any(t in normalized for t in agent_terms)
    return has_update and has_agent


def _extract_target_agent_from_text(text: str) -> str:
    normalized = _normalize_turkish(text.lower())
    if "autocad uzman 2" in normalized or "uzman-2" in normalized or "uzman2" in normalized:
        return "autocad-uzman-2"
    if "autocad uzman 1" in normalized or "uzman-1" in normalized or "uzman1" in normalized or "autocad uzman" in normalized:
        return "autocad-uzman-1"
    if "kalite kontrol" in normalized or "qc" in normalized:
        return "kalite-kontrol"
    if "dokuman kontrol" in normalized or "dokuman" in normalized:
        return "dokuman-kontrol"
    return "autocad-uzman-1"


def _looks_like_polygon_draw_instruction(text: str) -> bool:
    normalized = _normalize_turkish(text.lower())
    polygon_terms = (
        "poligon olarak ciz",
        "poligon ciz",
        "poligon komutu",
        "poligon kontur",
        "polygon olarak ciz",
        "polygon ciz",
        "polygon kontur",
        "koordinatlar kullanilacak",
        "kose koordinat",
        "kontur ciz",
        "polygon",
        "poligon",
    )
    return any(term in normalized for term in polygon_terms)


def _extract_poz_no_from_text(text: str) -> str | None:
    """Metindeki ilk poz numarasını döndürür (3-6 haneli sayı)."""
    match = re.search(r"\b([0-9]{3,6})\b", text)
    return match.group(1) if match else None


def _extract_all_poz_nos_from_text(text: str) -> list[str]:
    """Metindeki tüm poz numaralarını (3-6 haneli sayılar) döndürür."""
    return re.findall(r"\b([0-9]{3,6})\b", text)


def _candidates_needing_corner_reliefs(candidates: list[Any]) -> list[dict]:
    strong_terms = ("pah", "chamfer", "poligon", "polygon", "polygonal", "chamfered", "side offset", "edge offset")
    result: list[dict] = []
    for i, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            continue
        reliefs = item.get("corner_reliefs")
        if isinstance(reliefs, list) and reliefs:
            continue  # dolu veya polygon_contour ile isaretlenmis
        contour_type = str(item.get("contour_type") or "").strip().lower()
        evidence = str(item.get("evidence") or item.get("reason") or "").strip().lower()
        combined = f"{contour_type} {evidence}"
        if any(term in combined for term in strong_terms):
            row = dict(item)
            row["_row_index"] = i
            result.append(row)
    return result


def _merge_flow_pending(stored_pending: list[dict[str, Any]], current_pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not stored_pending:
        return current_pending
    stored_indices = {
        int(item["_row_index"])
        for item in stored_pending
        if isinstance(item, dict) and isinstance(item.get("_row_index"), int)
    }
    if not stored_indices:
        return current_pending
    filtered = [
        item
        for item in current_pending
        if isinstance(item.get("_row_index"), int) and int(item["_row_index"]) in stored_indices
    ]
    return filtered or current_pending


def _parse_corner_reliefs_from_text(text: str) -> list[dict[str, Any]]:
    normalized = _normalize_turkish(text.lower())
    corner_map: dict[str, str] = {
        "alt-sol": "bottom_left",
        "alt sol": "bottom_left",
        "bottom_left": "bottom_left",
        "alt-sag": "bottom_right",
        "alt sag": "bottom_right",
        "bottom_right": "bottom_right",
        "ust-sol": "top_left",
        "ust sol": "top_left",
        "top_left": "top_left",
        "ust-sag": "top_right",
        "ust sag": "top_right",
        "top_right": "top_right",
    }
    all_corners = ["bottom_left", "bottom_right", "top_left", "top_right"]
    is_all = any(w in normalized for w in ("hepsi", "tumu", "her kose", "tum koseler", "4 kose"))
    radius_m = re.search(r"(\d+(?:\.\d+)?)\s*mm", normalized)
    radius: float
    if radius_m:
        radius = float(radius_m.group(1))
    else:
        num_m = re.search(r"\b(\d+(?:\.\d+)?)\b", normalized)
        radius = float(num_m.group(1)) if num_m else 5.0
    if any(w in normalized for w in ("round", "yuvarlak", "radius")):
        relief_type = "round"
    elif "cugul" in normalized:
        relief_type = "cugul"
    else:
        relief_type = "chamfer"
    corners = all_corners if is_all else []
    if not is_all:
        for key, corner_val in corner_map.items():
            if key in normalized and corner_val not in corners:
                corners.append(corner_val)
    if not corners:
        return []
    return [{"corner": c, "radius": radius, "relief_type": relief_type, "x_offset": radius, "y_offset": radius} for c in corners]


def _parse_corner_reliefs_by_pending_candidate(text: str, pending: list[dict]) -> dict[int, list[dict[str, Any]]]:
    normalized = _normalize_turkish(text)
    pending_by_poz = {str(item.get("poz_no")): item for item in pending if item.get("poz_no")}
    occurrences: list[tuple[int, str]] = []
    for poz_no in pending_by_poz:
        occurrences.extend((match.start(), poz_no) for match in re.finditer(rf"\b{re.escape(poz_no)}\b", normalized))
    occurrences.sort()
    if not occurrences and len(pending) == 1:
        parsed = _parse_corner_relief_segment(normalized)
        return {int(pending[0]["_row_index"]): parsed} if parsed else {}

    result: dict[int, list[dict[str, Any]]] = {}
    for idx, (start, poz_no) in enumerate(occurrences):
        end = occurrences[idx + 1][0] if idx + 1 < len(occurrences) else len(normalized)
        segment = normalized[start:end]
        parsed = _parse_corner_relief_segment(segment)
        if parsed:
            result[int(pending_by_poz[poz_no]["_row_index"])] = parsed
    return result


def _parse_corner_relief_segment(segment: str) -> list[dict[str, Any]]:
    corner_mentions = _corner_mentions(segment)
    if not corner_mentions:
        if any(word in segment for word in ("hepsi", "tumu", "her kose", "tum koseler", "4 kose")):
            corner_mentions = [(0, len(segment), ["bottom_left", "bottom_right", "top_left", "top_right"])]
        else:
            return []
    reliefs: list[dict[str, Any]] = []
    for index, (start, end, corners) in enumerate(corner_mentions):
        next_start = corner_mentions[index + 1][0] if index + 1 < len(corner_mentions) else len(segment)
        chunk = segment[end:next_start]
        x_offset, y_offset = _corner_size_from_text(chunk) or _corner_size_from_text(segment) or (5.0, 5.0)
        relief_type = _relief_type_from_text(chunk or segment)
        for corner in corners:
            reliefs.append(
                {
                    "corner": corner,
                    "radius": min(x_offset, y_offset),
                    "relief_type": relief_type,
                    "x_offset": x_offset,
                    "y_offset": y_offset,
                }
            )
    return _dedupe_corner_reliefs(reliefs)


def _corner_mentions(text: str) -> list[tuple[int, int, list[str]]]:
    patterns: list[tuple[str, list[str]]] = [
        (r"sol\s+ve\s+sag\s+ust", ["top_left", "top_right"]),
        (r"sag\s+ve\s+sol\s+ust", ["top_left", "top_right"]),
        (r"ust\s+sol\s+ve\s+sag", ["top_left", "top_right"]),
        (r"ust\s+sag\s+ve\s+sol", ["top_left", "top_right"]),
        (r"sol\s+ve\s+sag\s+alt", ["bottom_left", "bottom_right"]),
        (r"sag\s+ve\s+sol\s+alt", ["bottom_left", "bottom_right"]),
        (r"alt\s+sol\s+ve\s+sag", ["bottom_left", "bottom_right"]),
        (r"alt\s+sag\s+ve\s+sol", ["bottom_left", "bottom_right"]),
        (r"sol\s+ust", ["top_left"]),
        (r"ust\s+sol", ["top_left"]),
        (r"sag\s+ust", ["top_right"]),
        (r"ust\s+sag", ["top_right"]),
        (r"sol\s+alt", ["bottom_left"]),
        (r"alt\s+sol", ["bottom_left"]),
        (r"sag\s+alt", ["bottom_right"]),
        (r"alt\s+sag", ["bottom_right"]),
    ]
    mentions: list[tuple[int, int, list[str]]] = []
    spans: list[tuple[int, int]] = []
    for pattern, corners in patterns:
        for match in re.finditer(pattern, text):
            span = match.span()
            if any(max(span[0], used[0]) < min(span[1], used[1]) for used in spans):
                continue
            spans.append(span)
            mentions.append((span[0], span[1], corners))
    return sorted(mentions, key=lambda item: item[0])


def _corner_size_from_text(text: str) -> tuple[float, float] | None:
    pair = re.search(r"(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)", text)
    if pair:
        return float(pair.group(1).replace(",", ".")), float(pair.group(2).replace(",", "."))
    single = re.search(r"(\d+(?:[.,]\d+)?)\s*mm", text)
    if single:
        value = float(single.group(1).replace(",", "."))
        return value, value
    return None


def _relief_type_from_text(text: str) -> str:
    if any(word in text for word in ("round", "yuvarlak", "radius")):
        return "round"
    if "cugul" in text:
        return "cugul"
    return "chamfer"


def _dedupe_corner_reliefs(reliefs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for relief in reliefs:
        result[str(relief["corner"])] = relief
    return list(result.values())


def _apply_corner_reliefs_to_candidates(
    all_candidates: list[Any],
    reliefs_by_row_index: dict[int, list[dict]],
) -> list[dict]:
    result: list[dict] = []
    for i, candidate in enumerate(all_candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        row = {k: v for k, v in candidate.items() if not k.startswith("_")}
        if i in reliefs_by_row_index:
            row["corner_reliefs"] = reliefs_by_row_index[i]
        result.append(row)
    return result


def _write_codex_candidates(output_dir: Path, codex_data: dict[str, Any], candidates: list[dict]) -> None:
    updated = dict(codex_data) if isinstance(codex_data, dict) else {}
    updated["candidates"] = candidates
    (output_dir / "codex_candidates.json").write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")


def _pozs_for_relief_rows(candidates: list[dict], reliefs_by_row_index: dict[int, list[dict]]) -> list[str]:
    pozs: list[str] = []
    for index in sorted(reliefs_by_row_index):
        if 1 <= index <= len(candidates):
            pozs.append(str(candidates[index - 1].get("poz_no") or f"satir {index}"))
    return pozs


def _format_corner_reliefs_question(job_id: str, pending: list[dict]) -> str:
    lines = [f"`{job_id}` isinde su adaylar kose bosaltma bilgisi gerektiriyor ama bos:"]
    for item in pending:
        poz_no = item.get("poz_no") or "?"
        width = item.get("width")
        height = item.get("height")
        thickness = item.get("thickness")
        evidence = str(item.get("evidence") or item.get("contour_type") or "").strip()
        dims = f"{width}x{height}x{thickness}mm" if all(v is not None for v in [width, height, thickness]) else ""
        ev_short = f", {evidence[:60]}" if evidence else ""
        lines.append(f"- Satir {item.get('_row_index', '?')}: {poz_no} ({dims}{ev_short})")
    lines.append("")
    lines.append("Hangi koseler bosaltilacak ve ne tip/boyut?")
    lines.append("Ornek: 'hepsi pah 5mm' veya 'alt-sol ve alt-sag round 8mm, ust-sag chamfer 3mm'")
    return "\n".join(lines)


def _format_corner_relief_missing_detail_response(job_id: str, pending: list[dict]) -> str:
    lines = [
        f"`{job_id}` icin kose bosaltma bilgisini su adaylar icin soruyorum:",
    ]
    for item in pending:
        poz_no = item.get("poz_no") or "?"
        width = item.get("width")
        height = item.get("height")
        thickness = item.get("thickness")
        dims = f"{width}x{height}x{thickness}mm" if all(v is not None for v in [width, height, thickness]) else "olcu belirsiz"
        lines.append(f"- Poz {poz_no}: satir {item.get('_row_index', '?')}, {dims}")
    lines.extend(
        [
            "Eksik kalan kisim, her poz icin kose-konum ve pah olcusunun net eslesmesi.",
            "Benden beklenen net format su:",
            "- `206: sol ve sag ust pah 30x120`",
            "- `207: sol ust pah 30x120, sag alt pah 10x10`",
            "- `209: sol ve sag ust pah 60x120`",
        ]
    )
    return "\n".join(lines)


def _format_corner_relief_ambiguity_response(job_id: str, pending: list[dict]) -> str:
    pozs = ", ".join(str(item.get("poz_no") or f"satir {item.get('_row_index', '?')}") for item in pending)
    return (
        f"`{job_id}` icin kose bosaltma bilgisi hala net degil. Pozlar: {pozs}.\n"
        "Her poz icin konum (alt-sol, alt-sag, ust-sol, ust-sag veya hepsi), tip "
        "(pah/round/cugul) ve boyut (mm) birlikte gerekli.\n"
        "Ornek: `206: sol ve sag ust pah 30x120`"
    )


def _looks_like_job_completion_step_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower:
        return False
    has_job = bool(
        _extract_job_id(visible_text)
        or _selected_job_id_from_context(text)
        or _job_id_from_memory_context(text)
        or _looks_like_selected_job_reference(lower)
    )
    if not has_job or _completion_step_number_for_request(text) is None:
        return False
    action_words = (
        "adim",
        "asama",
        "sec",
        "secenek",
        "opsiyon",
        "devam",
        "basla",
        "baslayalim",
        "bslayalim",
        "gec",
        "gecelim",
        "ilerle",
        "ilerleyelim",
        "yap",
        "yapalim",
        "kontrol",
        "pdf",
    )
    return any(word in lower for word in action_words)


def _completion_step_number_for_request(text: str) -> int | None:
    visible_text = _visible_user_text(text)
    visible_lower = _normalize_turkish(visible_text)
    explicit_step = _completion_step_number(visible_text)
    if explicit_step is not None:
        return explicit_step
    if "pdf" in visible_lower and any(word in visible_lower for word in ("kontrol", "incele", "oku")):
        return 1
    short_continue = visible_lower.strip() in {"devam", "devam et", "ilerle"} or re.fullmatch(r"(devam|ilerle)\s+(et|edelim)", visible_lower.strip()) is not None
    if short_continue:
        full_lower = _normalize_turkish(text)
        if any(
            phrase in full_lower
            for phrase in (
                "pdf uzerinden",
                "pdf'yi sen kontrol",
                "pdfyi sen kontrol",
                "1. adimi baslattim",
                "eksik sayfa kapsami",
                "sayfa 4-26",
            )
        ):
            return 1
    return None


def _completion_step_number(text: str) -> int | None:
    lower = _normalize_turkish(text)
    for pattern in (
        r"\b([1-4])\s*[\.)]?\s*(?:adim|asama)",
        r"\b(?:adim|asama)\s*([1-4])\b",
        r"\b([1-4])\s*(?:numarali|nolu|no\s*lu)?\s*(?:sec\w*|opsiyon|tercih)",
        r"\b(?:sec\w*|opsiyon|tercih)\s*([1-4])\b",
    ):
        match = re.search(pattern, lower)
        if match:
            return int(match.group(1))
    word_steps = {
        "ilk": 1,
        "birinci": 1,
        "ikinci": 2,
        "ucuncu": 3,
        "dorduncu": 4,
    }
    for word, step in word_steps.items():
        if word in lower and ("adim" in lower or "asama" in lower):
            return step
    if re.search(r"^\s*1\s*[\.)]", lower) and any(word in lower for word in ("basla", "baslayalim", "bslayalim", "gec", "yap")):
        return 1
    return None


def _looks_like_confirmed_job_reset(lower: str) -> bool:
    return any(
        phrase in lower
        for phrase in (
            "temiz baslat",
            "temiz basla",
            "sifirla",
            "resetle",
            "reset at",
        )
    )


def _build_live_job_context(paths: "RuntimePaths", job_id: str) -> str:
    """Seçili işin anlık durumunu Gemini system prompt'una enjekte etmek için okur."""
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return ""
    lines = [f"## Seçili İş: {job_id}"]
    fsm = _read_json_file(output_dir / "fsm_state.json") or {}
    lines.append(f"- FSM: {fsm.get('state', 'unknown')}")
    meta = _read_json_file(job_dir / "job.json") or {}
    if meta.get("project_name"):
        lines.append(f"- Proje: {meta['project_name']}")
    summary = _read_json_file(output_dir / "job_summary.json") or {}
    if isinstance(summary, dict):
        ok = summary.get("ok")
        produced = len(summary.get("produced") or [])
        manual = len(summary.get("manual_reviews") or [])
        lines.append(f"- Pipeline ok={str(ok).lower()}, üretilen={produced}, manuel_inceleme={manual}")
    codex = _read_json_file(output_dir / "codex_candidates.json") or {}
    candidates = codex.get("candidates") if isinstance(codex, dict) else None
    if isinstance(candidates, list):
        lines.append(f"- Codex aday: {len(candidates)}")
    lines.append(f"- Onaylı spec: {'var' if (job_dir / 'approved_plate_specs.json').exists() else 'yok'}")
    failure_details = _read_job_failure_details(output_dir, job_dir)
    if failure_details:
        lines.append("- Son hata:")
        lines.extend(f"  {d}" for d in failure_details[:3])
    open_notes = _actionable_open_manager_issue_notes(paths, job_id)
    if open_notes:
        lines.append(f"- Açık müdür notu: {len(open_notes)}")
    return "\n".join(lines)


def _try_patch_approved_spec_corner_reliefs(job_dir: Path) -> str | None:
    """approved_plate_specs.json'da geçersiz corner_relief tiplerini normalize eder.

    Döndürür: değişiklik özeti string (örn. "3 satırda 2 kayıt temizlendi") veya
    patch gerekmiyorsa None (dosya yok, zaten temiz).
    """
    import json as _json

    _VALID_RELIEF_TYPES = {"round", "cugul", "chamfer", "pah"}
    _VALID_CORNERS = {"bottom_left", "bottom_right", "top_left", "top_right"}

    def _norm(v: str) -> str:
        n = v.strip().lower()
        if n in {"pah", "bevel", "beveled", "chamfered"}:
            return "chamfer"
        if n in {"round_relief", "rounded", "radius"}:
            return "round"
        return n

    def _norm_corner(v: str) -> str:
        # Codex bazen _inner/_outer/_mid suffix üretiyor — base köşeye normalize et
        n = v.strip().lower()
        for base in ("bottom_left", "bottom_right", "top_left", "top_right"):
            if n == base or n.startswith(base + "_"):
                return base
        # Kısa takma adlar
        _aliases = {
            "bl": "bottom_left", "br": "bottom_right",
            "tl": "top_left", "tr": "top_right",
            "sol_alt": "bottom_left", "sag_alt": "bottom_right",
            "sol_ust": "top_left", "sag_ust": "top_right",
        }
        return _aliases.get(n, n)

    spec_path = job_dir / "approved_plate_specs.json"
    if not spec_path.exists():
        return None

    try:
        data = _json.loads(spec_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    rows = data.get("plates", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None

    total_removed = 0
    total_fixed = 0
    changed_rows = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        reliefs = row.get("corner_reliefs")
        if not isinstance(reliefs, list):
            continue
        clean: list[dict] = []
        seen_corners: set[str] = set()
        for r in reliefs:
            if not isinstance(r, dict):
                continue
            rtype = str(r.get("relief_type") or r.get("type") or "")
            normalized = _norm(rtype)
            # polygon_contour sentinel veya corner alanı yok → at
            if normalized == "polygon_contour" or not r.get("corner"):
                total_removed += 1
                continue
            # Köşe adını normalize et
            raw_corner = str(r.get("corner", ""))
            normed_corner = _norm_corner(raw_corner)
            if normed_corner not in _VALID_CORNERS:
                # Tanımsız köşe → kaydı kaldır, validation crash'i önle
                total_removed += 1
                continue
            if normed_corner in seen_corners:
                # Aynı köşe tekrarı → at (duplicate corner hatasını önle)
                total_removed += 1
                continue
            seen_corners.add(normed_corner)
            if normed_corner != raw_corner:
                r["corner"] = normed_corner
                total_fixed += 1
            # Geçersiz tip → düzelt
            if normalized not in _VALID_RELIEF_TYPES:
                r["relief_type"] = "round"
                total_fixed += 1
            elif normalized != rtype.strip().lower():
                r["relief_type"] = normalized
                total_fixed += 1
            if "type" in r and "relief_type" not in r:
                r["relief_type"] = normalized
            clean.append(r)
        if len(clean) != len(reliefs):
            row["corner_reliefs"] = clean
            changed_rows += 1
        elif total_fixed > 0:
            row["corner_reliefs"] = clean
            changed_rows += 1

    if total_removed == 0 and total_fixed == 0:
        return None

    if isinstance(data, dict):
        data["plates"] = rows
    spec_path.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    parts = []
    if total_removed:
        parts.append(f"{total_removed} gecersiz/sentinel kayit kaldirildi")
    if total_fixed:
        parts.append(f"{total_fixed} tip/kose normalize edildi")
    return f"{changed_rows} satirda: {', '.join(parts)}"


def _format_job_restart_plan(paths: RuntimePaths, job_id: str) -> str:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return f"`{job_id}` isi bulunamadi. Is listesinden dogru job ID'yi sec veya yaz."
    pdfs = sorted(path.name for path in job_dir.glob("*.pdf"))
    approved_exists = (job_dir / "approved_plate_specs.json").exists()
    summary_exists = (output_dir / "job_summary.json").exists()
    outputs_exist = output_dir.exists() and any(output_dir.iterdir())
    lines = [
        f"`{job_id}` icin bastan baslama Codex gerektirmez; bu yerel job yonetimi akisi.",
        f"PDF girdileri korunacak: {', '.join(pdfs) if pdfs else 'PDF bulunamadi'}.",
    ]
    if approved_exists:
        lines.append("Mevcut `approved_plate_specs.json` var; bu dosya temizlenmeden pipeline gercekten bastan baslamaz.")
    if summary_exists or outputs_exist:
        lines.append("Mevcut cikti/ozet klasoru var; temiz baslangicta bunlari arsivlemek gerekir.")
    lines.extend(
        [
            "Guvenli temiz baslangic icin eski ciktilari ve onayli spec dosyasini arsivler, isi `uploaded` durumuna alirim.",
            f"Uygulamak icin net komut yaz: `{job_id} temiz baslat`.",
        ]
    )
    return "\n".join(lines)


def _format_job_status_response(paths: RuntimePaths, job_id: str) -> str:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return f"`{job_id}` isi bulunamadi. Is listesinden dogru job ID'yi sec veya yaz."

    metadata = _read_json_file(job_dir / "job.json") or {}
    summary = _read_json_file(output_dir / "job_summary.json") or {}
    diagnostics = _read_json_file(output_dir / "pdf_diagnostics.json") or {}
    codex_candidates = _read_json_file(output_dir / "codex_candidates.json") or {}
    fsm = _read_json_file(output_dir / "fsm_state.json") or {}
    fsm_state = fsm.get("state") if isinstance(fsm, dict) else None
    pdfs = diagnostics.get("pdfs") if isinstance(diagnostics, dict) else None
    pdf_count = len(pdfs) if isinstance(pdfs, list) else len(list(job_dir.glob("*.pdf")))
    page_count = None
    classifications: list[str] = []
    if isinstance(pdfs, list):
        page_values = [item.get("page_count") for item in pdfs if isinstance(item, dict) and isinstance(item.get("page_count"), int)]
        page_count = sum(page_values) if page_values else None
        classifications = sorted({str(item.get("classification")) for item in pdfs if isinstance(item, dict) and item.get("classification")})
    produced = summary.get("produced") if isinstance(summary, dict) else None
    manual_reviews = summary.get("manual_reviews") if isinstance(summary, dict) else None
    candidates = codex_candidates.get("candidates") if isinstance(codex_candidates, dict) else None
    approved_exists = (job_dir / "approved_plate_specs.json").exists()

    produced_count = len(produced) if isinstance(produced, list) else 0
    manual_review_count = len(manual_reviews) if isinstance(manual_reviews, list) else 0
    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    ok_value = summary.get("ok") if isinstance(summary, dict) and "ok" in summary else "bilinmiyor"
    project_name = metadata.get("project_name") if isinstance(metadata, dict) else None
    open_notes = 0 if ok_value is True else len(_actionable_open_manager_issue_notes(paths, job_id))

    lines = [
        f"`{job_id}` durum ozeti:",
        f"- Proje: {project_name or job_id}",
        f"- FSM: {fsm_state or 'uploaded'}",
        f"- Job sonucu: ok={str(ok_value).lower() if isinstance(ok_value, bool) else ok_value}",
        f"- PDF: {pdf_count} dosya" + (f", toplam {page_count} sayfa" if page_count is not None else ""),
        f"- Siniflandirma: {', '.join(classifications) if classifications else 'bilinmiyor'}",
        f"- Codex adaylari: {candidate_count}",
        f"- Uretilen poz: {produced_count}",
        f"- Manuel inceleme: {manual_review_count}",
        f"- Onayli spec: {'var' if approved_exists else 'yok'}",
    ]
    if open_notes:
        lines.append(f"- Acik mudur notu: {open_notes}")

    # Failed job → events.jsonl ve approved_plate_specs'ten gerçek hata detaylarını oku
    if fsm_state == "failed":
        error_details = _read_job_failure_details(output_dir, job_dir)
        if error_details:
            lines.append("")
            lines.append("Hata detaylari:")
            lines.extend(f"  {line}" for line in error_details)
        lines.append("")
        lines.append(
            "Aksiyon secenekleri:\n"
            "  - `resetle`: Onaylanmis spec'teki gecersiz corner_relief tiplerini duzelt ve pipeline'i yeniden baslat.\n"
            "  - `approved_plate_specs.json` silinmis is icin: adaylari yeniden onayla.\n"
            "  - Tam sifirlama gerekiyorsa: `temiz baslat` veya `sifirdan baslat` yaz."
        )
    elif isinstance(page_count, int) and page_count > produced_count and any(item in {"visual_text_required", "text_layer_unreadable"} for item in classifications):
        lines.append(
            "Karar: Bu is tamamlanmis kabul edilmemeli. Gorsel PDF sayfa kapsami uretilen/adaya gore eksik; "
            "eksik sayfalar ve acik geometri notlari kapanmadan partlist/teslim kapisi acilmamali."
        )
    elif fsm_state == "uploaded":
        lines.append("Karar: Is baslangic durumunda. Pipeline yeniden calistirilabilir.")
    elif manual_review_count:
        lines.append("Karar: Manuel inceleme kapanmadan teslim/partlist acilmamali.")
    else:
        lines.append("Karar: Mevcut ozet dosyalarina gore ek blokaj gorunmuyor; yine de QC ve aday kapsamindan emin olmak gerekir.")
    return "\n".join(lines)


def _read_job_failure_details(output_dir: Path, job_dir: Path) -> list[str]:
    """Failed job için events.jsonl ve approved_plate_specs.json'dan gerçek hata nedenini okur."""
    import json as _json

    details: list[str] = []

    # 1. approved_plate_specs.json'da geçersiz corner_reliefs var mı?
    spec_path = job_dir / "approved_plate_specs.json"
    if spec_path.exists():
        try:
            data = _json.loads(spec_path.read_text(encoding="utf-8"))
            rows = data.get("plates", data) if isinstance(data, dict) else data
            _VALID = {"round", "cugul", "chamfer", "pah"}
            bad_rows: list[str] = []
            for i, row in enumerate(rows if isinstance(rows, list) else [], start=1):
                if not isinstance(row, dict):
                    continue
                for j, relief in enumerate(row.get("corner_reliefs") or [], start=1):
                    if not isinstance(relief, dict):
                        continue
                    rtype = str(relief.get("relief_type") or relief.get("type") or "")
                    normalized = rtype.strip().lower()
                    if normalized not in _VALID and normalized not in ("", "polygon_contour"):
                        bad_rows.append(
                            f"  Satir {i} ({row.get('poz_no', '?')}): corner_relief {j} gecersiz tip={rtype!r}"
                        )
            if bad_rows:
                details.append("ApprovedSpecValidationError — onaylanmis speclerde gecersiz corner_relief tipleri:")
                details.extend(bad_rows)
                details.append("  → Duzeltmek icin: 'resetle' yaz; sistem tipleri normalize edip pipeline'i yeniden baslatir.")
        except Exception:
            pass

    # 2. events.jsonl'daki son failure / error olayını oku
    events_path = output_dir / "events.jsonl"
    if events_path.exists() and not details:
        try:
            lines_raw = events_path.read_text(encoding="utf-8").splitlines()
            for raw in reversed(lines_raw):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    evt = _json.loads(raw)
                except Exception:
                    continue
                event_type = str(evt.get("type") or evt.get("event") or "")
                payload = evt.get("payload") or {}
                if event_type in ("failed", "job_blocked", "production_failed", "qc_failed") or (
                    isinstance(payload, dict) and payload.get("ok") is False
                ):
                    reason = payload.get("reason") or payload.get("error") or evt.get("reason") or event_type
                    details.append(f"Son hata olayi ({event_type}): {reason}")
                    break
        except Exception:
            pass

    # 3. job_summary.json'daki hata bilgisi
    summary_path = output_dir / "job_summary.json"
    if summary_path.exists() and not details:
        try:
            summary = _json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(summary, dict) and summary.get("ok") is False:
                manual_reviews = summary.get("manual_reviews")
                if isinstance(manual_reviews, list) and manual_reviews:
                    details.append(f"Pipeline {len(manual_reviews)} sayfa/poz icin manuel inceleme istedi.")
                    for mr in manual_reviews[:5]:
                        if isinstance(mr, dict):
                            details.append(f"  - Sayfa {mr.get('page', '?')}: {mr.get('reason', '?')}")
                    if len(manual_reviews) > 5:
                        details.append(f"  ... ve {len(manual_reviews) - 5} madde daha.")
        except Exception:
            pass

    return details


def _format_manual_review_detail_response(paths: RuntimePaths, job_id: str, request_text: str) -> str:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return f"`{job_id}` isi bulunamadi. Is listesinden dogru job ID'yi sec veya yaz."

    summary = _read_json_file(output_dir / "job_summary.json") or {}
    fsm = _read_json_file(output_dir / "fsm_state.json") or {}
    ok_value = summary.get("ok") if isinstance(summary, dict) else None
    manual_reviews = _manual_reviews_from_files(output_dir, summary)
    actionable_notes = [] if ok_value is True else _actionable_open_manager_issue_notes(paths, job_id)
    known_pozs = _known_job_pozs(paths, job_id, summary)
    affected_pozs = _affected_pozs_from_notes(actionable_notes, allowed_pozs=known_pozs)

    lines = [
        f"`{job_id}` manuel inceleme kayitlari:",
        f"- FSM: {fsm.get('state') if isinstance(fsm, dict) else 'uploaded'}",
        f"- Job sonucu: ok={str(ok_value).lower() if isinstance(ok_value, bool) else ok_value}",
        f"- Ana liste: workspace/outputs/jobs/{job_id}/manual_review_required.json",
        f"- Mudur notlari: workspace/outputs/jobs/{job_id}/manager_issue_notes.jsonl",
        f"- Poz QC dosyalari: workspace/outputs/jobs/{job_id}/<poz>/<poz>_qc.json",
    ]

    if manual_reviews:
        lines.append("Aktif manuel inceleme maddeleri:")
        deduped_reviews = _dedupe_manual_reviews(manual_reviews)
        for review in deduped_reviews[:12]:
            poz = review.get("poz_no") or "-"
            page = review.get("page") or "-"
            source_pdf = review.get("source_pdf") or "bilinmeyen PDF"
            reason = review.get("reason") or "manual_review"
            detail = _short_text(str(review.get("detail") or review.get("next_action") or ""), 220)
            lines.append(f"- Poz {poz}, sayfa {page}, {source_pdf}: {reason}" + (f" - {detail}" if detail else ""))
        if len(deduped_reviews) > 12:
            lines.append(f"- ... {len(deduped_reviews) - 12} kayit daha var")
    else:
        lines.append("Aktif `manual_review_required` maddesi yok.")

    if affected_pozs:
        lines.append(f"Mudur notlarindan etkilenen pozlar: {', '.join(affected_pozs)}")
    elif actionable_notes:
        lines.append(f"Aksiyon bekleyen mudur notu var: {len(actionable_notes)}")

    lower = _normalize_turkish(request_text)
    if any(term in lower for term in ("nasil", "tamam", "kapat", "coz")):
        lines.append("Tamamlama yolu:")
        lines.append("1. Listedeki poz veya sayfa icin aday geometriyi duzelt ve tekrar mudur onayina sok.")
        lines.append("2. Pipeline yeniden uretimden sonra `manual_review_required.json` kaybolmali veya bos kalmali.")
        lines.append("3. Poz QC dosyalarinda `ok=true` ve `manual_review_required=false` olmadan partlist acilmamali.")
        lines.append("4. Bunlar temizse `Partlist Uret` adimi calistirilabilir.")

    if ok_value is True and not manual_reviews:
        lines.append("Karar: Mevcut ozet dosyasina gore manuel inceleme kapanmis gorunuyor.")
    elif manual_reviews or actionable_notes:
        lines.append("Karar: Bu maddeler kapanmadan teslim/partlist acilmamali.")
    else:
        lines.append("Karar: Dosyalarda aktif manuel inceleme blokaji gorunmuyor; son QC ozetini kontrol etmek yeterli.")
    return "\n".join(lines)


def _format_deep_output_inspection(paths: RuntimePaths, job_id: str) -> str:
    """Çıktı klasörünü dosya düzeyinde tarar: hangi pozlar üretildi, hangileri hâlâ bloke."""
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return f"`{job_id}` isi bulunamadi."

    fsm = _read_json_file(output_dir / "fsm_state.json") or {}
    summary = _read_json_file(output_dir / "job_summary.json") or {}
    approved = _read_json_file(job_dir / "approved_plate_specs.json") or {}

    lines = [f"## `{job_id}` Cikti Denetimi"]
    lines.append(f"- FSM: {fsm.get('state', 'unknown')}")

    # Onaylı spec'teki pozlar
    approved_rows = approved.get("plates", approved) if isinstance(approved, dict) else approved
    approved_pozs: list[str] = []
    if isinstance(approved_rows, list):
        for row in approved_rows:
            if isinstance(row, dict) and row.get("poz_no"):
                approved_pozs.append(str(row["poz_no"]))
    lines.append(f"- Onaylı spec'teki poz sayısı: {len(approved_pozs)}")

    # Üretilen DXF/NC1 dosyaları (output klasöründe poz alt-klasörleri)
    produced_pozs: list[str] = []
    if output_dir.exists():
        for sub in sorted(output_dir.iterdir()):
            if sub.is_dir() and any(sub.glob("*.dxf")):
                produced_pozs.append(sub.name)
    lines.append(f"- Üretilen DXF poz sayısı: {len(produced_pozs)}")
    if produced_pozs:
        lines.append(f"  Üretilenler: {', '.join(produced_pozs[:20])}" + (" ..." if len(produced_pozs) > 20 else ""))

    # QC durumu per poz
    qc_ok: list[str] = []
    qc_fail: list[str] = []
    if output_dir.exists():
        for sub in sorted(output_dir.iterdir()):
            if sub.is_dir():
                qc_file = sub / f"{sub.name}_qc.json"
                if qc_file.exists():
                    qc_data = _read_json_file(qc_file) or {}
                    if isinstance(qc_data, dict):
                        if qc_data.get("ok"):
                            qc_ok.append(sub.name)
                        else:
                            qc_fail.append(sub.name)
    if qc_ok:
        lines.append(f"- QC ok: {len(qc_ok)} poz — {', '.join(qc_ok[:10])}" + (" ..." if len(qc_ok) > 10 else ""))
    if qc_fail:
        lines.append(f"- QC başarısız: {len(qc_fail)} poz — {', '.join(qc_fail)}")

    # Hâlâ açık manuel inceleme
    manual_reviews = _manual_reviews_from_files(output_dir, summary if isinstance(summary, dict) else {})
    still_open = [r for r in manual_reviews if r.get("poz_no") not in produced_pozs]
    if still_open:
        lines.append(f"- Hâlâ açık manuel inceleme: {len(still_open)} kayıt")
        for r in still_open[:8]:
            lines.append(f"  • Poz {r.get('poz_no', '?')}, sayfa {r.get('page', '?')}: {r.get('reason', '')}")
        if len(still_open) > 8:
            lines.append(f"  ... {len(still_open) - 8} kayıt daha")
    else:
        lines.append("- Açık manuel inceleme: yok (tüm review'lar kapanmış veya üretim tamamlandı)")

    # Onaylı ama üretilmemiş pozlar
    not_produced = [p for p in approved_pozs if p not in produced_pozs]
    if not_produced:
        lines.append(f"- Onaylı ama DXF üretilmemiş: {len(not_produced)} poz — {', '.join(not_produced[:15])}")

    # Partlist
    partlist_files = list(output_dir.glob("*.xlsx")) if output_dir.exists() else []
    if partlist_files:
        lines.append(f"- Partlist dosyası: {', '.join(f.name for f in partlist_files)}")
    else:
        lines.append("- Partlist: henüz üretilmedi")

    # Açık müdür notları
    open_notes = _actionable_open_manager_issue_notes(paths, job_id)
    if open_notes:
        lines.append(f"- Açık müdür notu: {len(open_notes)}")

    return "\n".join(lines)


def _manual_reviews_from_files(output_dir: Any, summary: dict[str, Any]) -> list[dict[str, Any]]:
    reviews = summary.get("manual_reviews") if isinstance(summary, dict) else None
    if isinstance(reviews, list):
        return [dict(item) for item in reviews if isinstance(item, dict)]
    fallback = _read_json_file(output_dir / "manual_review_required.json")
    if isinstance(fallback, list):
        return [dict(item) for item in fallback if isinstance(item, dict)]
    return []


def _dedupe_manual_reviews(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for review in reviews:
        key = (
            str(review.get("reason") or ""),
            str(review.get("source_pdf") or ""),
            str(review.get("page") or ""),
            str(review.get("poz_no") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(review)
    return deduped


def _actionable_open_manager_issue_notes(paths: RuntimePaths, job_id: str) -> list[dict[str, Any]]:
    output_dir = paths.jobs_output_root / job_id
    notes = _read_open_manager_issue_notes(output_dir)
    return [note for note in notes if _is_actionable_manager_issue_note(note)]


def _is_actionable_manager_issue_note(note: dict[str, Any]) -> bool:
    tags = {str(tag).lower() for tag in note.get("tags", []) if str(tag).strip()} if isinstance(note.get("tags"), list) else set()
    actionable_tags = {
        "pah/kose eksigi",
        "poligon kontur",
        "gorsel analiz notu",
        "eksik uretim",
        "eksik sayfa/poz",
    }
    return bool(tags & actionable_tags)


def _open_manager_issue_note_count(output_dir: Any) -> int:
    path = output_dir / "manager_issue_notes.jsonl"
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            note = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(note, dict) and note.get("status") in {None, "open"}:
            count += 1
    return count


def _apply_manager_decisions(paths: RuntimePaths, tools: ToolRegistry, job_id: str) -> dict[str, Any]:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return {"ok": False, "job_id": job_id, "error": "job_not_found"}

    fsm = get_fsm(paths.jobs_output_root)
    if fsm.is_in_progress(job_id):
        return {
            "ok": False,
            "job_id": job_id,
            "error": "job_in_progress",
            "fsm_state": fsm.get_state(job_id).value,
        }

    summary_before = _read_json_file(output_dir / "job_summary.json") or {}
    open_notes = _read_open_manager_issue_notes(output_dir)
    snapshot = _job_issue_snapshot(paths, job_id) or {}
    page_count = snapshot.get("page_count")
    produced_count = snapshot.get("produced_count", 0)
    visual_scope_mismatch = isinstance(page_count, int) and page_count > produced_count
    manual_reviews = summary_before.get("manual_reviews") if isinstance(summary_before, dict) else None
    has_manual_review = isinstance(manual_reviews, list) and bool(manual_reviews)
    has_blocking_signal = bool(open_notes) or has_manual_review or summary_before.get("ok") is False or visual_scope_mismatch

    run_result: dict[str, Any] | None = None
    if (job_dir / "approved_plate_specs.json").exists() and has_blocking_signal:
        fsm.force_transition(job_id, JobState.PRODUCING, reason="manager_decision_apply")
        run_result = tools.run("run_autocad_job", {"job_id": job_id, "autocad_live_policy": "off"})

    summary_after = _read_json_file(output_dir / "job_summary.json") or summary_before
    manual_reviews_after = summary_after.get("manual_reviews") if isinstance(summary_after, dict) else None
    should_await = (
        bool(open_notes)
        or (isinstance(manual_reviews_after, list) and bool(manual_reviews_after))
        or summary_after.get("ok") is False
        or visual_scope_mismatch
    )
    target_state = JobState.AWAITING_APPROVAL if should_await else JobState.COMPLETED
    fsm.force_transition(job_id, target_state, reason="manager_decision_apply")
    known_pozs = _known_job_pozs(paths, job_id, summary_after)

    return {
        "ok": True,
        "job_id": job_id,
        "fsm_state": target_state.value,
        "open_note_count": len(open_notes),
        "affected_pozs": _affected_pozs_from_notes(open_notes, allowed_pozs=known_pozs),
        "manual_review_count": len(manual_reviews_after) if isinstance(manual_reviews_after, list) else 0,
        "summary_ok": summary_after.get("ok") if isinstance(summary_after, dict) else None,
        "visual_scope_mismatch": visual_scope_mismatch,
        "production_refreshed": bool(run_result),
        "production_result_ok": run_result.get("ok") if isinstance(run_result, dict) else None,
        "production_error": run_result.get("error") if isinstance(run_result, dict) else None,
    }


def _extract_supplied_position_poz_no(text: str, *, job_id: str) -> str | None:
    repaired = repair_text(text).strip()
    quoted = re.findall(r"[\"'`\u201c\u201d]([^\"'`\u201c\u201d]{1,80})[\"'`\u201c\u201d]", repaired)
    for value in quoted:
        token = _clean_supplied_poz_token(value)
        if _is_valid_supplied_poz_token(token, job_id=job_id):
            return token

    patterns = (
        r"(?:poz\s+(?:bilgisi|biligisi|no(?:su)?|numarasi)|poznosu)\s*[:=]?\s*([A-Za-z0-9][A-Za-z0-9_.-]{0,63})",
        r"([A-Za-z0-9][A-Za-z0-9_.-]{0,63})\s+olarak\s+(?:alinabilir|al|kullan)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, repaired, flags=re.IGNORECASE):
            token = _clean_supplied_poz_token(match.group(1))
            if _is_valid_supplied_poz_token(token, job_id=job_id):
                return token
    return None


def _clean_supplied_poz_token(value: str) -> str:
    return str(value or "").strip().strip(".,;:()[]{}")


def _is_valid_supplied_poz_token(token: str | None, *, job_id: str) -> bool:
    if not token or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", token):
        return False
    lower = _normalize_turkish(token)
    if lower in {
        "poz",
        "no",
        "nosu",
        "bilgisi",
        "biligisi",
        "numarasi",
        "olarak",
        "alinabilir",
        "guncelle",
    }:
        return False
    return token != job_id


def _apply_supplied_position_info(
    paths: RuntimePaths,
    tools: ToolRegistry,
    job_id: str,
    poz_no: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return {"ok": False, "job_id": job_id, "poz_no": poz_no, "error": "job_not_found"}
    output_dir.mkdir(parents=True, exist_ok=True)

    fsm = get_fsm(paths.jobs_output_root)
    if fsm.is_in_progress(job_id):
        return {
            "ok": False,
            "job_id": job_id,
            "poz_no": poz_no,
            "error": "job_in_progress",
            "fsm_state": fsm.get_state(job_id).value,
        }

    summary_before = _read_json_file(output_dir / "job_summary.json") or {}
    pages = _pages_for_supplied_position_info(paths, job_id, summary_before)
    positions_path = _write_supplied_positions_json(paths, job_id, poz_no, pages)

    fsm.force_transition(job_id, JobState.PRODUCING, reason="manager_supplied_position_info")
    append_job_event(
        paths,
        job_id,
        "production_started",
        {
            "scope": "manager_supplied_position_info",
            "poz_no": poz_no,
            "pages": pages,
            "positions_path": _relative_to_suite(paths, positions_path),
        },
    )
    pipeline = tools.run("run_autocad_job", {"job_id": job_id, "autocad_live_policy": "off"})
    summary = pipeline.get("summary") if isinstance(pipeline.get("summary"), dict) else (_read_json_file(output_dir / "job_summary.json") or {})
    manual_reviews = _manual_reviews_from_files(output_dir, summary if isinstance(summary, dict) else {})
    summary_ok = summary.get("ok") if isinstance(summary, dict) else None
    produced = summary.get("produced") if isinstance(summary, dict) and isinstance(summary.get("produced"), list) else []

    completion: dict[str, Any] | None = None
    if bool(pipeline.get("ok")) and summary_ok is True:
        completion = complete_approved_job(
            paths,
            job_id,
            summary,
            approved_count=len(produced) if produced else None,
            session_id=session_id,
        )
    else:
        fsm.force_transition(job_id, JobState.AWAITING_APPROVAL, reason="manager_supplied_position_manual_review")
        append_job_event(
            paths,
            job_id,
            "qc_completed",
            {
                "ok": False,
                "scope": "manager_supplied_position_info",
                "manual_review_count": len(manual_reviews),
                "produced_count": len(produced),
            },
        )

    final_state = get_fsm(paths.jobs_output_root).get_state(job_id).value
    return {
        "ok": bool(completion and completion.get("ok")),
        "job_id": job_id,
        "poz_no": poz_no,
        "pages": pages,
        "positions_path": _relative_to_suite(paths, positions_path),
        "pipeline_ok": bool(pipeline.get("ok")),
        "summary_ok": summary_ok,
        "manual_reviews": manual_reviews,
        "manual_review_count": len(manual_reviews),
        "produced_count": len(produced),
        "poz_no_blocker_closed": not any(str(item.get("reason")) == "poz_no_not_found" for item in manual_reviews),
        "fsm_state": final_state,
        "partlist": completion.get("partlist") if isinstance(completion, dict) else None,
        "retrospective": completion.get("retrospective") if isinstance(completion, dict) else None,
        "message": completion.get("message") if isinstance(completion, dict) else None,
        "error": None if completion and completion.get("ok") else (completion or pipeline).get("error"),
    }


def _pages_for_supplied_position_info(paths: RuntimePaths, job_id: str, summary: dict[str, Any]) -> list[int]:
    output_dir = paths.jobs_output_root / job_id
    reviews = _manual_reviews_from_files(output_dir, summary)
    pages = sorted(
        {
            int(review["page"])
            for review in reviews
            if review.get("reason") == "poz_no_not_found" and isinstance(review.get("page"), int)
        }
    )
    if pages:
        return pages

    diagnostics = _read_json_file(output_dir / "pdf_diagnostics.json") or {}
    pdfs = diagnostics.get("pdfs") if isinstance(diagnostics, dict) else None
    if isinstance(pdfs, list) and len(pdfs) == 1:
        page_count = pdfs[0].get("page_count") if isinstance(pdfs[0], dict) else None
        if page_count == 1:
            return [1]
    return [1]


def _write_supplied_positions_json(paths: RuntimePaths, job_id: str, poz_no: str, pages: list[int]) -> Path:
    job_dir = paths.jobs_import_root / job_id
    positions_path = job_dir / "positions.json"
    existing = _read_json_file(positions_path)
    rows = existing.get("positions") if isinstance(existing, dict) and isinstance(existing.get("positions"), list) else existing
    positions = [dict(item) for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []
    page_set = set(pages)
    positions = [item for item in positions if _position_row_page(item) not in page_set]
    positions.extend({"poz_no": poz_no, "page": page} for page in pages)
    positions_path.write_text(
        json.dumps({"positions": positions}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return positions_path


def _position_row_page(row: dict[str, Any]) -> int | None:
    try:
        return int(row.get("page"))
    except (TypeError, ValueError):
        return None


def _format_supplied_position_info_response(result: dict[str, Any]) -> str:
    job_id = str(result.get("job_id") or "")
    poz_no = str(result.get("poz_no") or "")
    if not result.get("pipeline_ok", False) and result.get("error"):
        return f"`{job_id}` icin poz bilgisi uygulanamadi: {result.get('error')}"

    pages = result.get("pages") if isinstance(result.get("pages"), list) else []
    page_text = ", ".join(str(page) for page in pages) if pages else "belirsiz"
    lines = [
        f"`{job_id}` icin poz bilgisini uyguladim ve isi yeniden calistirdim.",
        f"- Yazilan poz: {poz_no}, sayfa: {page_text}",
        f"- positions.json: {result.get('positions_path')}",
        f"- Poz no blokaji: {'kapandi' if result.get('poz_no_blocker_closed') else 'devam ediyor'}",
        f"- Yeniden uretim/QC: ok={str(result.get('summary_ok')).lower()}",
        f"- Uretilen poz: {result.get('produced_count', 0)}",
        f"- Manuel inceleme: {result.get('manual_review_count', 0)}",
        f"- FSM: {result.get('fsm_state')}",
    ]
    reviews = result.get("manual_reviews")
    if isinstance(reviews, list) and reviews:
        lines.append("Kalan manuel inceleme:")
        for review in reviews[:5]:
            if not isinstance(review, dict):
                continue
            detail = str(review.get("detail") or review.get("reason") or "").strip()
            poz = review.get("poz_no") or poz_no
            page = review.get("page")
            prefix = f"- Poz {poz}" if poz else "-"
            if page:
                prefix += f", sayfa {page}"
            lines.append(f"{prefix}: {review.get('reason')} - {detail}")
        lines.append("Karar: Poz no artik var; ancak kalan manuel inceleme kapanmadan DXF/NC1/partlist teslim kapisi acilmadi.")
    partlist = result.get("partlist")
    if isinstance(partlist, dict) and partlist.get("path"):
        lines.append(f"- Partlist: {partlist.get('path')}")
    if result.get("message"):
        lines.append("")
        lines.append(str(result["message"]))
    elif result.get("error"):
        lines.append(f"- Kapanis blokaji: {result.get('error')}")
    return "\n".join(lines)


def _extract_page_numbers_for_exclusion(text: str) -> list[int]:
    repaired = repair_text(text)
    values: list[int] = []

    # Tekil: "sayfa no: X" ve "sayfa X"
    for pattern in (
        r"\bsayfa\s*no\s*[:#-]?\s*([0-9]{1,4})\b",
        r"\bsayfa\s*[:#-]?\s*([0-9]{1,4})\b",
    ):
        for match in re.finditer(pattern, repaired, flags=re.IGNORECASE):
            try:
                page = int(match.group(1))
            except ValueError:
                continue
            if page > 0:
                values.append(page)

    # Ordinal: "1. sayfa" veya "2. ve 3. sayfalar"
    for match in re.finditer(r"\b([0-9]{1,4})\.\s+sayfa", repaired, flags=re.IGNORECASE):
        try:
            page = int(match.group(1))
        except ValueError:
            continue
        if page > 0:
            values.append(page)

    # Çoklu virgül/ve: "sayfa X ve Y" veya "sayfa X, Y, Z"
    for match in re.finditer(
        r"\bsayfa\s+([0-9]{1,4}(?:\s*(?:,|ve)\s*[0-9]{1,4})+)",
        repaired,
        flags=re.IGNORECASE,
    ):
        for num_str in re.findall(r"[0-9]{1,4}", match.group(1)):
            try:
                page = int(num_str)
            except ValueError:
                continue
            if page > 0:
                values.append(page)

    # Aralık: "sayfa X-Y" → X, X+1, ..., Y
    for match in re.finditer(
        r"\bsayfa\s+([0-9]{1,4})\s*[-–]\s*([0-9]{1,4})\b",
        repaired,
        flags=re.IGNORECASE,
    ):
        try:
            start, end = int(match.group(1)), int(match.group(2))
        except ValueError:
            continue
        values.extend(p for p in range(start, end + 1) if 0 < p <= 9999)

    return list(dict.fromkeys(values))


def _apply_page_exclusion_decision(
    paths: RuntimePaths,
    tools: ToolRegistry,
    job_id: str,
    pages: list[int],
    note: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return {"ok": False, "job_id": job_id, "pages": pages, "error": "job_not_found"}
    output_dir.mkdir(parents=True, exist_ok=True)

    fsm = get_fsm(paths.jobs_output_root)
    if fsm.is_in_progress(job_id):
        return {
            "ok": False,
            "job_id": job_id,
            "pages": pages,
            "error": "job_in_progress",
            "fsm_state": fsm.get_state(job_id).value,
        }

    exclusions_path = _write_page_exclusions_json(paths, job_id, pages, note)
    try:
        import hashlib as _hashlib
        from .memory_bridge import get_memory_bridge as _get_memory_bridge
        _bridge = _get_memory_bridge(paths.workspace_root)
        for _pdf in (paths.jobs_import_root / job_id).glob("*.pdf"):
            _sha256 = _hashlib.sha256(_pdf.read_bytes()).hexdigest()
            _bridge.record_page_exclusion(_sha256, job_id, pages, note[:200])
    except Exception:
        pass
    resolved_notes = _resolve_open_manager_notes_matching(
        paths,
        job_id,
        keywords=("baslik", "plaka cizimi", "profil detayi", "profil detay", "atlan"),
        resolution="manager_page_exclusion uygulandi",
    )
    fsm.force_transition(job_id, JobState.PRODUCING, reason="manager_page_exclusion")
    append_job_event(
        paths,
        job_id,
        "production_started",
        {
            "scope": "manager_page_exclusion",
            "pages": pages,
            "page_exclusions_path": _relative_to_suite(paths, exclusions_path),
        },
    )
    pipeline = tools.run("run_autocad_job", {"job_id": job_id, "autocad_live_policy": "off"})
    summary = pipeline.get("summary") if isinstance(pipeline.get("summary"), dict) else (_read_json_file(output_dir / "job_summary.json") or {})
    manual_reviews = _manual_reviews_from_files(output_dir, summary if isinstance(summary, dict) else {})
    produced = summary.get("produced") if isinstance(summary, dict) and isinstance(summary.get("produced"), list) else []
    summary_ok = summary.get("ok") if isinstance(summary, dict) else None

    completion: dict[str, Any] | None = None
    if bool(pipeline.get("ok")) and summary_ok is True:
        completion = complete_approved_job(
            paths,
            job_id,
            summary,
            approved_count=len(produced) if produced else None,
            session_id=session_id,
        )
    else:
        fsm.force_transition(job_id, JobState.AWAITING_APPROVAL, reason="manager_page_exclusion_manual_review")
        append_job_event(
            paths,
            job_id,
            "qc_completed",
            {
                "ok": False,
                "scope": "manager_page_exclusion",
                "manual_review_count": len(manual_reviews),
                "produced_count": len(produced),
            },
        )

    applied = _read_json_file(output_dir / "page_exclusions_applied.json") or {}
    return {
        "ok": bool(completion and completion.get("ok")),
        "job_id": job_id,
        "pages": pages,
        "page_exclusions_path": _relative_to_suite(paths, exclusions_path),
        "applied_exclusion_count": len(applied.get("excluded_pages", [])) if isinstance(applied, dict) and isinstance(applied.get("excluded_pages"), list) else 0,
        "resolved_note_count": resolved_notes,
        "pipeline_ok": bool(pipeline.get("ok")),
        "summary_ok": summary_ok,
        "manual_reviews": manual_reviews,
        "manual_review_count": len(manual_reviews),
        "produced_count": len(produced),
        "fsm_state": get_fsm(paths.jobs_output_root).get_state(job_id).value,
        "partlist": completion.get("partlist") if isinstance(completion, dict) else None,
        "retrospective": completion.get("retrospective") if isinstance(completion, dict) else None,
        "message": completion.get("message") if isinstance(completion, dict) else None,
        "error": None if completion and completion.get("ok") else (completion or pipeline).get("error"),
    }


def _write_page_exclusions_json(paths: RuntimePaths, job_id: str, pages: list[int], note: str) -> Path:
    job_dir = paths.jobs_import_root / job_id
    path = job_dir / "page_exclusions.json"
    existing = _read_json_file(path)
    rows = existing.get("excluded_pages") if isinstance(existing, dict) and isinstance(existing.get("excluded_pages"), list) else existing
    exclusions = [dict(item) for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []
    page_set = set(pages)
    exclusions = [item for item in exclusions if _position_row_page(item) not in page_set]
    decided_at = _now_iso()
    for page in pages:
        exclusions.append(
            {
                "page": page,
                "reason": "manager_confirmed_non_plate_page",
                "note": _short_text(note, 400),
                "decided_by": "teknik-ofis-muduru",
                "decided_at": decided_at,
            }
        )
    path.write_text(
        json.dumps({"schema_version": 1, "excluded_pages": exclusions}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _format_page_exclusion_response(result: dict[str, Any]) -> str:
    job_id = str(result.get("job_id") or "")
    if not result.get("pipeline_ok", False) and result.get("error"):
        return f"`{job_id}` icin sayfa atlama karari uygulanamadi: {result.get('error')}"
    pages = result.get("pages") if isinstance(result.get("pages"), list) else []
    page_text = ", ".join(str(page) for page in pages) if pages else "belirsiz"
    lines = [
        f"`{job_id}` icin sayfa atlama kararini uyguladim ve isi yeniden calistirdim.",
        f"- Atlanan sayfa: {page_text}",
        f"- page_exclusions.json: {result.get('page_exclusions_path')}",
        f"- Uygulanan atlama kaydi: {result.get('applied_exclusion_count', 0)}",
        f"- Kapatilan mudur notu: {result.get('resolved_note_count', 0)}",
        f"- Yeniden uretim/QC: ok={str(result.get('summary_ok')).lower()}",
        f"- Uretilen poz: {result.get('produced_count', 0)}",
        f"- Manuel inceleme: {result.get('manual_review_count', 0)}",
        f"- FSM: {result.get('fsm_state')}",
    ]
    reviews = result.get("manual_reviews")
    if isinstance(reviews, list) and reviews:
        lines.append("Kalan manuel inceleme:")
        for review in reviews[:5]:
            if not isinstance(review, dict):
                continue
            page = review.get("page")
            poz = review.get("poz_no")
            detail = str(review.get("detail") or review.get("reason") or "").strip()
            prefix = f"- Sayfa {page}" if page else "-"
            if poz:
                prefix += f", Poz {poz}"
            lines.append(f"{prefix}: {review.get('reason')} - {detail}")
        if len(reviews) > 5:
            lines.append(f"- ... {len(reviews) - 5} kayit daha var")
        lines.append("Karar: Atlanan sayfa artik blokaj degil; kalan manuel incelemeler kapanmadan teslim/partlist kapisi acilmadi.")
    partlist = result.get("partlist")
    if isinstance(partlist, dict) and partlist.get("path"):
        lines.append(f"- Partlist: {partlist.get('path')}")
    if result.get("message"):
        lines.append("")
        lines.append(str(result["message"]))
    elif result.get("error"):
        lines.append(f"- Kapanis blokaji: {result.get('error')}")
    return "\n".join(lines)


def _apply_mark_column_position_hint(
    paths: RuntimePaths,
    tools: ToolRegistry,
    job_id: str,
    note: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return {"ok": False, "job_id": job_id, "error": "job_not_found"}
    output_dir.mkdir(parents=True, exist_ok=True)

    fsm = get_fsm(paths.jobs_output_root)
    if fsm.is_in_progress(job_id):
        return {
            "ok": False,
            "job_id": job_id,
            "error": "job_in_progress",
            "fsm_state": fsm.get_state(job_id).value,
        }

    hints_path = _write_position_hints_json(paths, job_id, note)
    resolved_notes = _resolve_open_manager_notes_matching(
        paths,
        job_id,
        keywords=("mark", "sutun", "sutn", "tablo", "alt kisim", "alt kism"),
        resolution="manager_mark_column_position_hint uygulandi",
    )
    fsm.force_transition(job_id, JobState.PRODUCING, reason="manager_mark_column_position_hint")
    append_job_event(
        paths,
        job_id,
        "production_started",
        {
            "scope": "manager_mark_column_position_hint",
            "position_hints_path": _relative_to_suite(paths, hints_path),
        },
    )
    pipeline = tools.run("run_autocad_job", {"job_id": job_id, "autocad_live_policy": "off"})
    summary = pipeline.get("summary") if isinstance(pipeline.get("summary"), dict) else (_read_json_file(output_dir / "job_summary.json") or {})
    manual_reviews = _manual_reviews_from_files(output_dir, summary if isinstance(summary, dict) else {})
    produced = summary.get("produced") if isinstance(summary, dict) and isinstance(summary.get("produced"), list) else []
    summary_ok = summary.get("ok") if isinstance(summary, dict) else None

    completion: dict[str, Any] | None = None
    if bool(pipeline.get("ok")) and summary_ok is True:
        completion = complete_approved_job(
            paths,
            job_id,
            summary,
            approved_count=len(produced) if produced else None,
            session_id=session_id,
        )
    else:
        fsm.force_transition(job_id, JobState.AWAITING_APPROVAL, reason="manager_mark_column_manual_review")
        append_job_event(
            paths,
            job_id,
            "qc_completed",
            {
                "ok": False,
                "scope": "manager_mark_column_position_hint",
                "manual_review_count": len(manual_reviews),
                "produced_count": len(produced),
            },
        )

    reviews_with_poz = [item for item in manual_reviews if isinstance(item, dict) and item.get("poz_no")]
    poz_not_found_count = sum(1 for item in manual_reviews if isinstance(item, dict) and item.get("reason") == "poz_no_not_found")
    return {
        "ok": bool(completion and completion.get("ok")),
        "job_id": job_id,
        "position_hints_path": _relative_to_suite(paths, hints_path),
        "resolved_note_count": resolved_notes,
        "pipeline_ok": bool(pipeline.get("ok")),
        "summary_ok": summary_ok,
        "manual_reviews": manual_reviews,
        "manual_review_count": len(manual_reviews),
        "reviews_with_poz_count": len(reviews_with_poz),
        "poz_not_found_count": poz_not_found_count,
        "produced_count": len(produced),
        "fsm_state": get_fsm(paths.jobs_output_root).get_state(job_id).value,
        "partlist": completion.get("partlist") if isinstance(completion, dict) else None,
        "retrospective": completion.get("retrospective") if isinstance(completion, dict) else None,
        "message": completion.get("message") if isinstance(completion, dict) else None,
        "error": None if completion and completion.get("ok") else (completion or pipeline).get("error"),
    }


def _write_position_hints_json(paths: RuntimePaths, job_id: str, note: str) -> Path:
    path = paths.jobs_import_root / job_id / "position_hints.json"
    existing = _read_json_file(path)
    hints = existing.get("hints") if isinstance(existing, dict) and isinstance(existing.get("hints"), list) else []
    hints = [dict(item) for item in hints if isinstance(item, dict)]
    hint = {
        "type": "mark_column_bottom_table",
        "source": "teknik-ofis-muduru",
        "note": _short_text(note, 400),
        "created_at": _now_iso(),
    }
    if not any(item.get("type") == hint["type"] for item in hints):
        hints.append(hint)
    path.write_text(json.dumps({"schema_version": 1, "hints": hints}, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _format_mark_column_position_hint_response(result: dict[str, Any]) -> str:
    job_id = str(result.get("job_id") or "")
    if not result.get("pipeline_ok", False) and result.get("error"):
        return f"`{job_id}` icin Mark sutunu poz okuma kuralini uygulayamadim: {result.get('error')}"
    lines = [
        f"`{job_id}` icin Mark sutunu poz okuma bilgisini uyguladim ve isi yeniden calistirdim.",
        f"- position_hints.json: {result.get('position_hints_path')}",
        f"- Kapatilan mudur notu: {result.get('resolved_note_count', 0)}",
        f"- Yeniden uretim/QC: ok={str(result.get('summary_ok')).lower()}",
        f"- Uretilen poz: {result.get('produced_count', 0)}",
        f"- Manuel inceleme: {result.get('manual_review_count', 0)}",
        f"- Pozu bulunan manuel inceleme: {result.get('reviews_with_poz_count', 0)}",
        f"- Pozu hala bulunamayan sayfa: {result.get('poz_not_found_count', 0)}",
        f"- FSM: {result.get('fsm_state')}",
    ]
    reviews = result.get("manual_reviews")
    if isinstance(reviews, list) and reviews:
        lines.append("Kalan manuel inceleme:")
        for review in reviews[:6]:
            if not isinstance(review, dict):
                continue
            page = review.get("page")
            poz = review.get("poz_no") or "-"
            detail = str(review.get("detail") or review.get("reason") or "").strip()
            lines.append(f"- Sayfa {page}, Poz {poz}: {review.get('reason')} - {detail}")
        if len(reviews) > 6:
            lines.append(f"- ... {len(reviews) - 6} kayit daha var")
        lines.append("Karar: Mark sutunundan poz okuma devrede; kalan blokajlar geometri/olcu veya hala poz bulunamayan sayfalar icin.")
    partlist = result.get("partlist")
    if isinstance(partlist, dict) and partlist.get("path"):
        lines.append(f"- Partlist: {partlist.get('path')}")
    if result.get("message"):
        lines.append("")
        lines.append(str(result["message"]))
    elif result.get("error"):
        lines.append(f"- Kapanis blokaji: {result.get('error')}")
    return "\n".join(lines)


def _apply_corner_relief_correction(
    paths: RuntimePaths,
    tools: ToolRegistry,
    job_id: str,
    poz_no: str,
    correction: dict[str, Any],
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    approved_path = job_dir / "approved_plate_specs.json"
    if not job_dir.exists():
        return {"ok": False, "job_id": job_id, "poz_no": poz_no, "error": "job_not_found"}
    approved = _read_json_file(approved_path)
    rows = approved.get("plates") if isinstance(approved, dict) and isinstance(approved.get("plates"), list) else None
    if rows is None:
        return {"ok": False, "job_id": job_id, "poz_no": poz_no, "error": "approved_specs_missing"}

    target: dict[str, Any] | None = None
    for row in rows:
        if isinstance(row, dict) and str(row.get("poz_no")) == poz_no:
            target = row
            break
    if target is None:
        return {"ok": False, "job_id": job_id, "poz_no": poz_no, "error": "poz_not_in_approved_specs"}

    reliefs = target.get("corner_reliefs")
    if not isinstance(reliefs, list):
        reliefs = []
        target["corner_reliefs"] = reliefs
    corner = str(correction["corner"])
    relief = next((item for item in reliefs if isinstance(item, dict) and item.get("corner") == corner), None)
    if relief is None:
        relief = {"corner": corner}
        reliefs.append(relief)
    relief.update(
        {
            "radius": float(correction["radius"]),
            "relief_type": str(correction.get("relief_type") or "chamfer"),
            "x_offset": float(correction["x_offset"]),
            "y_offset": float(correction["y_offset"]),
        }
    )
    notes = target.get("notes")
    if not isinstance(notes, list):
        notes = []
    note_text = (
        f"manager_corrected_corner_relief:{corner}:"
        f"{float(correction['x_offset']):g}x{float(correction['y_offset']):g}"
    )
    if note_text not in notes:
        notes.append(note_text)
    target["notes"] = notes
    approved_path.write_text(json.dumps(approved, indent=2, ensure_ascii=False), encoding="utf-8")

    resolved_notes = _resolve_geometry_notes_with_approved_reliefs(paths, job_id)
    fsm = get_fsm(paths.jobs_output_root)
    fsm.force_transition(job_id, JobState.PRODUCING, reason="manager_poz_correction")
    append_job_event(
        paths,
        job_id,
        "production_started",
        {
            "scope": "single_poz_correction",
            "poz_no": poz_no,
            "corner": corner,
            "x_offset": correction["x_offset"],
            "y_offset": correction["y_offset"],
        },
    )
    pipeline = tools.run("run_autocad_job", {"job_id": job_id, "autocad_live_policy": "off"})
    summary = pipeline.get("summary") if isinstance(pipeline.get("summary"), dict) else (_read_json_file(output_dir / "job_summary.json") or {})
    completion: dict[str, Any] | None = None
    if pipeline.get("ok") and isinstance(summary, dict) and summary.get("ok") is True:
        completion = complete_approved_job(
            paths,
            job_id,
            summary,
            approved_count=len(rows),
            session_id=session_id,
        )
    else:
        fsm.force_transition(job_id, JobState.AWAITING_APPROVAL, reason="manager_poz_correction_qc_blocked")

    qc = _read_json_file(output_dir / poz_no / f"{poz_no}_qc.json") or {}
    return {
        "ok": bool(completion and completion.get("ok")),
        "job_id": job_id,
        "poz_no": poz_no,
        "correction": correction,
        "resolved_note_count": resolved_notes,
        "pipeline_ok": bool(pipeline.get("ok")),
        "summary_ok": summary.get("ok") if isinstance(summary, dict) else None,
        "qc_ok": qc.get("ok") if isinstance(qc, dict) else None,
        "fsm_state": get_fsm(paths.jobs_output_root).get_state(job_id).value,
        "partlist": completion.get("partlist") if isinstance(completion, dict) else None,
        "retrospective": completion.get("retrospective") if isinstance(completion, dict) else None,
        "message": completion.get("message") if isinstance(completion, dict) else None,
        "error": None if completion and completion.get("ok") else (completion or pipeline).get("error"),
    }


def _format_poz_correction_response(result: dict[str, Any]) -> str:
    job_id = str(result.get("job_id") or "")
    poz_no = str(result.get("poz_no") or "")
    if not result.get("pipeline_ok", False) and result.get("error"):
        return f"`{job_id}` Poz {poz_no} duzeltmesi uygulanamadi: {result.get('error')}"
    correction = result.get("correction") if isinstance(result.get("correction"), dict) else {}
    lines = [
        f"`{job_id}` Poz {poz_no} icin aksiyonu uyguladim.",
        (
            f"- Duzeltilen pah: {correction.get('corner')} "
            f"{float(correction.get('x_offset') or 0):g}x{float(correction.get('y_offset') or 0):g} mm"
        ),
        f"- Yeniden uretim/QC: ok={str(result.get('summary_ok')).lower()}, poz QC={str(result.get('qc_ok')).lower()}",
        f"- FSM: {result.get('fsm_state')}",
        f"- Kapatilan geometri notu: {result.get('resolved_note_count', 0)}",
    ]
    partlist = result.get("partlist")
    if isinstance(partlist, dict) and partlist.get("path"):
        lines.append(f"- Partlist guncellendi: {partlist.get('path')}")
    if result.get("message"):
        lines.append("")
        lines.append(str(result["message"]))
    elif result.get("error"):
        lines.append(f"- Kapanis blokaji: {result.get('error')}")
    return "\n".join(lines)


def _latest_referenced_poz_from_notes(paths: RuntimePaths, job_id: str, *, allowed_pozs: set[str]) -> str | None:
    notes = _read_open_manager_issue_notes(paths.jobs_output_root / job_id)
    for note in reversed(notes):
        affected = _note_known_pozs(note, allowed_pozs=allowed_pozs)
        if affected:
            return affected[-1]
    return None


def _latest_corner_correction_for_poz(
    paths: RuntimePaths,
    job_id: str,
    poz_no: str,
    current_text: str,
    history: list[dict[str, str]],
) -> dict[str, Any] | None:
    row = _approved_row_for_poz(paths, job_id, poz_no)
    if row is None:
        return None
    candidates: list[str] = [current_text]
    notes = _read_open_manager_issue_notes(paths.jobs_output_root / job_id)
    candidates.extend(str(note.get("message") or "") for note in reversed(notes) if poz_no in _note_known_pozs(note, allowed_pozs={poz_no}))
    for item in reversed(history[-12:]):
        if item.get("role") == "user" and isinstance(item.get("content"), str):
            candidates.append(_visible_user_text(str(item["content"])))
    for candidate in candidates:
        parsed = _parse_corner_correction(candidate, row)
        if parsed:
            parsed["source_text"] = _short_text(candidate, 220)
            return parsed
    return None


def _approved_row_for_poz(paths: RuntimePaths, job_id: str, poz_no: str) -> dict[str, Any] | None:
    approved = _read_json_file(paths.jobs_import_root / job_id / "approved_plate_specs.json") or {}
    rows = approved.get("plates") if isinstance(approved, dict) and isinstance(approved.get("plates"), list) else []
    for row in rows:
        if isinstance(row, dict) and str(row.get("poz_no")) == poz_no:
            return row
    return None


def _parse_corner_correction(text: str, row: dict[str, Any]) -> dict[str, Any] | None:
    lower = _normalize_turkish(text)
    match = re.search(r"\b([0-9]+(?:[.,][0-9]+)?)\s*[x×]\s*([0-9]+(?:[.,][0-9]+)?)\b", lower)
    if not match:
        return None
    corner = _corner_from_text(lower)
    if corner is None:
        return None
    first = float(match.group(1).replace(",", "."))
    second = float(match.group(2).replace(",", "."))
    width = _float_or_zero(row.get("width"))
    height = _float_or_zero(row.get("height"))
    short_value = min(first, second)
    long_value = max(first, second)
    long_axis = "y" if height >= width else "x"
    short_axis = "x" if long_axis == "y" else "y"
    target_axis: str | None = None
    if "uzun kenar" in lower:
        target_axis = long_axis
    elif "kisa kenar" in lower or "kısa kenar" in lower:
        target_axis = short_axis
    if target_axis == "x":
        x_offset, y_offset = long_value, short_value
    elif target_axis == "y":
        x_offset, y_offset = short_value, long_value
    else:
        x_offset, y_offset = first, second
    return {
        "corner": corner,
        "radius": min(x_offset, y_offset),
        "relief_type": "chamfer",
        "x_offset": x_offset,
        "y_offset": y_offset,
    }


def _corner_from_text(lower: str) -> str | None:
    has_left = "sol" in lower or "left" in lower
    has_right = "sag" in lower or "right" in lower
    has_top = "ust" in lower or "top" in lower
    has_bottom = "alt" in lower or "bottom" in lower
    if has_left and has_top:
        return "top_left"
    if has_right and has_top:
        return "top_right"
    if has_left and has_bottom:
        return "bottom_left"
    if has_right and has_bottom:
        return "bottom_right"
    return None


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _note_known_pozs(note: dict[str, Any], *, allowed_pozs: set[str]) -> list[str]:
    values = note.get("affected_pozs")
    affected = [str(value) for value in values if str(value) in allowed_pozs] if isinstance(values, list) else []
    if affected:
        return list(dict.fromkeys(affected))
    message = str(note.get("message") or "")
    return _extract_poz_numbers(message, allowed_pozs=allowed_pozs)


def _resolve_geometry_notes_with_approved_reliefs(paths: RuntimePaths, job_id: str) -> int:
    output_dir = paths.jobs_output_root / job_id
    path = output_dir / "manager_issue_notes.jsonl"
    if not path.exists():
        return 0
    known_pozs = _known_job_pozs(paths, job_id)
    approved = _read_json_file(paths.jobs_import_root / job_id / "approved_plate_specs.json") or {}
    rows = approved.get("plates") if isinstance(approved, dict) and isinstance(approved.get("plates"), list) else []
    approved_by_poz = {str(row.get("poz_no")): row for row in rows if isinstance(row, dict) and row.get("poz_no")}
    resolved = 0
    rewritten: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            note = json.loads(line)
        except json.JSONDecodeError:
            rewritten.append(line)
            continue
        if not isinstance(note, dict) or note.get("status") not in {None, "open"}:
            rewritten.append(json.dumps(note, ensure_ascii=False))
            continue
        tags = {str(tag).lower() for tag in note.get("tags", []) if str(tag).strip()} if isinstance(note.get("tags"), list) else set()
        if not ({"pah/kose eksigi", "poligon kontur", "gorsel analiz notu"} & tags):
            rewritten.append(json.dumps(note, ensure_ascii=False))
            continue
        affected = _note_known_pozs(note, allowed_pozs=known_pozs)
        if not affected:
            rewritten.append(json.dumps(note, ensure_ascii=False))
            continue
        if all(isinstance(approved_by_poz.get(poz), dict) and approved_by_poz[poz].get("corner_reliefs") for poz in affected):
            note = dict(note)
            note["status"] = "resolved"
            note["resolved_at"] = _now_iso()
            note["resolved_by"] = "teknik-ofis-muduru"
            note["resolution"] = "approved_plate_specs corner_reliefs ile yeniden uretim aksiyonuna alindi"
            resolved += 1
        rewritten.append(json.dumps(note, ensure_ascii=False))
    path.write_text("\n".join(rewritten) + ("\n" if rewritten else ""), encoding="utf-8")
    return resolved


def _read_open_manager_issue_notes(output_dir: Any) -> list[dict[str, Any]]:
    path = output_dir / "manager_issue_notes.jsonl"
    if not path.exists():
        return []
    notes: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            note = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(note, dict) and note.get("status") in {None, "open"}:
            notes.append(note)
    return notes


def _resolve_open_manager_notes_matching(
    paths: RuntimePaths,
    job_id: str,
    *,
    keywords: tuple[str, ...],
    resolution: str,
) -> int:
    path = paths.jobs_output_root / job_id / "manager_issue_notes.jsonl"
    if not path.exists():
        return 0
    resolved = 0
    rewritten: list[str] = []
    normalized_keywords = tuple(_normalize_turkish(keyword) for keyword in keywords)
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            note = json.loads(line)
        except json.JSONDecodeError:
            rewritten.append(line)
            continue
        if not isinstance(note, dict) or note.get("status") not in {None, "open"}:
            rewritten.append(json.dumps(note, ensure_ascii=False))
            continue
        message = _normalize_turkish(str(note.get("message") or ""))
        tags = " ".join(str(tag) for tag in note.get("tags", []) if str(tag).strip()) if isinstance(note.get("tags"), list) else ""
        haystack = f"{message} {_normalize_turkish(tags)}"
        if any(keyword and keyword in haystack for keyword in normalized_keywords):
            note = dict(note)
            note["status"] = "resolved"
            note["resolved_at"] = _now_iso()
            note["resolved_by"] = "teknik-ofis-muduru"
            note["resolution"] = resolution
            resolved += 1
        rewritten.append(json.dumps(note, ensure_ascii=False))
    path.write_text("\n".join(rewritten) + ("\n" if rewritten else ""), encoding="utf-8")
    return resolved


def _affected_pozs_from_notes(notes: list[dict[str, Any]], *, allowed_pozs: set[str] | None = None) -> list[str]:
    geometry_tags = {"pah/kose eksigi", "poligon kontur", "gorsel analiz notu"}
    pozs: list[str] = []
    for note in notes:
        tags = {str(tag) for tag in note.get("tags", []) if str(tag).strip()} if isinstance(note.get("tags"), list) else set()
        if tags and not (tags & geometry_tags):
            continue
        values = note.get("affected_pozs")
        if isinstance(values, list):
            pozs.extend(str(value) for value in values if re.fullmatch(r"[0-9]{3,6}", str(value).strip()))
    unique = list(dict.fromkeys(pozs))
    return [poz for poz in unique if poz in allowed_pozs] if allowed_pozs else unique


def _known_job_pozs(paths: RuntimePaths, job_id: str, summary: dict[str, Any] | None = None) -> set[str]:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    known: set[str] = set()
    summary_data = summary if isinstance(summary, dict) else (_read_json_file(output_dir / "job_summary.json") or {})
    for item in summary_data.get("produced", []) if isinstance(summary_data, dict) else []:
        if isinstance(item, dict) and item.get("poz_no"):
            known.add(str(item["poz_no"]))
    codex = _read_json_file(output_dir / "codex_candidates.json") or {}
    for item in codex.get("candidates", []) if isinstance(codex, dict) else []:
        if isinstance(item, dict) and item.get("poz_no"):
            known.add(str(item["poz_no"]))
    approved = _read_json_file(job_dir / "approved_plate_specs.json") or {}
    rows = approved.get("plates", approved) if isinstance(approved, dict) else approved
    for item in rows if isinstance(rows, list) else []:
        if isinstance(item, dict) and item.get("poz_no"):
            known.add(str(item["poz_no"]))
    return known


def _format_apply_manager_decision_response(result: dict[str, Any]) -> str:
    job_id = str(result.get("job_id") or "")
    if not result.get("ok"):
        if result.get("error") == "job_in_progress":
            return f"`{job_id}` su anda isleniyor ({result.get('fsm_state')}); mudur kararini uygulamak icin once mevcut islem bitmeli."
        return f"`{job_id}` icin mudur karari uygulanamadi: {result.get('error')}"

    lines = [
        f"`{job_id}` icin mudur kararini uyguladim.",
        f"- FSM: {result.get('fsm_state')}",
        f"- Acik mudur notu: {result.get('open_note_count', 0)}",
        f"- Manuel inceleme: {result.get('manual_review_count', 0)}",
        f"- Job sonucu: ok={str(result.get('summary_ok')).lower()}",
    ]
    affected = result.get("affected_pozs")
    if isinstance(affected, list) and affected:
        lines.append(f"- Etkilenen pozlar: {', '.join(affected)}")
    if result.get("production_refreshed"):
        lines.append("- Uretim/QC ozeti acik mudur notlariyla tekrar degerlendirildi.")
    if result.get("production_error"):
        lines.append(f"- Uretim tekrar kontrolu hata verdi: {result.get('production_error')}")
    if result.get("visual_scope_mismatch"):
        lines.append("- PDF sayfa kapsami eksik oldugu icin teslim/partlist kapisi kapali kalmali.")
    lines.append("Sonraki adim: eksik sayfalar ve pah/kose adaylari duzeltilip yeniden mudur onayina sunulmali.")
    return "\n".join(lines)


def _extract_missing_pdf_candidates(paths: RuntimePaths, bridge: Any, job_id: str) -> dict[str, Any]:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return {"ok": False, "job_id": job_id, "error": "job_not_found"}
    output_dir.mkdir(parents=True, exist_ok=True)

    diagnostics = _read_json_file(output_dir / "pdf_diagnostics.json") or {}
    codex_data = _read_json_file(output_dir / "codex_candidates.json") or {}
    existing_candidates = codex_data.get("candidates", []) if isinstance(codex_data, dict) and isinstance(codex_data.get("candidates"), list) else []
    pdf_infos = _pdf_infos_for_candidate_scan(paths, job_id, diagnostics)
    if not pdf_infos:
        return {"ok": False, "job_id": job_id, "error": "pdf_not_found"}

    single_pdf = len(pdf_infos) == 1
    missing_requests: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for info in pdf_infos:
        source_pdf = str(info["source_pdf"])
        page_count = int(info["page_count"])
        covered = _candidate_pages_for_pdf(existing_candidates, source_pdf, single_pdf=single_pdf)
        missing = [page for page in range(1, page_count + 1) if page not in covered]
        coverage.append(
            {
                "source_pdf": source_pdf,
                "page_count": page_count,
                "covered_pages": covered,
                "missing_pages": missing,
            }
        )
        missing_requests.extend({"source_pdf": source_pdf, "source_page": page} for page in missing)

    report_path = output_dir / "missing_candidate_review.json"
    report: dict[str, Any] = {
        "schema_version": 1,
        "job_id": job_id,
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "pending",
        "existing_candidate_count": len(existing_candidates),
        "coverage": coverage,
        "missing_page_count": len(missing_requests),
        "missing_pages": missing_requests,
    }
    if not missing_requests:
        report["status"] = "complete_no_missing_pages"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "ok": True,
            "job_id": job_id,
            "status": report["status"],
            "existing_candidate_count": len(existing_candidates),
            "missing_page_count": 0,
            "report_path": _relative_to_suite(paths, report_path),
        }

    try:
        rendered = _render_pdf_page_requests(paths, job_id, missing_requests)
    except RuntimeError as exc:
        report["status"] = "render_failed"
        report["error"] = str(exc)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "ok": False,
            "job_id": job_id,
            "status": report["status"],
            "error": str(exc),
            "missing_page_count": len(missing_requests),
            "report_path": _relative_to_suite(paths, report_path),
        }

    report["rendered_pages"] = [
        {
            "source_pdf": item["source_pdf"],
            "source_page": item["source_page"],
            "image": _relative_to_suite(paths, item["image"]),
        }
        for item in rendered
    ]
    if not rendered:
        report["status"] = "rendered_no_pages"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "ok": False,
            "job_id": job_id,
            "status": report["status"],
            "error": "No missing PDF pages could be rendered.",
            "missing_page_count": len(missing_requests),
            "report_path": _relative_to_suite(paths, report_path),
        }

    schema_path = _visual_candidate_schema_path(paths)
    prompt = _missing_candidate_prompt(paths, job_id, rendered, existing_candidates)
    result = bridge.run(
        CodexRunRequest(
            prompt=prompt,
            agent_id="autocad-uzman-1",
            sandbox="read-only",
            timeout_seconds=180,
            images=[Path(item["image"]) for item in rendered],
            output_schema=schema_path,
        ),
        job_id=job_id,
    )
    if not result.ok:
        report["status"] = "codex_failed"
        report["error"] = result.error or "Codex candidate extraction failed."
        report["codex_run"] = _codex_record_dict(result)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "ok": False,
            "job_id": job_id,
            "status": report["status"],
            "error": report["error"],
            "missing_page_count": len(missing_requests),
            "rendered_page_count": len(rendered),
            "report_path": _relative_to_suite(paths, report_path),
        }

    try:
        parsed = json.loads(result.content)
    except json.JSONDecodeError as exc:
        report["status"] = "codex_json_failed"
        report["error"] = f"Codex candidate JSON parse failed: {exc.msg}"
        report["codex_run"] = _codex_record_dict(result)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "ok": False,
            "job_id": job_id,
            "status": report["status"],
            "error": report["error"],
            "missing_page_count": len(missing_requests),
            "rendered_page_count": len(rendered),
            "report_path": _relative_to_suite(paths, report_path),
        }

    raw_candidates = parsed.get("candidates") if isinstance(parsed, dict) else None
    if not isinstance(raw_candidates, list):
        raw_candidates = []
    allowed_pdf_names = [str(info["source_pdf"]) for info in pdf_infos]
    allowed_pages = {(str(item["source_pdf"]), int(item["source_page"])) for item in missing_requests}
    new_candidates = [
        _normalize_visual_candidate(item, index, provider="manager_pdf_scan", allowed_pdf_names=allowed_pdf_names)
        for index, item in enumerate(raw_candidates, start=1)
        if isinstance(item, dict)
    ]
    if single_pdf:
        only_pdf = allowed_pdf_names[0]
        for candidate in new_candidates:
            candidate["source_pdf"] = only_pdf
    new_candidates = [
        candidate
        for candidate in new_candidates
        if (str(candidate.get("source_pdf")), int(candidate.get("source_page") or 0)) in allowed_pages
    ]
    existing_keys = {_candidate_identity(candidate, single_pdf_name=allowed_pdf_names[0] if single_pdf else None) for candidate in existing_candidates if isinstance(candidate, dict)}
    merged = list(existing_candidates)
    appended: list[dict[str, Any]] = []
    for candidate in new_candidates:
        key = _candidate_identity(candidate, single_pdf_name=allowed_pdf_names[0] if single_pdf else None)
        if key in existing_keys:
            continue
        candidate["candidate_id"] = _next_candidate_id(merged, candidate.get("candidate_id") or "manager_pdf_scan")
        merged.append(candidate)
        appended.append(candidate)
        existing_keys.add(key)

    codex_out = {
        "schema_version": codex_data.get("schema_version", 1) if isinstance(codex_data, dict) else 1,
        "job_id": job_id,
        "candidates": merged,
        "last_manager_pdf_scan": _codex_record_dict(result),
    }
    if isinstance(codex_data, dict) and codex_data.get("codex_run"):
        codex_out["codex_run"] = codex_data.get("codex_run")
    (output_dir / "codex_candidates.json").write_text(json.dumps(codex_out, indent=2, ensure_ascii=False), encoding="utf-8")

    report.update(
        {
            "status": "extracted",
            "codex_run": _codex_record_dict(result),
            "extracted_candidate_count": len(new_candidates),
            "appended_candidate_count": len(appended),
            "candidates_path": _relative_to_suite(paths, output_dir / "codex_candidates.json"),
            "extracted_candidates": new_candidates,
        }
    )
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    get_fsm(paths.jobs_output_root).force_transition(job_id, JobState.AWAITING_APPROVAL, reason="manager_pdf_candidate_scan")
    return {
        "ok": True,
        "job_id": job_id,
        "status": report["status"],
        "missing_page_count": len(missing_requests),
        "rendered_page_count": len(rendered),
        "extracted_candidate_count": len(new_candidates),
        "appended_candidate_count": len(appended),
        "report_path": _relative_to_suite(paths, report_path),
        "candidates_path": _relative_to_suite(paths, output_dir / "codex_candidates.json"),
    }


def _format_missing_candidate_extraction_response(result: dict[str, Any]) -> str:
    job_id = str(result.get("job_id") or "")
    if result.get("ok"):
        if result.get("status") == "complete_no_missing_pages":
            return f"`{job_id}` icin eksik PDF sayfasi gorunmuyor. Rapor: {result.get('report_path')}"
        return "\n".join(
            [
                f"`{job_id}` icin PDF uzerinden eksik aday listesini cikardim.",
                f"- Eksik sayfa: {result.get('missing_page_count', 0)}",
                f"- Render edilen sayfa: {result.get('rendered_page_count', 0)}",
                f"- Cikarilan aday: {result.get('extracted_candidate_count', 0)}",
                f"- Aday listesine eklenen: {result.get('appended_candidate_count', 0)}",
                f"- Aday dosyasi: {result.get('candidates_path')}",
                f"- Inceleme raporu: {result.get('report_path')}",
                "Sonraki adim: yeni adaylari mudur onay ekraninda kontrol edip hatali/eksik geometri varsa duzeltmek.",
            ]
        )
    lines = [
        f"`{job_id}` icin PDF uzerinden eksik aday cikarma tamamlanamadi.",
        f"- Durum: {result.get('status') or 'failed'}",
    ]
    if result.get("missing_page_count") is not None:
        lines.append(f"- Eksik sayfa: {result.get('missing_page_count')}")
    if result.get("rendered_page_count") is not None:
        lines.append(f"- Render edilen sayfa: {result.get('rendered_page_count')}")
    if result.get("report_path"):
        lines.append(f"- Inceleme raporu: {result.get('report_path')}")
    if result.get("error"):
        lines.append(f"- Hata: {result.get('error')}")
    lines.append("Bu durumda teslim/partlist kapisi kapali kalir; gorsel aday okuma tekrar denenmeli veya eksik pozlar manuel girilmeli.")
    return "\n".join(lines)


def _pdf_infos_for_candidate_scan(paths: RuntimePaths, job_id: str, diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    job_dir = paths.jobs_import_root / job_id
    pdf_infos: list[dict[str, Any]] = []
    pdfs = diagnostics.get("pdfs") if isinstance(diagnostics, dict) else None
    if isinstance(pdfs, list):
        for item in pdfs:
            if not isinstance(item, dict) or not item.get("source_pdf"):
                continue
            page_count = item.get("page_count")
            if not isinstance(page_count, int) or page_count < 1:
                continue
            if item.get("classification") not in {"visual_text_required", "text_layer_unreadable", None}:
                continue
            pdf_infos.append({"source_pdf": str(item["source_pdf"]), "page_count": page_count})
    if pdf_infos:
        return pdf_infos
    return [
        {"source_pdf": path.name, "page_count": _pdf_page_count(path) or 1}
        for path in sorted(job_dir.glob("*.pdf"))
    ]


def _candidate_pages_for_pdf(candidates: list[Any], source_pdf: str, *, single_pdf: bool) -> list[int]:
    pages: list[int] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        page = candidate.get("source_page")
        if not isinstance(page, int) or page < 1:
            continue
        candidate_pdf = str(candidate.get("source_pdf") or "")
        if single_pdf or candidate_pdf == source_pdf:
            pages.append(page)
    return sorted(set(pages))


def _render_pdf_page_requests(paths: RuntimePaths, job_id: str, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        import fitz  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError("PyMuPDF is required for PDF page rendering. Install runtime dependencies and retry.") from exc
    if len(requests) > MANAGER_VISUAL_CANDIDATE_MAX_PAGES:
        raise RuntimeError(
            f"Visual analysis render limit exceeded ({MANAGER_VISUAL_CANDIDATE_MAX_PAGES} pages). "
            "Split the PDF or narrow the page range before visual extraction."
        )
    run_id = f"{job_id}-missing-{uuid.uuid4().hex[:8]}"
    pages_dir = paths.suite_root / ".state" / "codex-runs" / run_id / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[dict[str, Any]] = []
    grouped: dict[str, list[int]] = {}
    for request in requests:
        source_pdf = str(request.get("source_pdf") or "")
        page = int(request.get("source_page") or 0)
        if source_pdf and page > 0:
            grouped.setdefault(source_pdf, []).append(page)
    for source_pdf, pages in grouped.items():
        pdf_path = paths.jobs_import_root / job_id / _safe_pdf_name_local(source_pdf)
        if not pdf_path.exists():
            continue
        doc = None
        try:
            doc = fitz.open(pdf_path)
            for page_number in sorted(set(pages)):
                if page_number < 1 or page_number > len(doc):
                    continue
                page = doc[page_number - 1]
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                target = pages_dir / f"{pdf_path.stem}-p{page_number}.png"
                pix.save(target)
                rendered.append({"source_pdf": source_pdf, "source_page": page_number, "image": target})
        finally:
            close = getattr(doc, "close", None)
            if callable(close):
                close()
    return rendered


def _load_autocad_uzman_skill_context(paths: RuntimePaths) -> str:
    """autocad-uzman-1 AGENT.md ve PDF okuma/geometri cıkarma skill dosyalarını yükler."""
    agents_root = paths.suite_root / "agents"
    parts: list[str] = []
    for rel in (
        "autocad-uzman-1/AGENT.md",
        "_shared/skills/PDF_POZ_OKUMA.md",
        "_shared/skills/PLAKA_GEOMETRI_CIKARMA.md",
    ):
        p = agents_root / rel
        if p.exists():
            parts.append(f"# {rel}\n{p.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def _missing_candidate_prompt(paths: RuntimePaths, job_id: str, rendered: list[dict[str, Any]], existing_candidates: list[Any]) -> str:
    skill_context = _load_autocad_uzman_skill_context(paths)
    page_lines = [
        f"- image {index}: {item['source_pdf']} page {item['source_page']}"
        for index, item in enumerate(rendered, start=1)
    ]
    existing_pozs = [
        str(item.get("poz_no"))
        for item in existing_candidates
        if isinstance(item, dict) and item.get("poz_no")
    ]
    context_header = f"{skill_context}\n\n---\n" if skill_context.strip() else ""
    return (
        f"{context_header}"
        "Bu teknik ofis PDF renderlerinden eksik plaka adaylarini oku. Yalnizca JSON dondur.\n"
        "Bu is, daha once okunmamis sayfalari tamamlamak icindir; gorulmeyen olcu veya poz uydurma.\n"
        "Her image icin karsilik gelen PDF sayfasi:\n"
        + "\n".join(page_lines)
        + "\n"
        "JSON schema: {\"candidates\":[{\"source_pdf\":\"...pdf\",\"source_page\":1,\"poz_no\":\"1001\",\"width\":200,\"height\":100,\"thickness\":10,\"material\":\"S355\",\"quantity\":1,\"holes\":[{\"x\":50,\"y\":25,\"diameter\":18}],\"slots\":[],\"corner_reliefs\":[],\"contour_type\":\"rectangle|polygon|chamfered\",\"confidence\":0.45,\"evidence\":\"kisa kanit\"}]}\n"
        "Poz numarasi sayfa numarasi degildir. Cizimdeki parca/mark bilgisini poz_no olarak kullan.\n"
        "Plaka dis konturu dikdortgen degilse `contour_type` ile belirt. Pah/chamfer veya kose bosaltma varsa `corner_reliefs` doldur; bos birakma.\n"
        "Delik, slot, kalinlik, malzeme, adet ve ana olculer net degilse dusuk confidence ve acik evidence yaz; tamamen belirsizse aday verme.\n"
        f"Zaten mevcut pozlar: {', '.join(existing_pozs) if existing_pozs else 'yok'}.\n"
        f"Job: {job_id}"
    )


def _visual_candidate_schema_path(paths: RuntimePaths) -> Path:
    path = paths.suite_root / ".state" / "codex-runs" / "manager-missing-candidates.v1.schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_visual_candidate_output_schema(), indent=2), encoding="utf-8")
    return path


def _visual_candidate_output_schema() -> dict[str, Any]:
    number_or_null = {"type": ["number", "null"]}
    string_or_null = {"type": ["string", "null"]}
    integer_or_null = {"type": ["integer", "null"]}
    hole_schema = {
        "type": "object",
        "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "diameter": {"type": "number"}},
        "required": ["x", "y", "diameter"],
        "additionalProperties": False,
    }
    slot_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
            "length": {"type": "number"},
            "width": {"type": "number"},
            "rotation_deg": {"type": "number"},
        },
        "required": ["x", "y", "length", "width", "rotation_deg"],
        "additionalProperties": False,
    }
    relief_schema = {
        "type": "object",
        "properties": {"corner": {"type": "string"}, "radius": {"type": "number"}, "relief_type": {"type": "string"}},
        "required": ["corner", "radius", "relief_type"],
        "additionalProperties": False,
    }
    candidate_schema = {
        "type": "object",
        "properties": {
            "source_pdf": {"type": "string"},
            "source_page": integer_or_null,
            "poz_no": string_or_null,
            "width": number_or_null,
            "height": number_or_null,
            "thickness": number_or_null,
            "material": string_or_null,
            "quantity": integer_or_null,
            "holes": {"type": "array", "items": hole_schema},
            "slots": {"type": "array", "items": slot_schema},
            "corner_reliefs": {"type": "array", "items": relief_schema},
            "contour_type": string_or_null,
            "confidence": {"type": "number"},
            "evidence": string_or_null,
        },
        "required": [
            "source_pdf",
            "source_page",
            "poz_no",
            "width",
            "height",
            "thickness",
            "material",
            "quantity",
            "holes",
            "slots",
            "corner_reliefs",
            "contour_type",
            "confidence",
            "evidence",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"candidates": {"type": "array", "items": candidate_schema}},
        "required": ["candidates"],
        "additionalProperties": False,
    }


def _normalize_visual_candidate(item: dict[str, Any], index: int, *, provider: str, allowed_pdf_names: list[str]) -> dict[str, Any]:
    source_pdf = str(item.get("source_pdf") or "").strip()
    if source_pdf not in set(allowed_pdf_names) and len(allowed_pdf_names) == 1:
        source_pdf = allowed_pdf_names[0]
    return {
        "candidate_id": str(item.get("candidate_id") or f"{provider}-{index}"),
        "provider": provider,
        "source_pdf": source_pdf or item.get("source_pdf"),
        "source_page": item.get("source_page"),
        "poz_no": item.get("poz_no"),
        "width": item.get("width"),
        "height": item.get("height"),
        "thickness": item.get("thickness"),
        "material": item.get("material") or "UNKNOWN",
        "quantity": item.get("quantity") or 1,
        "holes": item.get("holes") if isinstance(item.get("holes"), list) else [],
        "slots": item.get("slots") if isinstance(item.get("slots"), list) else [],
        "corner_reliefs": item.get("corner_reliefs") if isinstance(item.get("corner_reliefs"), list) else [],
        "contour_type": item.get("contour_type"),
        "confidence": item.get("confidence") or 0.0,
        "evidence": item.get("evidence") or item.get("reason"),
        "approval_required": True,
        "validation_errors": [],
    }


def _candidate_identity(candidate: dict[str, Any], *, single_pdf_name: str | None = None) -> tuple[str, int, str]:
    source_pdf = single_pdf_name or str(candidate.get("source_pdf") or "")
    return (source_pdf, int(candidate.get("source_page") or 0), str(candidate.get("poz_no") or ""))


def _next_candidate_id(candidates: list[Any], preferred: Any) -> str:
    existing = {str(item.get("candidate_id")) for item in candidates if isinstance(item, dict) and item.get("candidate_id")}
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", str(preferred or "manager_pdf_scan"))[:80] or "manager_pdf_scan"
    if base not in existing:
        return base
    index = 1
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"


def _pdf_page_count(path: Path) -> int | None:
    try:
        import fitz  # type: ignore[import-not-found]
        doc = fitz.open(path)
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:
        return None


def _safe_pdf_name_local(value: str) -> str:
    name = Path(value).name.strip() or "input.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return re.sub(r"[^A-Za-z0-9_. -]", "_", name)


def _relative_to_suite(paths: RuntimePaths, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(paths.suite_root.resolve())).replace("\\", "/")
    except Exception:
        return str(path)


def _codex_record_dict(result: Any) -> dict[str, Any]:
    record = getattr(result, "record", None)
    to_dict = getattr(record, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        return value if isinstance(value, dict) else {}
    return {}


def _format_job_completion_plan(paths: RuntimePaths, job_id: str) -> str:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return f"`{job_id}` isi bulunamadi. Is listesinden dogru job ID'yi sec veya yaz."

    summary = _read_json_file(output_dir / "job_summary.json") or {}
    fsm = _read_json_file(output_dir / "fsm_state.json") or {}
    fsm_state = fsm.get("state") if isinstance(fsm, dict) else "uploaded"
    snapshot = _job_issue_snapshot(paths, job_id) or {}
    page_count = snapshot.get("page_count")
    produced_count = snapshot.get("produced_count", 0)
    candidate_count = snapshot.get("candidate_count", 0)
    manual_review_count = snapshot.get("manual_review_count", 0)
    open_notes = _read_open_manager_issue_notes(output_dir)
    affected_pozs = _affected_pozs_from_notes(open_notes, allowed_pozs=_known_job_pozs(paths, job_id, summary))
    visual_scope_mismatch = isinstance(page_count, int) and page_count > produced_count
    summary_ok = summary.get("ok") if isinstance(summary, dict) else None

    lines = [
        f"`{job_id}` icin tamamlamaya devam edebiliriz; fakat bu is su an dogrudan teslim/partlist adimina gecemez.",
        f"- FSM: {fsm_state}",
        f"- Job sonucu: ok={str(summary_ok).lower()}",
        f"- PDF kapsami: {page_count if page_count is not None else 'bilinmiyor'} sayfa, uretilen poz={produced_count}, aday={candidate_count}",
        f"- Manuel inceleme: {manual_review_count}",
        f"- Acik mudur notu: {len(open_notes)}",
    ]
    if affected_pozs:
        lines.append(f"- Geometri notu olan pozlar: {', '.join(affected_pozs)}")

    lines.append("Devam sirasi:")
    step = 1
    if visual_scope_mismatch:
        lines.append(f"{step}. Eksik sayfa kapsamini tamamla: gorsel/OCR inceleme {produced_count + 1}-{page_count} araligindaki sayfalari da aday listesine tasimali.")
        step += 1
    if affected_pozs:
        lines.append(f"{step}. {', '.join(affected_pozs)} pozlari icin pah/poligon bilgilerini adayda `corner_reliefs` ile duzelt; dikdortgen DXF bu notlarla QC'den gecmemeli.")
        step += 1
    if manual_review_count or open_notes:
        lines.append(f"{step}. Duzeltilen adaylari mudur onayina sun; acik notlar kapanmadan `approved_plate_specs.json` kesin teslim verisi sayilmamali.")
        step += 1
    lines.append(f"{step}. Yeniden uretimden sonra QC `ok=true` olmayan poz kalirsa partlist kapisini acma.")
    lines.append("Bu nedenle mevcut dogru sistem durumu `awaiting_approval` olmalidir; eksik aday/olcu duzeltmesi gelmeden otomatik tamamlandi yapmayacagim.")
    return "\n".join(lines)


def _format_job_completion_step(paths: RuntimePaths, job_id: str, step: int | None) -> str:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return f"`{job_id}` isi bulunamadi. Is listesinden dogru job ID'yi sec veya yaz."
    if step is None:
        return _format_job_completion_plan(paths, job_id)

    summary = _read_json_file(output_dir / "job_summary.json") or {}
    fsm = _read_json_file(output_dir / "fsm_state.json") or {}
    snapshot = _job_issue_snapshot(paths, job_id) or {}
    page_count = snapshot.get("page_count")
    produced_count = snapshot.get("produced_count", 0)
    candidate_count = snapshot.get("candidate_count", 0)
    manual_review_count = snapshot.get("manual_review_count", 0)
    open_notes = _read_open_manager_issue_notes(output_dir)
    affected_pozs = _affected_pozs_from_notes(open_notes, allowed_pozs=_known_job_pozs(paths, job_id, summary))
    fsm_state = fsm.get("state") if isinstance(fsm, dict) else "uploaded"
    visual_scope_mismatch = isinstance(page_count, int) and page_count > produced_count
    missing_range = None
    if visual_scope_mismatch:
        missing_range = f"{produced_count + 1}-{page_count}"

    if step == 1:
        lines = [
            f"`{job_id}` icin 1. adimi baslattim: eksik sayfa kapsami kontrolu.",
            "- Calisma modu: PDF uzerinden eksik sayfa/adayi ben kontrol edecegim.",
            f"- FSM: {fsm_state}",
            f"- PDF kapsami: {page_count if page_count is not None else 'bilinmiyor'} sayfa",
            f"- Mevcut aday/uretim: aday={candidate_count}, uretilen={produced_count}",
        ]
        if missing_range:
            lines.append(f"- Eksik gorsel aday kapsami: sayfa {missing_range}")
        else:
            lines.append("- Eksik gorsel aday kapsami dosyalardan net gorunmuyor; yine de aday ve QC kayitlari birlikte kontrol edilmeli.")
        if affected_pozs:
            lines.append(f"- Korunan geometri notlari: {', '.join(affected_pozs)} icin pah/poligon kontrolu")
        lines.extend(
            [
                "Bu adimda teslim/partlist acmayacagim; eksik sayfalar aday listesine poz, olcu, kalinlik, malzeme, delik ve pah/kose bilgisiyle tasinmali.",
                "Sonraki veri girdisi: her eksik sayfa icin sayfa no, poz no, ana olculer, malzeme/kalinlik ve varsa delik-pah-poligon bilgisi.",
            ]
        )
        return "\n".join(lines)

    if step == 2:
        lines = [
            f"`{job_id}` icin 2. adim: pah/poligon aday duzeltmesi.",
            f"- Geometri notu olan pozlar: {', '.join(affected_pozs) if affected_pozs else 'dosyalarda net poz bulunmuyor'}",
            "- Dikdortgen uretilmis ama PDF'de pah/kose gorulen adaylar `corner_reliefs` veya poligon kontur bilgisi olmadan QC'den gecmemeli.",
            "- Duzeltme sonrasi DXF/NC1 yeniden uretilmeli ve manuel inceleme notlari kapanmali.",
        ]
        return "\n".join(lines)

    if step == 3:
        return "\n".join(
            [
                f"`{job_id}` icin 3. adim: mudur onayi.",
                f"- Manuel inceleme: {manual_review_count}",
                f"- Acik mudur notu: {len(open_notes)}",
                "- Duzeltilen adaylar onaya sunulmadan `approved_plate_specs.json` kesin teslim verisi sayilmamali.",
                "- Onay yalnizca eksik sayfa ve geometri notlari kapandiktan sonra kabul edilmeli.",
            ]
        )

    if step == 4:
        return "\n".join(
            [
                f"`{job_id}` icin 4. adim: yeniden uretim, QC ve partlist kapisi.",
                "- Yeniden uretimden sonra `job_summary.ok=true` ve poz bazli QC temiz olmadan teslim/partlist acilmamali.",
                "- Herhangi bir QC hatasi veya acik manuel inceleme kalirsa is `awaiting_approval` durumunda tutulmali.",
            ]
        )

    return _format_job_completion_plan(paths, job_id)


def _looks_like_project_edit_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower:
        return False
    edit_words = (
        "duzelt",
        "guncelle",
        "ekle",
        "kaldir",
        "degistir",
        "uygula",
        "implement",
        "refactor",
        "patch",
        "yama",
        "kod yaz",
        "test ekle",
        "ayarla",
        "tanimla",
        "olmali",
        "olmasi",
        "calismali",
        "yapabilmeli",
        "yapabilemeliyim",
        "yapabilmeliyim",
    )
    if not any(word in lower for word in edit_words):
        return False
    project_targets = (
        "kod",
        "dosya",
        "repo",
        "proje uzerinde",
        "bu projede",
        "runtime",
        "dashboard",
        "arayuz",
        "ui",
        "frontend",
        "backend",
        "api",
        "endpoint",
        "pytest",
        "test",
        "codex cli",
        "mcp",
        "manager chat",
        "mudur chat",
        "mudur baglantisi",
        "teknik-ofis-muduru",
        "claude.md",
        "suite.ps1",
        "orchestrator.py",
        "app.py",
        "index.html",
        ".py",
        ".html",
        ".md",
        ".ps1",
        ".json",
        "runtime/",
        "runtime\\",
        "tests/",
        "tests\\",
    )
    return any(target in lower for target in project_targets)


def _format_project_edit_unavailable_response(reason: str) -> str:
    return (
        "Bu istek proje/kod duzeltmesi gerektiriyor; bunun icin mudurun Codex CLI'yi proje yazma modunda calistirmasi gerekiyor. "
        f"Su anda Codex cevabi tamamlanamadi ({reason}). `toffice doctor` ile Codex CLI durumunu kontrol edip tekrar deneyelim; "
        "runtime isleri ve mevcut job kararlarini yine yerel araclarla yonetebilirim."
    )


def _extract_numeric_reference(text: str) -> str | None:
    match = re.search(r"\b[0-9]{2,}\b", text)
    return match.group(0) if match else None


def _extract_poz_no(text: str) -> str | None:
    match = re.search(r"\b[0-9]{3,6}\b", text)
    return match.group(0) if match else None


def _looks_like_job_reference(lower: str) -> bool:
    return any(word in lower for word in ("job", "is", "id", "numara"))


def _looks_like_selected_job_reference(lower: str) -> bool:
    return _looks_like_job_reference(lower) or any(phrase in lower for phrase in ("bu isin", "bu isi", "bu ise", "secili is", "secilen is", "pdf"))


def _looks_like_list_jobs_request(lower: str) -> bool:
    return any(
        phrase in lower
        for phrase in (
            "isleri listele",
            "joblari listele",
            "hangi isler",
            "mevcut isler",
            "hangi isimiz",
            "hangi is var",
            "aktif is",
            "is listesi",
            "is listele",
            "joblar neler",
            "hangi pdf",
            "hangi dosya",
            "ne isimiz var",
        )
    )


def _looks_like_create_job_request(lower: str) -> bool:
    return any(
        phrase in lower
        for phrase in (
            "yeni is",
            "yeni job",
            "is baslat",
            "imports",
            "klasor olustur",
            "is ac",
            "is ekle",
            "job olustur",
            "job ac",
        )
    )


def _extract_project_name(text: str) -> str | None:
    match = re.search(r"(?:proje|project|isim|adi|named?)[:\s]+([^\n,;]+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _looks_like_run_request(lower: str) -> bool:
    if "run job" in lower or "pipeline calistir" in lower:
        return True
    if any(phrase in lower for phrase in ("calistir", "isle", "uretime al", "uretimi baslat", "yeniden uret")):
        return True
    return re.search(r"\buret\b", lower) is not None


def _looks_like_issue_discussion_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if _looks_like_run_request(lower) or _looks_like_list_jobs_request(lower) or _looks_like_create_job_request(lower):
        return False
    issue_words = (
        "hata",
        "yanlis",
        "duzelt",
        "duzeltebilir",
        "sorun",
        "sikinti",
        "tespit",
        "eksik",
        "sayfa",
        "pdf icerisinde",
        "toplam",
        "uretti",
        "uretildi",
        "neden",
        "cevap vermedin",
        "yapilmadi",
        "olmadi",
        "pah",
        "poligon",
        "kose",
        "cizim",
        "gorsel analiz",
        "gorselanaliz",
        "ilet",
        "aktar",
    )
    return any(word in lower for word in issue_words)


def _looks_like_job_learning_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not any(word in lower for word in ("ogrendi", "ogrenim", "retrospektif", "retrospective", "ders", "lesson")):
        return False
    return any(word in lower for word in ("agent", "ajan", "sistem", "proje", "is", "job", "bu projede"))


def _looks_like_agent_creation_request(lower: str) -> bool:
    return "ajan" in lower and any(
        word in lower
        for word in (
            "taslak",
            "olustur",
            "kur",
            "hazirla",
            "ekle",
            "yarat",
            "yeni",
        )
    )


def _policy_from_text(lower: str) -> str:
    return "auto_start_if_needed" if any(word in lower for word in ("live acik", "autocad on")) else "off"


def _normalize_turkish(text: str) -> str:
    return normalize_search_text(text)


def _visible_user_text(text: str) -> str:
    cut = len(text)
    for marker in (f"\n\n{_SELECTED_CONTEXT_MARKER}", f"\n\n{_MEMORY_CONTEXT_MARKER}"):
        idx = text.find(marker)
        if idx >= 0:
            cut = min(cut, idx)
    return repair_text(text[:cut].strip())


def _repair_mojibake_text(text: str) -> str:
    if not text or not any(marker in text for marker in ("Ã", "Ä", "Å")):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return text
    return repaired if sum(repaired.count(marker) for marker in ("Ã", "Ä", "Å")) < sum(text.count(marker) for marker in ("Ã", "Ä", "Å")) else text


def _selected_job_id_from_context(text: str) -> str | None:
    raw = _hidden_context_payload(text, _SELECTED_CONTEXT_MARKER)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    job_id = parsed.get("selected_job_id") if isinstance(parsed, dict) else None
    return str(job_id) if job_id else None


def _job_id_from_memory_context(text: str) -> str | None:
    raw = _hidden_context_payload(text, _MEMORY_CONTEXT_MARKER)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    primary = parsed.get("primary_job_id")
    if isinstance(primary, str) and primary.strip():
        return primary.strip()
    for key in ("facts", "recent_events"):
        items = parsed.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            job_id = item.get("job_id")
            if isinstance(job_id, str) and job_id.strip():
                return job_id.strip()
            job_ids = item.get("job_ids")
            if isinstance(job_ids, list):
                for value in job_ids:
                    if isinstance(value, str) and value.strip():
                        return value.strip()
    return None


def _manager_memory_context_block(text: str) -> str:
    raw = _hidden_context_payload(text, _MEMORY_CONTEXT_MARKER)
    if raw is None:
        return "(ilgili kalici hafiza kaydi yok)"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return "(hafiza baglami okunamadi)"
    if not isinstance(parsed, dict):
        return "(hafiza baglami bos)"
    primary = parsed.get("primary_job_id")
    facts = parsed.get("facts")
    events = parsed.get("recent_events")
    lines: list[str] = []
    if isinstance(primary, str) and primary.strip():
        lines.append(f"Ilgili is: `{primary.strip()}`")
    if isinstance(facts, list) and facts:
        lines.append("Acik/ilgili notlar:")
        for fact in facts[:8]:
            if not isinstance(fact, dict):
                continue
            fact_type = str(fact.get("fact_type") or "note")
            status = str(fact.get("status") or "open")
            content = str(fact.get("content") or "").strip()
            if content:
                lines.append(f"- [{status}] {fact_type}: {_short_text(content, 260)}")
    if isinstance(events, list) and events:
        lines.append("Son ilgili konusma:")
        for event in events[:8]:
            if not isinstance(event, dict):
                continue
            role = str(event.get("role") or "user")
            content = str(event.get("content") or "").strip()
            if content:
                lines.append(f"- {role}: {_short_text(content, 220)}")
    # Son extraction pattern'larını ekle (memory_bridge)
    try:
        from .memory_bridge import get_memory_bridge
        paths = get_paths()
        bridge = get_memory_bridge(paths.workspace_root)
        recent = bridge.get_recent_patterns(limit=3)
        if recent:
            lines.append("Son ogrenilen cikarma kaliplari (memory_bridge):")
            for p in recent:
                job = str(p.get("job_id") or "?")
                count = p.get("pattern_count") or 0
                conf = p.get("confidence") or 0.0
                lines.append(f"- {job}: {count} poz, guven={conf:.2f}")
    except Exception:
        pass
    return "\n".join(lines) if lines else "(ilgili kalici hafiza kaydi yok)"


def _hidden_context_payload(text: str, marker: str) -> str | None:
    idx = text.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    end = text.find("\n\n[", start)
    raw = text[start:end if end >= 0 else len(text)].strip()
    if raw.endswith("]"):
        raw = raw[:-1].strip()
    return raw or None


def _short_text(value: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


def _job_id_from_recent_history(history: list[dict[str, str]]) -> str | None:
    for item in reversed(history[-12:]):
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        matches = re.findall(r"\b[A-Za-z]+-[0-9][A-Za-z0-9_.-]*\b", _visible_user_text(content))
        if matches:
            return matches[-1]
    for item in reversed(history[-12:]):
        content = item.get("content")
        if not isinstance(content, str):
            continue
        match = re.search(r"`([A-Za-z]+-[0-9][A-Za-z0-9_.-]*)`\s+(?:durum ozeti|icin)", content)
        if match:
            return match.group(1)
    return None


def _format_issue_discussion_response(paths: RuntimePaths, text: str) -> str:
    visible_text = _visible_user_text(text)
    selected_job_id = _selected_job_id_from_context(text)
    job_id = _extract_job_id(visible_text) or selected_job_id or _job_id_from_memory_context(text)
    if not job_id:
        return (
            "Evet, tespit ettigin hatalari buraya yazabilirsin. Ben bunlari tek tek ayirip hangi PDF, sayfa, poz ve "
            "ciktiyi etkiledigini belirlemeliyim; sonra aday/olcu duzeltmesi, yeniden uretim veya manuel inceleme "
            "karari verebiliriz. Hangi is uzerinde oldugumuzu da yazarsan daha net ilerlerim."
        )

    snapshot = _job_issue_snapshot(paths, job_id)
    if snapshot is None:
        return (
            f"Evet, `{job_id}` isi icin tespitlerini yazabilirsin; fakat bu is klasorunu su an okuyamadim. "
            "Lutfen hatayi PDF sayfasi, poz no ve beklenen/uretilen farki ile yaz."
        )

    page_count = snapshot.get("page_count")
    produced_count = snapshot.get("produced_count", 0)
    candidate_count = snapshot.get("candidate_count", 0)
    manual_review_count = snapshot.get("manual_review_count", 0)
    mismatch = isinstance(page_count, int) and page_count > produced_count
    issue_tags = _issue_tags(visible_text)
    known_pozs = _known_job_pozs(paths, job_id)
    affected_pozs = _extract_poz_numbers(visible_text, allowed_pozs=known_pozs)
    note_path = _append_manager_issue_note(paths, job_id, visible_text, tags=issue_tags, affected_pozs=affected_pozs)

    lines = [
        f"Evet, `{job_id}` icin gordugun hatalari benimle duzeltebiliriz.",
        "Bu noktada seni sadece ozetle gecmem yanlis olur; tespitlerini teknik karar maddelerine cevirmem gerekiyor.",
    ]
    if mismatch:
        lines.append(
            f"Ilk tespitin gecerli: PDF toplam {page_count} sayfa gorunuyor ama sistem su an {produced_count} poz uretmis "
            f"ve {candidate_count} aday kaydi var. Bu is teslim/partlist icin tamamlanmis kabul edilmemeli; eksik sayfa/poz incelemesi acilmali."
        )
    else:
        lines.append(
            f"Mevcut gorunen durum: PDF sayfa sayisi={page_count if page_count is not None else 'bilinmiyor'}, "
            f"uretilen poz={produced_count}, aday={candidate_count}, manuel inceleme={manual_review_count}."
        )
    if issue_tags:
        tag_text = ", ".join(issue_tags)
        poz_text = ", ".join(affected_pozs) if affected_pozs else "belirtilen pozlar"
        lines.append(
            f"Bu tespiti `{poz_text}` icin gorsel analiz/aday duzeltme notu olarak kaydettim: {tag_text}. "
            "Bu not DXF/NC1 yeniden uretiminden once kontur, pah/kose ve aday geometri kontrolunde dikkate alinmali."
        )
    if note_path:
        lines.append(f"Not dosyasi: {note_path}")
    if _looks_like_job_learning_request(visible_text):
        lines.extend(["", _format_job_learning_summary(paths, job_id)])
    lines.extend(
        [
            "Bana hatalari su formatta yaz: sayfa no, poz no varsa poz no, sistemin urettigi deger, olmasi gereken deger.",
            "Ben de her madde icin `aday duzelt`, `yeniden uret`, `manuel inceleme` veya `QC blokla` karariyla ilerletecegim.",
        ]
    )
    return "\n".join(lines)


def _job_issue_snapshot(paths: RuntimePaths, job_id: str) -> dict[str, Any] | None:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return None
    summary = _read_json_file(output_dir / "job_summary.json") or {}
    diagnostics = _read_json_file(output_dir / "pdf_diagnostics.json") or {}
    codex_candidates = _read_json_file(output_dir / "codex_candidates.json") or {}
    pdfs = diagnostics.get("pdfs") if isinstance(diagnostics, dict) else None
    page_count = None
    if isinstance(pdfs, list):
        counts = [item.get("page_count") for item in pdfs if isinstance(item, dict) and isinstance(item.get("page_count"), int)]
        page_count = sum(counts) if counts else None
    produced = summary.get("produced") if isinstance(summary, dict) else None
    manual_reviews = summary.get("manual_reviews") if isinstance(summary, dict) else None
    candidates = codex_candidates.get("candidates") if isinstance(codex_candidates, dict) else None
    return {
        "page_count": page_count,
        "produced_count": len(produced) if isinstance(produced, list) else 0,
        "manual_review_count": len(manual_reviews) if isinstance(manual_reviews, list) else 0,
        "candidate_count": len(candidates) if isinstance(candidates, list) else 0,
    }


def _format_job_learning_summary(paths: RuntimePaths, job_id: str) -> str:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return f"`{job_id}` isi bulunamadi; ogrenim ozeti cikaramadim."

    retrospective = _read_json_file(output_dir / "retrospective.json")
    summary = _read_json_file(output_dir / "job_summary.json") or {}
    notes = _read_open_manager_issue_notes(output_dir)
    known_pozs = _known_job_pozs(paths, job_id, summary)
    affected_pozs = _affected_pozs_from_notes(notes, allowed_pozs=known_pozs)
    produced = summary.get("produced") if isinstance(summary, dict) and isinstance(summary.get("produced"), list) else []

    lines = [f"`{job_id}` agent ogrenim ozeti:"]
    if isinstance(retrospective, dict):
        partlist = retrospective.get("partlist") if isinstance(retrospective.get("partlist"), dict) else {}
        calculated_metrics = retrospective.get("calculated_metrics") if isinstance(retrospective.get("calculated_metrics"), list) else []
        resolved = retrospective.get("resolved_blockages") if isinstance(retrospective.get("resolved_blockages"), list) else []
        learning = retrospective.get("learning") if isinstance(retrospective.get("learning"), list) else []
        lines.extend(
            [
                f"- Retrospektif: workspace/outputs/jobs/{job_id}/retrospective.json",
                f"- Uretilen poz: {retrospective.get('produced_count', len(produced))}",
                f"- Partlist: {'ok' if partlist.get('ok') else 'yok/basarisiz'}"
                + (f", satir={partlist.get('rows')}" if partlist else ""),
                f"- Otomatik hesaplanan metrik: {len(calculated_metrics)} poz",
            ]
        )
        if resolved:
            lines.append(f"- Cozulen blokaj kaydi: {len(resolved)}")
        if learning:
            lines.append("Tekrar kullanilabilir ogrenimler:")
            for item in learning[:8]:
                if not isinstance(item, dict):
                    continue
                agent = item.get("agent_or_skill") or "agent"
                proposal = item.get("proposal") or ""
                if proposal:
                    lines.append(f"- {agent}: {proposal}")
    else:
        partlist_exists = bool(list(output_dir.glob("*_partlist.xlsx")))
        backfillable = (isinstance(summary, dict) and summary.get("ok") is True) or partlist_exists
        lines.extend(
            [
                "- Retrospektif dosyasi henuz yok; bu is icin ogrenimler acik mudur notlari ve QC kayitlarindan okunuyor.",
                f"- Uretilen poz: {len(produced)}",
            ]
        )
        if backfillable:
            lines.append("- Bu is backfill edilebilir: retrospektif, vault ozeti, memory bridge ve skill proposal sonradan uretilebilir.")
        else:
            lines.append("- Bu is henuz backfill icin uygun gorunmuyor; once job summary veya partlist kaydi gerekir.")

    if affected_pozs:
        lines.append(f"- Acik geometri ogrenimi: {', '.join(affected_pozs)} pozlarinda pah/kose/poligon notlari korunmali.")
    elif notes:
        lines.append(f"- Acik mudur notu: {len(notes)}")
    lines.append("- Kural: Agent/skill dosyalari otomatik degismedi; ogrenimler once retrospektif ve skill proposal olarak kayda gecmeli.")
    return "\n".join(lines)


def _read_json_file(path: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _issue_tags(text: str) -> list[str]:
    lower = _normalize_turkish(text)
    tags: list[str] = []
    if "pah" in lower or "kose" in lower:
        tags.append("pah/kose eksigi")
    if "poligon" in lower:
        tags.append("poligon kontur")
    if "gorsel analiz" in lower or "gorselanaliz" in lower:
        tags.append("gorsel analiz notu")
    if "sayfa" in lower or "toplam" in lower:
        tags.append("eksik sayfa/poz")
    if "yapilmadi" in lower or "olmadi" in lower or "eksik" in lower:
        tags.append("eksik uretim")
    return list(dict.fromkeys(tags))


def _extract_poz_numbers(text: str, *, allowed_pozs: set[str] | None = None) -> list[str]:
    lower = _normalize_turkish(text)
    if not any(word in lower for word in ("poz", "eleman", "parca", "numara", "numarali", "numrala", "dxf")):
        return []
    candidates = list(dict.fromkeys(re.findall(r"\b[0-9]{3,6}\b", text)))
    if allowed_pozs:
        filtered = [value for value in candidates if value in allowed_pozs]
        if filtered:
            return filtered
    # Without a known poz list, avoid treating dimensions from 10x120 style text as poz numbers.
    return [
        value
        for value in candidates
        if not re.search(rf"(?:x|×)\s*{re.escape(value)}\b|\b{re.escape(value)}\s*(?:x|×)", text, re.IGNORECASE)
    ]


def _append_manager_issue_note(paths: RuntimePaths, job_id: str, message: str, *, tags: list[str], affected_pozs: list[str]) -> str | None:
    output_dir = paths.jobs_output_root / job_id
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "manager_issue_notes.jsonl"
        payload = {
            "created_at": _now_iso(),
            "job_id": job_id,
            "source": "teknik-ofis-muduru-chat",
            "message": message,
            "tags": tags,
            "affected_pozs": affected_pozs,
            "status": "open",
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return str(path.relative_to(paths.suite_root)).replace("\\", "/")
    except Exception:
        return None


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


def _extract_agent_mission(text: str) -> str | None:
    visible = _visible_user_text(text)
    lower = _normalize_turkish(visible)
    for pattern in (
        r"gorevi\s+(.+?)(?:\.|,|$)",
        r"misyonu\s+(.+?)(?:\.|,|$)",
        r"amaci\s+(.+?)(?:\.|,|$)",
        r"icin\s+(.+?)(?:\.|,|$)",
    ):
        m = re.search(pattern, lower, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if len(candidate) > 5:
                return candidate
    return None


def _extract_agent_title(text: str) -> str:
    cleaned = re.sub(r"\b(ajan|taslak|olustur|kur|hazirla)\b", " ", text, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:;-")
    return cleaned or "Yeni Teknik Ofis Ajani"


def _format_qc_response(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return _format_tool_error("QC raporu okunamadi", result)
    summary = result.get("summary", {})
    return (
        f"QC raporu: {result.get('path')}\n"
        f"Durum: ok={str(summary.get('ok')).lower()}\n"
        f"Poz: {summary.get('poz_no')}\n"
        f"Malzeme: {summary.get('material')}, kalinlik: {summary.get('thickness')} mm\n"
        f"Olcu: {summary.get('dimensions')}\n"
        f"Delik sayisi: {summary.get('hole_count')}\n"
        f"AutoCAD live kontrol: {summary.get('autocad_live_check')}"
    )


def _format_tool_error(prefix: str, result: dict[str, Any]) -> str:
    if _is_job_not_found_result(result):
        return _format_job_not_found_response(result)
    return f"{prefix}: {result.get('error')}"


def _format_summary_response(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return _format_tool_error("Is ozeti okunamadi", result)
    summary = result.get("summary", {})
    produced = summary.get("produced", [])
    manual_reviews = summary.get("manual_reviews", [])
    lines = [
        f"Is ozeti: {result.get('path')}",
        f"Durum: ok={str(summary.get('ok')).lower()}",
        f"Uretilen poz sayisi: {len(produced) if isinstance(produced, list) else 0}",
        f"Manuel inceleme: {len(manual_reviews) if isinstance(manual_reviews, list) else 0}",
    ]
    if isinstance(manual_reviews, list) and manual_reviews:
        lines.append("Manuel inceleme notlari:")
        for review in manual_reviews[:5]:
            if not isinstance(review, dict):
                continue
            detail = review.get("detail") or review.get("reason")
            source_pdf = review.get("source_pdf") or "bilinmeyen PDF"
            lines.append(f"- {source_pdf}: {review.get('reason')} - {detail}")
            next_action = review.get("next_action")
            if next_action:
                lines.append(f"  Mudur aksiyonu: {next_action}")
    return "\n".join(lines)


def _is_job_not_found_result(result: dict[str, Any]) -> bool:
    return isinstance(result, dict) and result.get("ok") is False and isinstance(result.get("available_jobs"), list)


def _format_job_not_found_response(result: dict[str, Any]) -> str:
    job_id = str(result.get("job_id") or "belirtilen is")
    jobs = result.get("available_jobs", [])
    ids = [str(job.get("job_id")) for job in jobs if isinstance(job, dict) and job.get("job_id")]
    available = ", ".join(f"`{job}`" for job in ids) if ids else "kayitli is yok"
    return f"`{job_id}` ID'li is bulunamadi.\nMevcut isler: {available}."


def _format_jobs_response(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"Isler listelenemedi: {result.get('error')}"
    jobs = result.get("jobs", [])
    if not isinstance(jobs, list) or not jobs:
        return "Izole workspace icinde kayitli is yok."
    lines = ["Mevcut isler:"]
    for job in jobs:
        if isinstance(job, dict):
            lines.append(f"- `{job.get('job_id')}`: PDF={job.get('pdf_count', 0)}, metadata={str(job.get('has_metadata')).lower()}")
    return "\n".join(lines)
