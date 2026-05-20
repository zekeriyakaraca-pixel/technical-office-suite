from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import structlog

from .agent_context import build_system_prompt, load_agent_context
from .codex_bridge import CodexBridge, CodexRunRequest
from .chat_detectors import (
    _CORNER_RELIEFS_QUESTION_MARKER,
    _MEMORY_CONTEXT_MARKER,
    _SELECTED_CONTEXT_MARKER,
    _apply_corner_reliefs_to_candidates,
    _candidates_needing_corner_reliefs,
    _candidates_needing_polygon_vertices,
    _completion_step_number,
    _completion_step_number_for_request,
    _corner_mentions,
    _corner_relief_suggestion_from_history,
    _corner_relief_suggestion_from_prompt,
    _corner_size_from_text,
    _dedupe_corner_reliefs,
    _extract_all_poz_nos_from_text,
    _extract_job_id,
    _extract_numeric_reference,
    _extract_poz_no,
    _extract_poz_no_from_text,
    _extract_project_name,
    _extract_target_agent_from_text,
    _format_corner_relief_ambiguity_response,
    _format_corner_relief_missing_detail_response,
    _format_corner_reliefs_question,
    _format_lightweight_manager_response,
    _format_polygon_vertices_question,
    _format_runtime_ready_response,
    _hidden_context_payload,
    _job_id_from_memory_context,
    _job_id_from_recent_history,
    _last_message_was_corner_reliefs_question,
    _looks_like_agent_creation_request,
    _looks_like_apply_manager_decision_request,
    _looks_like_approval_queue_request,
    _looks_like_learning_write_intent,
    _looks_like_bare_manager_action_confirmation,
    _looks_like_corner_relief_confirmation,
    _looks_like_corner_relief_meta_question,
    _looks_like_corner_reliefs_help_request,
    _looks_like_confirmed_job_reset,
    _looks_like_create_job_request,
    _looks_like_deep_output_inspection_request,
    _looks_like_guided_flow_cancel,
    _looks_like_hole_coordinate_correction_request,
    _looks_like_job_completion_continue_request,
    _looks_like_job_completion_step_request,
    _looks_like_job_learning_request,
    _looks_like_job_reference,
    _looks_like_job_restart_request,
    _looks_like_job_status_request,
    _looks_like_issue_discussion_request,
    _looks_like_lightweight_manager_chat,
    _looks_like_list_jobs_request,
    _looks_like_manager_action_confirmation,
    _looks_like_manual_review_detail_request,
    _looks_like_mark_column_position_hint_request,
    _looks_like_missing_candidate_extraction_request,
    _looks_like_page_exclusion_request,
    _looks_like_polygon_draw_instruction,
    _looks_like_position_info_resolution_request,
    _looks_like_poz_correction_action_request,
    _looks_like_run_request,
    _looks_like_runtime_ready_request,
    _looks_like_selected_job_reference,
    _looks_like_skill_promote_request,
    _looks_like_skill_update_request,
    _manager_flow_session_id,
    _merge_flow_pending,
    _normalize_turkish,
    _parse_corner_relief_segment,
    _parse_corner_reliefs_by_pending_candidate,
    _parse_corner_reliefs_from_text,
    _policy_from_text,
    _pozs_for_relief_rows,
    _relief_type_from_text,
    _selected_job_id_from_context,
    _short_text,
    _should_route_locally,
    _visible_user_text,
    _write_codex_candidates,
)
from .config import RuntimePaths, get_paths
from .approval_validation import annotate_candidate_qualities
from .gemini_bridge import GeminiBridge, get_gemini_bridge
from .guided_flows import (
    FLOW_CORNER_RELIEF,
    FLOW_MANAGER_ACTION_CONFIRMATION,
    GuidedFlowState,
    corner_relief_state,
    get_guided_flow_store,
    manager_action_state,
)
from .job_fsm import JobState, get_fsm
from .state_io import atomic_write_json, read_json
from .text_normalization import normalize_search_text, repair_text
from .tools import ToolRegistry
from .completion import append_job_event, backfill_job_learning, complete_approved_job, write_manual_learning_note
from .visual_evidence import render_microzoom_images, write_microzoom_manifest


MANAGER_CODEX_READ_TIMEOUT_SECONDS = 90
MANAGER_CODEX_WRITE_TIMEOUT_SECONDS = 180
MANAGER_VISUAL_CANDIDATE_MAX_PAGES = 80

log = structlog.get_logger(__name__)


@dataclass
class AgentRunResult:
    content: str
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    used_llm: bool = False
    fallback_reason: str | None = None


@dataclass
class _DispatchEntry:
    """Tek bir manager chat dispatch kuralı."""
    detector: Callable[[str], bool]
    method_name: str
    needs_session: bool = False
    text_only: bool = False  # handler yalnızca (text,) alır; history olmadan


_MANAGER_DISPATCH: list[_DispatchEntry] = [
    _DispatchEntry(_looks_like_job_restart_request,                "_handle_job_restart_request"),
    _DispatchEntry(_looks_like_apply_manager_decision_request,     "_handle_apply_manager_decisions"),
    _DispatchEntry(_looks_like_missing_candidate_extraction_request, "_handle_missing_candidate_extraction"),
    _DispatchEntry(_looks_like_poz_correction_action_request,      "_handle_poz_correction_action",    needs_session=True),
    _DispatchEntry(_looks_like_hole_coordinate_correction_request, "_handle_hole_coordinate_correction", needs_session=True),
    _DispatchEntry(_looks_like_job_completion_step_request,        "_handle_job_completion_step"),
    _DispatchEntry(_looks_like_job_completion_continue_request,    "_handle_job_completion_continue"),
    _DispatchEntry(_looks_like_position_info_resolution_request,   "_handle_position_info_resolution", needs_session=True),
    _DispatchEntry(_looks_like_page_exclusion_request,             "_handle_page_exclusion_request",   needs_session=True),
    _DispatchEntry(_looks_like_mark_column_position_hint_request,  "_handle_mark_column_position_hint",needs_session=True),
    _DispatchEntry(_looks_like_deep_output_inspection_request,     "_handle_deep_output_inspection"),
    _DispatchEntry(_looks_like_manual_review_detail_request,       "_handle_manual_review_detail_request"),
    _DispatchEntry(_looks_like_job_status_request,                 "_handle_job_status_request",       needs_session=True),
    _DispatchEntry(_looks_like_approval_queue_request,             "_handle_approval_queue_request"),
    _DispatchEntry(_looks_like_skill_promote_request,              "_handle_skill_promote_request",    text_only=True),
    _DispatchEntry(_looks_like_skill_update_request,               "_handle_skill_update_request",     needs_session=True),
]


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
            action_flow = self._open_manager_action_flow_state(
                session_id=session_id,
                selected_job_id=selected_job_id,
            )
            if action_flow is not None and _looks_like_guided_flow_cancel(text):
                get_guided_flow_store(self.paths.workspace_root).cancel(action_flow)
                return AgentRunResult(
                    content="Bekleyen mudur aksiyonunu iptal ettim. Herhangi bir dosya veya pipeline degisikligi yapilmadi.",
                    fallback_reason="local_manager_action_cancelled",
                )
            if action_flow is not None and _looks_like_manager_action_confirmation(text):
                return self._apply_manager_action_flow(action_flow, session_id=session_id)
            if action_flow is None and _looks_like_bare_manager_action_confirmation(text):
                return AgentRunResult(
                    content="Uygulanacak bekleyen mudur aksiyonu bulamadim. Once secili isin durumunu okuyup oneriyi netlestirmem gerekiyor.",
                    fallback_reason="local_manager_action_missing",
                )
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
        dispatched = self._run_dispatch(text, history, session_id)
        if dispatched is not None:
            return dispatched
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
            return self._handle_job_learning_request(text, history, session_id=session_id)
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
        except Exception as exc:
            log.warning("manager_recent_patterns_failed", error=str(exc))
        try:
            from .agent_context import load_expert_agent_memories
            _expert_mem = load_expert_agent_memories(self.paths)
            if _expert_mem:
                base_system += f"\n\n## Uzman Hafızaları ve Kuralları\n{_expert_mem}"
        except Exception as exc:
            log.warning("manager_expert_memories_failed", error=str(exc))
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
        except Exception as exc:
            log.warning("gemini_expert_memories_failed", error=str(exc))

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
        except Exception as exc:
            log.warning("gemini_recent_patterns_failed", error=str(exc))

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
        except Exception as exc:
            log.warning("gemini_bridge_lookup_failed", error=str(exc))
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
            "Teknik-ofis-müdürü olarak yukarıdaki sistem verisini yorumla ve kullanıcıya DOĞAL bir paragraf halinde yanıt ver.\n\n"
            "ZORUNLU FORMAT KURALLARI:\n"
            "- Madde listesi (-), numaralı liste, veya 'Anahtar: Değer' satırları KULLANMA.\n"
            "- Sistem verisini olduğu gibi kopyalama veya tekrarlama.\n"
            "- Akıcı Türkçe cümleler yaz; konuşur gibi, müdür gibi.\n"
            "- 2-4 cümle yeterli. Fazladan bilgi ekleme.\n"
            "- Sonraki adımı açık ve doğrudan belirt: 'Şunu yapıyorum' veya 'Şunu öneriyorum'.\n"
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

    def _run_dispatch(
        self,
        text: str,
        history: list[dict[str, str]],
        session_id: str | None,
    ) -> AgentRunResult | None:
        """_MANAGER_DISPATCH tablosunu sırayla çalıştırır.

        İlk eşleşen entry'nin handler'ını çağırıp sonucunu döner.
        Hiç eşleşme yoksa None döner; çağıran run() devam eder.
        """
        for entry in _MANAGER_DISPATCH:
            if entry.detector(text):
                handler = getattr(self, entry.method_name)
                if entry.text_only:
                    return handler(text)
                if entry.needs_session:
                    return handler(text, history, session_id=session_id)
                return handler(text, history)
        return None

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
                content="Hangi isi bastan baslatacagimi belirt. Ornek: `job-001 temiz baslat`.",
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

    def _open_manager_action_flow_state(
        self,
        *,
        session_id: str | None,
        selected_job_id: str | None,
    ) -> GuidedFlowState | None:
        sid = _manager_flow_session_id(session_id)
        store = get_guided_flow_store(self.paths.workspace_root)
        if selected_job_id:
            found = store.get_open(session_id=sid, job_id=selected_job_id, flow_type=FLOW_MANAGER_ACTION_CONFIRMATION)
            if found is not None:
                return found
        return store.get_open(session_id=sid, flow_type=FLOW_MANAGER_ACTION_CONFIRMATION)

    def _apply_manager_action_flow(
        self,
        flow_state: GuidedFlowState,
        *,
        session_id: str | None = None,
    ) -> AgentRunResult:
        store = get_guided_flow_store(self.paths.workspace_root)
        if flow_state.action_type == "approved_spec_repair_rerun":
            result = _apply_approved_spec_repair_and_rerun(
                self.paths,
                self.tools,
                flow_state.job_id,
                session_id=session_id or flow_state.session_id,
            )
            if result.get("ok") or result.get("error") in {"no_repair_needed", "repair_ambiguous"}:
                store.resolve(flow_state)
            content = _format_manager_action_apply_response(result)
            return AgentRunResult(
                content=content,
                tool_results=[{"tool": "approved_spec_repair_rerun", "result": result}],
                fallback_reason="local_manager_action_apply",
            )
        store.cancel(flow_state)
        return AgentRunResult(
            content=f"Bekleyen mudur aksiyonunu uygulayamadim: bilinmeyen aksiyon `{flow_state.action_type}`. Akisi kapattim.",
            fallback_reason="local_manager_action_unknown",
        )

    def _handle_job_status_request(
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
                content="Hangi isin durumunu okuyacagimi belirt. Ornek: `job-001 ne durumdayiz`.",
                fallback_reason="local_job_status",
            )
        proposal = _manager_failed_job_repair_proposal(self.paths, job_id)
        if proposal.get("actionable"):
            content = _format_manager_repair_confirmation_prompt(self.paths, job_id, proposal)
            get_guided_flow_store(self.paths.workspace_root).upsert(
                manager_action_state(
                    session_id=_manager_flow_session_id(session_id),
                    job_id=job_id,
                    action_type="approved_spec_repair_rerun",
                    action_payload={"expected_summary": proposal.get("summary", "")},
                    prompt=content,
                )
            )
            return AgentRunResult(
                content=content,
                tool_results=[{"tool": "propose_approved_spec_repair", "result": proposal}],
                fallback_reason="local_manager_action_confirmation",
            )
        if proposal.get("ambiguous"):
            return AgentRunResult(
                content=_format_manager_repair_ambiguous_response(self.paths, job_id, proposal),
                tool_results=[{"tool": "propose_approved_spec_repair", "result": proposal}],
                fallback_reason="local_manager_action_ambiguous",
            )
        raw = _format_job_status_response(self.paths, job_id)
        return self._synthesize_query_with_gemini(raw, text, history, fallback_reason="local_job_status")

    def _handle_approval_queue_request(self, text: str, history: list[dict[str, str]]) -> AgentRunResult:
        result = self.tools.run("list_jobs", {})
        jobs = result.get("jobs", []) if result.get("ok") else []
        waiting: list[dict] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = str(job.get("job_id") or "")
            fsm = str(job.get("fsm_state") or "")
            if fsm != "awaiting_approval":
                continue
            output_dir = self.paths.jobs_output_root / job_id
            codex_path = output_dir / "codex_candidates.json"
            data = read_json(codex_path, default={})
            candidate_count = len(data.get("candidates") or [])
            waiting.append({
                "job_id": job_id,
                "project": str(job.get("project_name") or job_id),
                "candidates": candidate_count,
            })
        if not waiting:
            raw = "Su anda onay bekleyen is yok. Tum isler tamamlanmis veya aktif durumda."
        else:
            lines = [f"Onay bekleyen isler ({len(waiting)} adet):"]
            for w in waiting:
                lines.append(f"- `{w['job_id']}` — {w['project']} ({w['candidates']} aday)")
            lines.append("\nHer is icin `<job_id> adaylari onayla` veya dashboard Adaylar panelini kullan.")
            raw = "\n".join(lines)
        return self._synthesize_query_with_gemini(raw, text, history, fallback_reason="local_approval_queue")

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

    def _handle_hole_coordinate_correction(
        self,
        text: str,
        history: list[dict[str, str]],
        *,
        session_id: str | None = None,
    ) -> AgentRunResult:
        visible_text = _visible_user_text(text)
        selected_job_id = _selected_job_id_from_context(text)
        job_id = selected_job_id or _job_id_from_memory_context(text) or _extract_job_id(visible_text)
        poz_no: str | None = None
        if job_id:
            known_pozs = _known_job_pozs(self.paths, job_id)
            poz_candidates = _extract_poz_numbers(visible_text, allowed_pozs=known_pozs)
            poz_no = poz_candidates[-1] if poz_candidates else None
        else:
            poz_candidates = _extract_poz_numbers(visible_text)
            poz_no = poz_candidates[-1] if poz_candidates else _extract_poz_no_from_text(visible_text)
            if poz_no:
                job_id = self._find_job_for_poz(poz_no)
        if not job_id:
            return AgentRunResult(
                content="Delik koordinati duzeltmesi icin ilgili isi bulamadim. Bir job sec veya job ID yaz.",
                fallback_reason="local_hole_coordinate_correction",
            )
        if not poz_no:
            known_pozs = _known_job_pozs(self.paths, job_id)
            poz_candidates = _extract_poz_numbers(visible_text, allowed_pozs=known_pozs)
            poz_no = poz_candidates[-1] if poz_candidates else _latest_referenced_poz_from_notes(self.paths, job_id, allowed_pozs=known_pozs)
        if not poz_no:
            return AgentRunResult(
                content=f"`{job_id}` icin hangi pozun delik koordinatini duzeltecegimi bulamadim. Ornek: `Poz 4042 alt delik X=85 Y=98.5 olmali`.",
                fallback_reason="local_hole_coordinate_correction",
            )
        row = _approved_row_for_poz(self.paths, job_id, poz_no)
        if row is None:
            return AgentRunResult(
                content=f"`{job_id}` Poz {poz_no} icin onayli spec satiri bulunamadi. Once aday onayi veya spec kaydi gerekiyor.",
                fallback_reason="local_hole_coordinate_correction",
            )
        correction = _parse_hole_coordinate_correction(visible_text, row)
        if correction is None:
            return AgentRunResult(
                content=(
                    f"`{job_id}` Poz {poz_no} icin delik koordinati talebini aldim; fakat hedef X/Y degerini net okuyamadim. "
                    "Net format: `alt delik X=85 Y=75 yerine X=85 Y=98.5 olmali`."
                ),
                fallback_reason="local_hole_coordinate_correction",
            )
        note_path = _append_manager_issue_note(
            self.paths,
            job_id,
            visible_text,
            tags=_issue_tags(visible_text),
            affected_pozs=[poz_no],
        )
        result = _apply_hole_coordinate_correction(
            self.paths,
            self.tools,
            job_id,
            poz_no,
            correction,
            session_id=session_id,
        )
        result["note_path"] = note_path
        return AgentRunResult(
            content=_format_hole_coordinate_correction_response(result),
            tool_results=[{"tool": "apply_hole_coordinate_correction", "result": result}],
            fallback_reason="local_hole_coordinate_correction",
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

    def _handle_job_learning_request(
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
                content="Hangi isin ogrenimlerini ozetleyecegimi bulamadim. Bir job ID sec veya yaz.",
                fallback_reason="local_job_learning",
            )
        write_intent = _looks_like_learning_write_intent(text)
        retro_path = self.paths.jobs_output_root / job_id / "retrospective.json"
        retro_missing = not retro_path.exists()
        if write_intent or retro_missing:
            result = backfill_job_learning(
                self.paths,
                job_id,
                dry_run=False,
                session_id=session_id or DEFAULT_MANAGER_SESSION_ID,
            )
            if result.get("eligible") is False:
                note_path = write_manual_learning_note(
                    self.paths, job_id, text, session_id=session_id
                )
                raw = _format_manual_note_written_response(job_id, note_path, self.paths)
            else:
                raw = _format_backfill_result_response(job_id, result)
            result_obj = self._synthesize_query_with_gemini(
                raw, text, history, fallback_reason="local_job_learning_write"
            )
            result_obj.tool_results = [{"tool": "backfill_job_learning", "result": result}]
            return result_obj
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
        polygon_pending = _candidates_needing_polygon_vertices(all_candidates)
        validation_errors = trigger.get("validation_errors") if isinstance(trigger.get("validation_errors"), list) else []
        polygon_blocked = any("polygon_vertices" in str(item.get("error") if isinstance(item, dict) else item) for item in validation_errors)
        if polygon_pending and (polygon_blocked or not pending):
            return AgentRunResult(
                content=_format_polygon_vertices_question(job_id, polygon_pending),
                fallback_reason="local_polygon_vertices_trigger",
            )
        if not pending:
            return AgentRunResult(
                content=f"`{job_id}` isinde kontur/kose bosaltma gerektiren eksik aday bulunamadi.",
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
                polygon_reliefs: dict[int, list[dict]] = {}
                for _poly_item in target_pending:
                    _existing_reliefs = [
                        r for r in _poly_item.get("corner_reliefs", [])
                        if isinstance(r, dict) and r.get("corner")
                    ]
                    polygon_reliefs[_poly_item["_row_index"]] = _existing_reliefs + [{"type": "polygon_contour"}]
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
        "\n\n## Yanıt Formatı (KESİNLİKLE UYULMASI ZORUNLU)\n"
        "- Madde listesi (-), numaralı liste (1. 2. 3.), veya 'Anahtar: Değer' formatını KULLANMA.\n"
        "- Sistem verisini, JSON'u veya ham teknik şablonu olduğu gibi kopyalayıp yapıştırma.\n"
        "- Akıcı Türkçe paragraf yaz: bir müdür iş arkadaşına konuşur gibi, doğal ve kısa.\n"
        "- Yanıt genellikle 2-4 cümle olsun. Sonraki adımı 'Yapıyorum:', 'Öneriyorum:' ile belirt.\n"
        "- Hata varsa: ne oldu, neden, şimdi ne yapılacak — üç şeyi tek akıcı cümlede ver.\n"
        "- 'iş nerede?' sorularında FSM durumunu ve bir sonraki aksiyon adımını konuşur gibi özetle.\n"
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



def _manager_failed_job_repair_proposal(paths: RuntimePaths, job_id: str) -> dict[str, Any]:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return {"actionable": False, "error": "job_not_found"}
    fsm = _read_json_file(output_dir / "fsm_state.json") or {}
    if not isinstance(fsm, dict) or fsm.get("state") != "failed":
        return {"actionable": False}
    repair = _repair_approved_spec_corner_reliefs(job_dir, apply=False)
    if repair.get("ambiguous"):
        return {"actionable": False, "ambiguous": True, **repair}
    if repair.get("changed"):
        return {"actionable": True, **repair}
    return {"actionable": False, **repair}


def _apply_approved_spec_repair_and_rerun(
    paths: RuntimePaths,
    tools: ToolRegistry,
    job_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    job_dir = paths.jobs_import_root / job_id
    output_dir = paths.jobs_output_root / job_id
    if not job_dir.exists():
        return {"ok": False, "job_id": job_id, "error": "job_not_found"}
    fsm = get_fsm(paths.jobs_output_root)
    if fsm.is_in_progress(job_id):
        return {"ok": False, "job_id": job_id, "error": "job_in_progress", "fsm_state": fsm.get_state(job_id).value}

    repair = _repair_approved_spec_corner_reliefs(job_dir, apply=True)
    if repair.get("ambiguous"):
        return {"ok": False, "job_id": job_id, "error": "repair_ambiguous", "repair": repair}
    if not repair.get("changed"):
        return {"ok": False, "job_id": job_id, "error": "no_repair_needed", "repair": repair}

    fsm.force_transition(job_id, JobState.PRODUCING, reason="manager_confirmed_approved_spec_repair")
    append_job_event(
        paths,
        job_id,
        "production_started",
        {"scope": "manager_action_confirmation", "repair_summary": repair.get("summary")},
    )
    pipeline = tools.run("run_autocad_job", {"job_id": job_id, "autocad_live_policy": "off"})
    summary = pipeline.get("summary") if isinstance(pipeline.get("summary"), dict) else (_read_json_file(output_dir / "job_summary.json") or {})
    if not pipeline.get("ok"):
        fsm.force_transition(job_id, JobState.FAILED, reason=str(pipeline.get("error") or "pipeline_failed"))
        append_job_event(paths, job_id, "failed", {"error": pipeline.get("error") or "pipeline_failed"})
        return {"ok": False, "job_id": job_id, "error": pipeline.get("error"), "repair": repair, "pipeline": pipeline}
    if isinstance(summary, dict) and summary.get("ok") is True:
        completion = complete_approved_job(
            paths,
            job_id,
            summary,
            approved_count=_approved_spec_row_count(job_dir),
            session_id=session_id,
        )
        return {
            "ok": bool(completion.get("ok")),
            "job_id": job_id,
            "repair": repair,
            "pipeline": pipeline,
            "summary": summary,
            "partlist": completion.get("partlist"),
            "retrospective": completion.get("retrospective"),
            "message": completion.get("message"),
            "error": completion.get("error"),
            "fsm_state": get_fsm(paths.jobs_output_root).get_state(job_id).value,
        }
    fsm.force_transition(job_id, JobState.AWAITING_APPROVAL, reason="manager_repair_manual_review_pending")
    return {
        "ok": False,
        "job_id": job_id,
        "error": "manual_review_or_qc_pending",
        "repair": repair,
        "pipeline": pipeline,
        "summary": summary,
        "fsm_state": get_fsm(paths.jobs_output_root).get_state(job_id).value,
    }


def _approved_spec_row_count(job_dir: Path) -> int | None:
    approved = _read_json_file(job_dir / "approved_plate_specs.json") or {}
    rows = approved.get("plates", approved) if isinstance(approved, dict) else approved
    return len(rows) if isinstance(rows, list) else None


def _format_manager_repair_confirmation_prompt(paths: RuntimePaths, job_id: str, proposal: dict[str, Any]) -> str:
    output_dir = paths.jobs_output_root / job_id
    fsm = _read_json_file(output_dir / "fsm_state.json") or {}
    details = _read_job_failure_details(output_dir, paths.jobs_import_root / job_id)
    lines = [
        f"`{job_id}` icin uygulanabilir bir mudur aksiyonu hazir.",
        "",
        "Neden:",
        f"- FSM: {fsm.get('state', 'failed') if isinstance(fsm, dict) else 'failed'}",
    ]
    if details:
        lines.extend(f"- {line.strip()}" for line in details[:3])
    lines.extend(
        [
            "",
            "Onerilen duzeltme:",
            f"- {proposal.get('summary')}",
        ]
    )
    for change in (proposal.get("changes") or [])[:5]:
        if isinstance(change, dict):
            lines.append(f"- Satir {change.get('row')} ({change.get('poz_no', '?')}): {change.get('detail')}")
    if len(proposal.get("changes") or []) > 5:
        lines.append(f"- ... ve {len(proposal.get('changes') or []) - 5} duzeltme daha.")
    lines.extend(
        [
            "",
            "Onay verirsen uygulayacagim:",
            "- approved_plate_specs.json sadece bu duzeltmelerle guncellenecek.",
            "- Pipeline yeniden calisacak; QC ok olursa partlist ve kapanis zinciri tamamlanacak.",
            "- Uygulamak icin `yap`, vazgecmek icin `iptal et` yaz.",
        ]
    )
    return "\n".join(lines)


def _format_manager_repair_ambiguous_response(paths: RuntimePaths, job_id: str, proposal: dict[str, Any]) -> str:
    output_dir = paths.jobs_output_root / job_id
    details = _read_job_failure_details(output_dir, paths.jobs_import_root / job_id)
    lines = [
        f"`{job_id}` icin otomatik uygulanacak guvenli duzeltme cikaramadim.",
        "",
        "Neden:",
    ]
    if details:
        lines.extend(f"- {line.strip()}" for line in details[:3])
    for item in proposal.get("ambiguous_items") or []:
        if isinstance(item, dict):
            lines.append(f"- Satir {item.get('row')} ({item.get('poz_no', '?')}), {item.get('corner')}: {item.get('detail')}")
    lines.extend(
        [
            "",
            "Onerilen duzeltme:",
            "- Bu kayit icin radius/offset yorumu belirsiz. PDF/evidence netlesmeden dosya degistirmeyecegim.",
            "",
            "Onay verirsen uygulayacagim:",
            "- Net kose tipi ve olcuyu yazarsan sadece o pozu duzeltip yeniden uretimi baslatacagim.",
        ]
    )
    return "\n".join(lines)


def _format_manager_action_apply_response(result: dict[str, Any]) -> str:
    job_id = str(result.get("job_id") or "")
    if not result.get("ok"):
        return (
            f"`{job_id}` aksiyonu tamamlanamadi.\n"
            f"- Hata: {result.get('error') or 'bilinmeyen hata'}\n"
            f"- FSM: {result.get('fsm_state') or 'bilinmiyor'}"
        )
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    produced = len(summary.get("produced") or []) if isinstance(summary, dict) else 0
    manual = len(summary.get("manual_reviews") or []) if isinstance(summary, dict) else 0
    partlist = result.get("partlist") if isinstance(result.get("partlist"), dict) else {}
    return "\n".join(
        [
            f"`{job_id}` aksiyonunu uyguladim.",
            f"- FSM: {result.get('fsm_state') or 'completed'}",
            f"- Uretilen poz: {produced}",
            f"- QC: {'ok' if summary.get('ok') is True else summary.get('ok')}",
            f"- Manuel inceleme: {manual}",
            f"- Partlist: {partlist.get('path') or 'yok'}",
        ]
    )


def _repair_approved_spec_corner_reliefs(job_dir: Path, *, apply: bool) -> dict[str, Any]:
    import json as _json

    valid_relief_types = {"round", "cugul", "chamfer", "pah"}
    valid_corners = {"bottom_left", "bottom_right", "top_left", "top_right"}
    spec_path = job_dir / "approved_plate_specs.json"
    if not spec_path.exists():
        return {"ok": False, "changed": False, "error": "approved_specs_missing"}
    try:
        data = _json.loads(spec_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("approved_spec_repair_read_failed", path=str(spec_path), error=str(exc))
        return {"ok": False, "changed": False, "error": f"read_failed: {exc}"}
    rows = data.get("plates", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return {"ok": False, "changed": False, "error": "approved_specs_invalid_shape"}

    changes: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    counters = {"removed": 0, "fixed": 0, "large_offset_removed": 0, "equal_chamfer": 0}
    changed_rows = 0
    for row_number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        reliefs = row.get("corner_reliefs")
        if not isinstance(reliefs, list):
            continue
        clean: list[dict[str, Any]] = []
        seen_corners: set[str] = set()
        row_changed = False
        for relief_number, original in enumerate(reliefs, start=1):
            if not isinstance(original, dict):
                counters["removed"] += 1
                row_changed = True
                changes.append(_repair_change(row_number, row, "remove_invalid_relief", f"corner relief {relief_number} is not an object"))
                continue
            relief = dict(original)
            raw_type = str(relief.get("relief_type") or relief.get("type") or "")
            normalized_type = _approved_repair_relief_type(raw_type)
            if normalized_type == "polygon_contour" or not relief.get("corner"):
                counters["removed"] += 1
                row_changed = True
                changes.append(_repair_change(row_number, row, "remove_sentinel_or_missing_corner", f"corner relief {relief_number} ignored"))
                continue
            raw_corner = str(relief.get("corner") or "")
            normalized_corner = _approved_repair_corner(raw_corner)
            if normalized_corner not in valid_corners:
                counters["removed"] += 1
                row_changed = True
                changes.append(_repair_change(row_number, row, "remove_unknown_corner", f"{raw_corner!r} is not a supported corner"))
                continue
            if normalized_corner in seen_corners:
                counters["removed"] += 1
                row_changed = True
                changes.append(_repair_change(row_number, row, "remove_duplicate_corner", f"{normalized_corner} duplicate removed"))
                continue
            seen_corners.add(normalized_corner)
            if normalized_corner != raw_corner:
                relief["corner"] = normalized_corner
                counters["fixed"] += 1
                row_changed = True
                changes.append(_repair_change(row_number, row, "normalize_corner", f"{raw_corner} -> {normalized_corner}"))
            if normalized_type not in valid_relief_types:
                relief["relief_type"] = "round"
                counters["fixed"] += 1
                row_changed = True
                changes.append(_repair_change(row_number, row, "normalize_relief_type", f"{raw_type!r} -> round"))
            elif normalized_type != raw_type.strip().lower():
                relief["relief_type"] = normalized_type
                counters["fixed"] += 1
                row_changed = True
                changes.append(_repair_change(row_number, row, "normalize_relief_type", f"{raw_type!r} -> {normalized_type}"))
            if "type" in relief and "relief_type" not in relief:
                relief["relief_type"] = normalized_type

            chamfer_size = _equal_chamfer_size_from_evidence(row)
            if chamfer_size is not None and relief.get("relief_type") == "round":
                radius = _float_or_none(relief.get("radius"))
                if radius is None or abs(radius - chamfer_size) <= 1e-6:
                    relief["relief_type"] = "chamfer"
                    relief["radius"] = chamfer_size
                    relief["x_offset"] = chamfer_size
                    relief["y_offset"] = chamfer_size
                    counters["equal_chamfer"] += 1
                    row_changed = True
                    changes.append(_repair_change(row_number, row, "equal_size_chamfer", f"{chamfer_size:g}x{chamfer_size:g} treated as chamfer"))

            too_large_detail = _relief_too_large_detail(row, relief)
            if too_large_detail:
                if _evidence_says_offset_not_radius(row, relief):
                    counters["large_offset_removed"] += 1
                    row_changed = True
                    _append_repair_note(row, f"manager_repair_removed_non_relief_offset:{normalized_corner}:{too_large_detail}")
                    changes.append(_repair_change(row_number, row, "remove_non_relief_offset", f"{normalized_corner} {too_large_detail}"))
                    continue
                ambiguous.append(
                    {
                        "row": row_number,
                        "poz_no": row.get("poz_no"),
                        "corner": normalized_corner,
                        "detail": too_large_detail,
                    }
                )
            clean.append(relief)
        if row_changed or len(clean) != len(reliefs):
            row["corner_reliefs"] = clean
            changed_rows += 1

    summary = _format_approved_spec_repair_summary(
        changed_rows,
        counters["removed"],
        counters["fixed"],
        counters["large_offset_removed"],
        counters["equal_chamfer"],
    )
    if ambiguous:
        return {
            "ok": False,
            "changed": False,
            "ambiguous": True,
            "ambiguous_items": ambiguous,
            "changes": changes,
            "summary": summary,
        }
    changed = any(counters.values())
    if changed and apply:
        if isinstance(data, dict):
            data["plates"] = rows
        atomic_write_json(spec_path, data)
    return {
        "ok": True,
        "changed": changed,
        "summary": summary if changed else "approved_plate_specs.json zaten temiz",
        "changes": changes,
        "changed_rows": changed_rows,
    }


def _approved_repair_relief_type(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"pah", "bevel", "beveled", "chamfered"}:
        return "chamfer"
    if normalized in {"round_relief", "rounded", "radius"}:
        return "round"
    return normalized


def _approved_repair_corner(value: str) -> str:
    normalized = value.strip().lower()
    for base in ("bottom_left", "bottom_right", "top_left", "top_right"):
        if normalized == base or normalized.startswith(base + "_"):
            return base
    aliases = {
        "bl": "bottom_left",
        "br": "bottom_right",
        "tl": "top_left",
        "tr": "top_right",
        "sol_alt": "bottom_left",
        "sag_alt": "bottom_right",
        "sol_ust": "top_left",
        "sag_ust": "top_right",
    }
    return aliases.get(normalized, normalized)


def _repair_change(row_number: int, row: dict[str, Any], kind: str, detail: str) -> dict[str, Any]:
    return {"row": row_number, "poz_no": row.get("poz_no"), "kind": kind, "detail": detail}


def _format_approved_spec_repair_summary(
    changed_rows: int,
    total_removed: int,
    total_fixed: int,
    total_large_offset_removed: int,
    total_equal_chamfers: int,
) -> str:
    parts = []
    if total_removed:
        parts.append(f"{total_removed} gecersiz/sentinel kayit kaldirildi")
    if total_fixed:
        parts.append(f"{total_fixed} tip/kose normalize edildi")
    if total_large_offset_removed:
        parts.append(f"{total_large_offset_removed} radius olmayan offset relief kaldirildi")
    if total_equal_chamfers:
        parts.append(f"{total_equal_chamfers} esit olculu pah/chamfer olarak isaretlendi")
    if not parts:
        return "approved_plate_specs.json zaten temiz"
    return f"{changed_rows} satirda: {', '.join(parts)}"


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _relief_too_large_detail(row: dict[str, Any], relief: dict[str, Any]) -> str | None:
    width = _float_or_none(row.get("width"))
    height = _float_or_none(row.get("height"))
    radius = _float_or_none(relief.get("radius"))
    if width is None or height is None or radius is None:
        return None
    x_offset = _float_or_none(relief.get("x_offset")) or radius
    y_offset = _float_or_none(relief.get("y_offset")) or radius
    if x_offset >= width or y_offset >= height:
        return f"x_offset={x_offset:g}, y_offset={y_offset:g}, plate={width:g}x{height:g}"
    return None


def _equal_chamfer_size_from_evidence(row: dict[str, Any]) -> float | None:
    evidence = _normalize_turkish(str(row.get("evidence") or ""))
    if not evidence or re.search(r"\br\s*\d", evidence):
        return None
    matches = re.findall(r"\b(\d+(?:[\.,]\d+)?)\s*[xX]\s*(\d+(?:[\.,]\d+)?)\b", evidence)
    for left, right in matches:
        left_value = _float_or_none(left)
        right_value = _float_or_none(right)
        if left_value is not None and right_value is not None and abs(left_value - right_value) <= 1e-6:
            return left_value
    return None


def _evidence_says_offset_not_radius(row: dict[str, Any], relief: dict[str, Any]) -> bool:
    evidence = _normalize_turkish(str(row.get("evidence") or ""))
    radius = _float_or_none(relief.get("radius"))
    if not evidence:
        return False
    if radius is not None and re.search(rf"\br\s*{re.escape(f'{radius:g}')}\b", evidence):
        return False
    return any(
        word in evidence
        for word in (
            "offset",
            "upstand",
            "top view",
            "ust gorunus",
            "ust gorun",
            "bukum",
            "bending",
            "flat length",
            "middle",
            "orta",
        )
    )


def _append_repair_note(row: dict[str, Any], note: str) -> None:
    notes = row.get("notes")
    if not isinstance(notes, list):
        notes = []
    if note not in notes:
        notes.append(note)
    row["notes"] = notes


def _try_patch_approved_spec_corner_reliefs(job_dir: Path) -> str | None:
    result = _repair_approved_spec_corner_reliefs(job_dir, apply=True)
    if not result.get("changed"):
        return None
    return str(result.get("summary") or "approved specs repaired")
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
    except Exception as exc:
        log.warning("approved_spec_patch_read_failed", path=str(spec_path), error=str(exc))
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
    atomic_write_json(spec_path, data)
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
    open_notes = len(_actionable_open_manager_issue_notes(paths, job_id))

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
            "  - Mudur guvenli bir spec tamiri bulursa once onay ister; `yap` dersen uygular ve pipeline'i yeniden baslatir.\n"
            "  - `approved_plate_specs.json` silinmis is icin: adaylari yeniden onayla.\n"
            "  - Tam sifirlama gerekiyorsa: `temiz baslat` veya `sifirdan baslat` yaz."
        )
    elif open_notes:
        lines.append(
            "Karar: Acik mudur hata notu varken bu is teslim/partlist icin tamamlanmis kabul edilmemeli. "
            "Once nottaki beklenen/uretilen farklari netlestirip aday/QC duzeltmesi yapilmali."
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
        except Exception as exc:
            log.warning("approved_spec_failure_detail_failed", job_id=job_dir.name, error=str(exc))

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
                except (json.JSONDecodeError, ValueError):
                    continue
                event_type = str(evt.get("type") or evt.get("event") or "")
                payload = evt.get("payload") or {}
                if event_type in ("failed", "job_blocked", "production_failed", "qc_failed") or (
                    isinstance(payload, dict) and payload.get("ok") is False
                ):
                    reason = payload.get("reason") or payload.get("error") or evt.get("reason") or event_type
                    details.append(f"Son hata olayi ({event_type}): {reason}")
                    break
        except Exception as exc:
            log.warning("events_failure_detail_failed", job_id=job_dir.name, error=str(exc))

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
        except Exception as exc:
            log.warning("summary_failure_detail_failed", job_id=job_dir.name, error=str(exc))

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
        "hata bildirimi",
        "delik koordinati",
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
    except Exception as exc:
        log.warning("page_exclusion_memory_record_failed", job_id=job_id, error=str(exc))
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
    atomic_write_json(path, {"schema_version": 1, "excluded_pages": exclusions})
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

    # If Codex already extracted visual candidates, the deterministic pipeline
    # will fail again (no text layer). Return early and direct manager to approve.
    codex_candidates_path = output_dir / "codex_candidates.json"
    existing_codex = _read_json_file(codex_candidates_path)
    if (
        isinstance(existing_codex, dict)
        and isinstance(existing_codex.get("candidates"), list)
        and len(existing_codex["candidates"]) > 0
    ):
        fsm.force_transition(job_id, JobState.AWAITING_APPROVAL, reason="manager_mark_column_hint_codex_candidates_exist")
        append_job_event(
            paths,
            job_id,
            "position_hint_noted",
            {
                "scope": "manager_mark_column_position_hint",
                "position_hints_path": _relative_to_suite(paths, hints_path),
                "codex_candidates_count": len(existing_codex["candidates"]),
                "note": "Codex gorsel adaylari mevcut; deterministic pipeline yeniden calistirilmadi.",
            },
        )
        candidates = existing_codex["candidates"]
        return {
            "ok": False,
            "job_id": job_id,
            "position_hints_path": _relative_to_suite(paths, hints_path),
            "resolved_note_count": resolved_notes,
            "pipeline_ok": False,
            "summary_ok": None,
            "manual_reviews": [],
            "manual_review_count": 0,
            "reviews_with_poz_count": len([c for c in candidates if c.get("poz_no")]),
            "poz_not_found_count": 0,
            "produced_count": 0,
            "fsm_state": JobState.AWAITING_APPROVAL.value,
            "codex_candidates_exist": True,
            "codex_candidates_count": len(candidates),
            "codex_poz_numbers": [c.get("poz_no") for c in candidates if c.get("poz_no")],
            "partlist": None,
            "retrospective": None,
            "message": (
                f"Codex gorsel analizi zaten {len(candidates)} aday buldu "
                f"(poz: {', '.join(c.get('poz_no','?') for c in candidates if c.get('poz_no'))}). "
                "PDF metin katmani olmadigi icin deterministic pipeline yeniden calistirilmadi. "
                "Dashboard Adaylar panelinden adaylari inceleyip 'Secili Adaylari Onayla' butonunu kullanin."
            ),
            "error": None,
        }

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
    if result.get("codex_candidates_exist"):
        poz_list = ", ".join(result.get("codex_poz_numbers") or []) or "?"
        return (
            f"`{job_id}` icin Mark sutunu poz bilgisi kaydedildi.\n"
            f"- Codex gorsel analizi zaten {result.get('codex_candidates_count', 0)} aday buldu (poz: {poz_list}).\n"
            f"- PDF metin katmani olmadigi icin pipeline yeniden calistirilmadi — bu dogru davranis.\n"
            f"- Kapatilan mudur notu: {result.get('resolved_note_count', 0)}\n"
            f"- FSM: {result.get('fsm_state')}\n\n"
            "**Sonraki adim:** Dashboard > 123 isi > Adaylar paneli > adaylari kontrol edip "
            "'Secili Adaylari Onayla' butonuna basin. Onay sonrasi DXF/NC1 uretimi baslar."
        )
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
    atomic_write_json(approved_path, approved)

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


def _parse_hole_coordinate_correction(text: str, row: dict[str, Any]) -> dict[str, Any] | None:
    pairs = _coordinate_pairs_from_text(text)
    if not pairs:
        return None
    target_x, target_y = pairs[-1]
    previous_x: float | None = None
    previous_y: float | None = None
    if len(pairs) >= 2:
        previous_x, previous_y = pairs[-2]
    width = _float_or_zero(row.get("width"))
    height = _float_or_zero(row.get("height"))
    if width > 0 and not (0 <= target_x <= width):
        return None
    if height > 0 and not (0 <= target_y <= height):
        return None
    selector = _hole_selector_from_text(text)
    return {
        "previous_x": previous_x,
        "previous_y": previous_y,
        "x": target_x,
        "y": target_y,
        "selector": selector,
        "source_text": _short_text(text, 260),
    }


def _coordinate_pairs_from_text(text: str) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    pattern = re.compile(
        r"\bx\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:[,;/\s]+)\s*y\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        pairs.append((_parse_decimal(match.group(1)), _parse_decimal(match.group(2))))
    return pairs


def _parse_decimal(value: str) -> float:
    return float(str(value).replace(",", "."))


def _hole_selector_from_text(text: str) -> str | None:
    lower = _normalize_turkish(text)
    if "alt delik" in lower or "lower hole" in lower:
        return "lower"
    if "ust delik" in lower or "upper hole" in lower:
        return "upper"
    if "sol delik" in lower or "left hole" in lower:
        return "left"
    if "sag delik" in lower or "right hole" in lower:
        return "right"
    return None


def _select_hole_index(holes: list[Any], correction: dict[str, Any]) -> int | None:
    hole_rows = [hole for hole in holes if isinstance(hole, dict)]
    if not hole_rows:
        return None
    previous_x = correction.get("previous_x")
    previous_y = correction.get("previous_y")
    if previous_x is not None and previous_y is not None:
        distances = [
            (
                index,
                abs(_float_or_zero(hole.get("x")) - float(previous_x))
                + abs(_float_or_zero(hole.get("y")) - float(previous_y)),
            )
            for index, hole in enumerate(holes)
            if isinstance(hole, dict)
        ]
        if distances:
            index, distance = min(distances, key=lambda item: item[1])
            if distance <= 5.0:
                return index
    selector = correction.get("selector")
    if selector == "lower":
        return min(range(len(hole_rows)), key=lambda i: _float_or_zero(hole_rows[i].get("y")))
    if selector == "upper":
        return max(range(len(hole_rows)), key=lambda i: _float_or_zero(hole_rows[i].get("y")))
    if selector == "left":
        return min(range(len(hole_rows)), key=lambda i: _float_or_zero(hole_rows[i].get("x")))
    if selector == "right":
        return max(range(len(hole_rows)), key=lambda i: _float_or_zero(hole_rows[i].get("x")))
    target_x = correction.get("x")
    if target_x is not None:
        close = [
            index
            for index, hole in enumerate(holes)
            if isinstance(hole, dict) and abs(_float_or_zero(hole.get("x")) - float(target_x)) <= 2.0
        ]
        if len(close) == 1:
            return close[0]
    if len(hole_rows) == 1:
        return 0
    return None


def _apply_hole_coordinate_correction(
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
    holes = target.get("holes")
    if not isinstance(holes, list) or not holes:
        return {"ok": False, "job_id": job_id, "poz_no": poz_no, "error": "holes_missing"}
    hole_index = _select_hole_index(holes, correction)
    if hole_index is None or not isinstance(holes[hole_index], dict):
        return {"ok": False, "job_id": job_id, "poz_no": poz_no, "error": "hole_not_identified"}

    hole = holes[hole_index]
    before = {
        "x": _float_or_zero(hole.get("x")),
        "y": _float_or_zero(hole.get("y")),
        "diameter": _float_or_zero(hole.get("diameter")),
    }
    hole["x"] = float(correction["x"])
    hole["y"] = float(correction["y"])
    notes = target.get("notes")
    if not isinstance(notes, list):
        notes = []
    note_text = (
        f"manager_corrected_hole_{hole_index + 1}:"
        f"{before['x']:g},{before['y']:g}->"
        f"{float(correction['x']):g},{float(correction['y']):g}"
    )
    if note_text not in notes:
        notes.append(note_text)
    target["notes"] = notes
    atomic_write_json(approved_path, approved)

    resolved_notes = _resolve_hole_coordinate_notes(paths, job_id, poz_no)
    fsm = get_fsm(paths.jobs_output_root)
    fsm.force_transition(job_id, JobState.PRODUCING, reason="manager_hole_coordinate_correction")
    append_job_event(
        paths,
        job_id,
        "production_started",
        {
            "scope": "hole_coordinate_correction",
            "poz_no": poz_no,
            "hole_index": hole_index,
            "before": before,
            "after": {"x": correction["x"], "y": correction["y"], "diameter": before["diameter"]},
        },
    )
    pipeline = tools.run("run_autocad_job", {"job_id": job_id, "autocad_live_policy": "off"})
    summary = pipeline.get("summary") if isinstance(pipeline.get("summary"), dict) else (_read_json_file(output_dir / "job_summary.json") or {})
    remaining_open_notes = _actionable_open_manager_issue_notes(paths, job_id)
    completion: dict[str, Any] | None = None
    if pipeline.get("ok") and isinstance(summary, dict) and summary.get("ok") is True and not remaining_open_notes:
        completion = complete_approved_job(
            paths,
            job_id,
            summary,
            approved_count=len(rows),
            session_id=session_id,
        )
    else:
        fsm.force_transition(job_id, JobState.AWAITING_APPROVAL, reason="manager_hole_coordinate_correction_review")

    qc = _read_json_file(output_dir / poz_no / f"{poz_no}_qc.json") or {}
    return {
        "ok": bool(pipeline.get("ok")),
        "completion_ok": bool(completion and completion.get("ok")),
        "job_id": job_id,
        "poz_no": poz_no,
        "hole_index": hole_index,
        "before": before,
        "after": {"x": correction["x"], "y": correction["y"], "diameter": before["diameter"]},
        "resolved_note_count": resolved_notes,
        "open_note_count": len(remaining_open_notes),
        "pipeline_ok": bool(pipeline.get("ok")),
        "summary_ok": summary.get("ok") if isinstance(summary, dict) else None,
        "qc_ok": qc.get("ok") if isinstance(qc, dict) else None,
        "fsm_state": get_fsm(paths.jobs_output_root).get_state(job_id).value,
        "partlist": completion.get("partlist") if isinstance(completion, dict) else None,
        "message": completion.get("message") if isinstance(completion, dict) else None,
        "error": None if pipeline.get("ok") else pipeline.get("error"),
    }


def _format_hole_coordinate_correction_response(result: dict[str, Any]) -> str:
    job_id = str(result.get("job_id") or "")
    poz_no = str(result.get("poz_no") or "")
    if not result.get("ok"):
        return f"`{job_id}` Poz {poz_no} delik koordinati duzeltmesi uygulanamadi: {result.get('error')}"
    before = result.get("before") if isinstance(result.get("before"), dict) else {}
    after = result.get("after") if isinstance(result.get("after"), dict) else {}
    lines = [
        f"`{job_id}` Poz {poz_no} icin delik koordinati duzeltmesini uyguladim.",
        (
            f"- Delik #{int(result.get('hole_index', 0)) + 1}: "
            f"X={float(before.get('x') or 0):g} Y={float(before.get('y') or 0):g} -> "
            f"X={float(after.get('x') or 0):g} Y={float(after.get('y') or 0):g}"
        ),
        f"- Yeniden uretim/QC: ok={str(result.get('summary_ok')).lower()}, poz QC={str(result.get('qc_ok')).lower()}",
        f"- FSM: {result.get('fsm_state')}",
        f"- Kapatilan delik notu: {result.get('resolved_note_count', 0)}",
    ]
    if result.get("open_note_count"):
        lines.append(f"- Acik mudur notu: {result.get('open_note_count')} (is tamamlanmis kabul edilmemeli)")
    partlist = result.get("partlist")
    if isinstance(partlist, dict) and partlist.get("path"):
        lines.append(f"- Partlist guncellendi: {partlist.get('path')}")
    if result.get("message"):
        lines.append("")
        lines.append(str(result["message"]))
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


def _resolve_hole_coordinate_notes(paths: RuntimePaths, job_id: str, poz_no: str) -> int:
    output_dir = paths.jobs_output_root / job_id
    path = output_dir / "manager_issue_notes.jsonl"
    if not path.exists():
        return 0
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
        affected = [str(value) for value in note.get("affected_pozs", [])] if isinstance(note.get("affected_pozs"), list) else []
        message = _normalize_turkish(str(note.get("message") or ""))
        if poz_no in affected and ("delik koordinati" in tags or "delik" in message or "hole" in message):
            note = dict(note)
            note["status"] = "resolved"
            note["resolved_at"] = _now_iso()
            note["resolved_by"] = "teknik-ofis-muduru"
            note["resolution"] = "approved_plate_specs holes koordinati guncellenerek yeniden uretildi"
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
    geometry_tags = {"pah/kose eksigi", "poligon kontur", "gorsel analiz notu", "delik koordinati"}
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
            "microzoom_manifest_path": _relative_to_suite(paths, item["microzoom_manifest_path"]) if item.get("microzoom_manifest_path") else None,
            "evidence_images": [
                _relative_to_suite(paths, Path(image_path))
                for image_path in item.get("evidence_images", [])
            ],
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

    # Diagnostic hints: total_vector_circles per PDF → Codex'in delikleri kaçırmaması için
    diagnostic_hints: dict[str, int] = {}
    for pdf_diag in (diagnostics.get("pdfs") or []):
        circles = int(pdf_diag.get("total_vector_circles") or 0)
        if circles:
            diagnostic_hints[str(pdf_diag.get("source_pdf", ""))] = circles

    schema_path = _visual_candidate_schema_path(paths)
    prompt = _missing_candidate_prompt(paths, job_id, rendered, existing_candidates, diagnostic_hints=diagnostic_hints or None)
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
    rendered_evidence = _evidence_by_page(rendered)
    new_candidates = [
        _normalize_visual_candidate(
            item,
            index,
            provider="manager_pdf_scan",
            allowed_pdf_names=allowed_pdf_names,
            evidence_by_page=rendered_evidence,
            paths=paths,
        )
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

    # Cross-check: expected holes (from vector circles) vs extracted holes
    if diagnostic_hints:
        for _hint_pdf, _expected_circles in diagnostic_hints.items():
            _extracted_holes = sum(
                len(c.get("holes") or [])
                for c in new_candidates
                if str(c.get("source_pdf", "")) == _hint_pdf
            )
            if _expected_circles > 0 and _extracted_holes == 0:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "job=%s pdf=%s expected_holes=%d extracted=0 — delik kacirimi olmasi muhtemel",
                    job_id, _hint_pdf, _expected_circles,
                )

    merged = annotate_candidate_qualities(
        [item for item in merged if isinstance(item, dict)],
        paths,
        job_id,
        extraction_status="extracted",
        refinement_attempted=False,
    )
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
    run_dir = paths.suite_root / ".state" / "codex-runs" / run_id
    pages_dir = run_dir / "pages"
    microzoom_dir = run_dir / "microzoom"
    pages_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[dict[str, Any]] = []
    full_page_images: list[dict[str, Any]] = []
    evidence_images: list[dict[str, Any]] = []
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
                full_page_images.append(
                    {
                        "source_pdf": source_pdf,
                        "source_page": page_number,
                        "role": "full_page",
                        "path": str(target),
                        "width_px": int(getattr(pix, "width", 0) or 0),
                        "height_px": int(getattr(pix, "height", 0) or 0),
                    }
                )
                page_evidence = render_microzoom_images(
                    page,
                    fitz,
                    microzoom_dir,
                    source_pdf=source_pdf,
                    source_page=page_number,
                )
                evidence_images.extend(page_evidence)
                rendered.append(
                    {
                        "source_pdf": source_pdf,
                        "source_page": page_number,
                        "image": target,
                        "evidence_images": [item["path"] for item in page_evidence],
                    }
                )
        finally:
            close = getattr(doc, "close", None)
            if callable(close):
                close()
    if rendered:
        manifest_path = write_microzoom_manifest(
            run_dir,
            job_id=job_id,
            full_page_images=full_page_images,
            evidence_images=evidence_images,
        )
        for item in rendered:
            item["microzoom_manifest_path"] = manifest_path
    return rendered


def _load_autocad_uzman_skill_context(paths: RuntimePaths) -> str:
    """autocad-uzman-1 AGENT.md ve PDF okuma/geometri cıkarma skill dosyalarını yükler."""
    agents_root = paths.suite_root / "agents"
    parts: list[str] = []
    for rel in (
        "autocad-uzman-1/AGENT.md",
        "_shared/skills/GORSEL_ANALIZ_PROTOKOLU.md",
        "_shared/skills/MIKRO_ZOOM_PROTOKOLU.md",
        "_shared/skills/PDF_POZ_OKUMA.md",
        "_shared/skills/PLAKA_GEOMETRI_CIKARMA.md",
    ):
        p = agents_root / rel
        if p.exists():
            parts.append(f"# {rel}\n{p.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def _missing_candidate_prompt(
    paths: RuntimePaths,
    job_id: str,
    rendered: list[dict[str, Any]],
    existing_candidates: list[Any],
    diagnostic_hints: dict[str, int] | None = None,
) -> str:
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
    hint_block = ""
    if diagnostic_hints:
        hint_lines = [
            f"- {pdf}: {count} vektor daire tespit edildi → holes[] en az {count} delik icermeli."
            for pdf, count in diagnostic_hints.items()
        ]
        hint_block = (
            "Diagnostik ipuclari (PDF basi beklenen delik sayisi):\n"
            + "\n".join(hint_lines)
            + "\nEger goruntuyde daireler varsa holes[] bos birakilmamali; koordinat belirsizse dusuk confidence + uncertainties yaz.\n"
        )
    return (
        f"{context_header}"
        "Bu teknik ofis PDF renderlerinden eksik plaka adaylarini oku. Yalnizca JSON dondur.\n"
        "Shell komutu calistirma, dosya arama yapma, ek dosya okuma denemesi yapma; yalnizca attached render ve mikro-zoom kanitlarini kullan.\n"
        "Bu is, daha once okunmamis sayfalari tamamlamak icindir; gorulmeyen olcu veya poz uydurma.\n"
        "Her image icin karsilik gelen PDF sayfasi:\n"
        + "\n".join(page_lines)
        + "\n"
        + hint_block
        + "JSON schema: {\"candidates\":[{\"source_pdf\":\"...pdf\",\"source_page\":1,\"poz_no\":\"1001\",\"width\":200,\"height\":100,\"thickness\":10,\"material\":\"S355\",\"quantity\":1,\"holes\":[{\"x\":50,\"y\":25,\"diameter\":18}],\"slots\":[],\"corner_reliefs\":[{\"corner\":\"bottom_left\",\"radius\":10,\"relief_type\":\"chamfer\",\"x_offset\":10,\"y_offset\":10}],\"polygon_vertices\":null,\"contour_type\":\"rectangle|polygon|chamfered\",\"confidence\":0.45,\"analysis_confidence\":0.45,\"uncertainties\":[],\"source_trace\":{\"source_pdf\":\"...pdf\",\"source_page\":1,\"method\":\"manager_pdf_scan\",\"microzoom_manifest_path\":null,\"evidence_images\":[]},\"microzoom_manifest_path\":null,\"evidence_images\":[],\"evidence\":\"kisa kanit\"}]}\n"
        "Poz numarasi sayfa numarasi degildir. Cizimdeki parca/mark bilgisini poz_no olarak kullan.\n"
        "Her aday icin mikro-zoom manifestine dayali source_trace, evidence_images, analysis_confidence ve uncertainties yaz.\n"
        "Plaka dis konturu dikdortgen degilse contour_type='polygon' yaz ve polygon_vertices icine tum kose koordinatlarini CCW siraya (0,0)=sol alt referansiyla mm cinsinden gir; net okunamazsa null birak.\n"
        "`contour_type='polygon'` olan aday uretilebilir sayilmak icin `polygon_vertices` zorunludur; belirsiz vertex listesini tahmin ederek doldurma.\n"
        "Polygon kontur + pah/chamfer kombinasyonu gecerlidir: polygon_vertices ile dis konturu, corner_reliefs ile kose pahlarini ayni anda belirt.\n"
        "Plaka dis konturu dikdortgen degilse `contour_type` ile belirt. Pah/chamfer veya kose bosaltma varsa `corner_reliefs` doldur; bos birakma.\n"
        "Delik, slot, kalinlik, malzeme, adet ve ana olculer net degilse dusuk confidence ve acik evidence yaz; tamamen belirsizse aday verme.\n"
        f"Zaten mevcut pozlar: {', '.join(existing_pozs) if existing_pozs else 'yok'}.\n"
        f"Job: {job_id}"
    )


def _visual_candidate_schema_path(paths: RuntimePaths) -> Path:
    path = paths.suite_root / ".state" / "codex-runs" / "manager-missing-candidates.v3.schema.json"
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
        "properties": {
            "corner": {"type": "string"},
            "radius": {"type": "number"},
            "relief_type": {"type": "string"},
            "x_offset": number_or_null,
            "y_offset": number_or_null,
        },
        "required": ["corner", "radius", "relief_type", "x_offset", "y_offset"],
        "additionalProperties": False,
    }
    polygon_vertex_schema = {
        "type": "object",
        "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
        "required": ["x", "y"],
        "additionalProperties": False,
    }
    source_trace_schema = {
        "type": "object",
        "properties": {
            "source_pdf": {"type": "string"},
            "source_page": integer_or_null,
            "method": string_or_null,
            "microzoom_manifest_path": string_or_null,
            "evidence_images": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["source_pdf", "source_page", "method", "microzoom_manifest_path", "evidence_images"],
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
            "polygon_vertices": {"type": ["array", "null"], "items": polygon_vertex_schema},
            "contour_type": string_or_null,
            "confidence": {"type": "number"},
            "analysis_confidence": {"type": "number"},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
            "source_trace": source_trace_schema,
            "microzoom_manifest_path": string_or_null,
            "evidence_images": {"type": "array", "items": {"type": "string"}},
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
            "polygon_vertices",
            "contour_type",
            "confidence",
            "analysis_confidence",
            "uncertainties",
            "source_trace",
            "microzoom_manifest_path",
            "evidence_images",
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


def _normalize_visual_candidate(
    item: dict[str, Any],
    index: int,
    *,
    provider: str,
    allowed_pdf_names: list[str],
    evidence_by_page: dict[tuple[str, int], dict[str, Any]] | None = None,
    paths: RuntimePaths | None = None,
) -> dict[str, Any]:
    source_pdf = str(item.get("source_pdf") or "").strip()
    if source_pdf not in set(allowed_pdf_names) and len(allowed_pdf_names) == 1:
        source_pdf = allowed_pdf_names[0]
    source_page = item.get("source_page")
    try:
        source_page_key = int(source_page or 0)
    except (TypeError, ValueError):
        source_page_key = 0
    evidence_info = (evidence_by_page or {}).get((source_pdf, source_page_key), {})
    generated_images = evidence_info.get("evidence_images") if isinstance(evidence_info.get("evidence_images"), list) else []
    # Runtime-rendered evidence is authoritative. Model-provided image paths can
    # be stale or hallucinated, so only use them when no manifest evidence exists.
    raw_evidence_images = generated_images or (item.get("evidence_images") if isinstance(item.get("evidence_images"), list) else [])
    manifest_path = item.get("microzoom_manifest_path") or evidence_info.get("microzoom_manifest_path")
    evidence_images = [
        _relative_to_suite(paths, Path(image_path)) if paths is not None else str(image_path)
        for image_path in raw_evidence_images
        if image_path
    ]
    manifest_text = _relative_to_suite(paths, Path(manifest_path)) if paths is not None and manifest_path else (str(manifest_path) if manifest_path else None)
    source_trace = {
        "source_pdf": source_pdf or str(item.get("source_pdf") or ""),
        "source_page": source_page,
        "method": provider,
        "microzoom_manifest_path": manifest_text,
        "evidence_images": evidence_images,
    }
    if isinstance(item.get("source_trace"), dict):
        incoming_trace = item["source_trace"]
        if incoming_trace.get("method") not in (None, ""):
            source_trace["method"] = provider
    return {
        "candidate_id": str(item.get("candidate_id") or f"{provider}-{index}"),
        "provider": provider,
        "source_pdf": source_pdf or item.get("source_pdf"),
        "source_page": source_page,
        "poz_no": item.get("poz_no"),
        "width": item.get("width"),
        "height": item.get("height"),
        "thickness": item.get("thickness"),
        "material": item.get("material") or "UNKNOWN",
        "quantity": item.get("quantity") or 1,
        "holes": item.get("holes") if isinstance(item.get("holes"), list) else [],
        "slots": item.get("slots") if isinstance(item.get("slots"), list) else [],
        "corner_reliefs": item.get("corner_reliefs") if isinstance(item.get("corner_reliefs"), list) else [],
        "polygon_vertices": item.get("polygon_vertices") if isinstance(item.get("polygon_vertices"), list) else None,
        "contour_type": item.get("contour_type"),
        "confidence": item.get("confidence") or 0.0,
        "analysis_confidence": item.get("analysis_confidence") or item.get("confidence") or 0.0,
        "uncertainties": item.get("uncertainties") if isinstance(item.get("uncertainties"), list) else [],
        "source_trace": source_trace,
        "microzoom_manifest_path": manifest_text,
        "evidence_images": evidence_images,
        "evidence": item.get("evidence") or item.get("reason"),
        "approval_required": True,
        "validation_errors": [],
    }


def _evidence_by_page(rendered: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    for item in rendered:
        try:
            key = (str(item.get("source_pdf") or ""), int(item.get("source_page") or 0))
        except (TypeError, ValueError):
            continue
        indexed[key] = {
            "microzoom_manifest_path": str(item.get("microzoom_manifest_path")) if item.get("microzoom_manifest_path") else None,
            "evidence_images": [str(path) for path in item.get("evidence_images", []) if path],
        }
    return indexed


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
    except Exception as exc:
        log.debug("pdf_page_count_failed", path=str(path), error=str(exc))
        return None


def _safe_pdf_name_local(value: str) -> str:
    name = Path(value).name.strip() or "input.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return re.sub(r"[^A-Za-z0-9_. -]", "_", name)


def _relative_to_suite(paths: RuntimePaths, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(paths.suite_root.resolve())).replace("\\", "/")
    except Exception as exc:
        log.debug("relative_to_suite_failed", path=str(path), error=str(exc))
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
    if _looks_like_job_artifact_geometry_issue(text):
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


def _looks_like_job_artifact_geometry_issue(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    has_job_or_poz = bool(
        _selected_job_id_from_context(text)
        or _job_id_from_memory_context(text)
        or _extract_job_id(visible_text)
        or _extract_poz_no_from_text(visible_text)
        or ("poz" in lower and _extract_numeric_reference(visible_text))
    )
    if not has_job_or_poz:
        return False
    artifact_terms = ("dxf", "nc1", "qc", "cizim", "uretilen", "olusturulan")
    geometry_terms = ("delik", "hole", "pah", "kose", "poligon", "kontur", "koordinat", "konum")
    issue_terms = ("hatali", "yanlis", "olmasi gereken", "olmali", "duzelt", "tespit", "hata")
    return (
        any(term in lower for term in artifact_terms)
        and any(term in lower for term in geometry_terms)
        and any(term in lower for term in issue_terms)
    )


def _format_project_edit_unavailable_response(reason: str) -> str:
    return (
        "Bu istek proje/kod duzeltmesi gerektiriyor; bunun icin mudurun Codex CLI'yi proje yazma modunda calistirmasi gerekiyor. "
        f"Su anda Codex cevabi tamamlanamadi ({reason}). `toffice doctor` ile Codex CLI durumunu kontrol edip tekrar deneyelim; "
        "runtime isleri ve mevcut job kararlarini yine yerel araclarla yonetebilirim."
    )




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
    except Exception as exc:
        log.warning("manager_memory_patterns_failed", error=str(exc))
    return "\n".join(lines) if lines else "(ilgili kalici hafiza kaydi yok)"



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


def _format_backfill_result_response(job_id: str, result: dict[str, Any]) -> str:
    lines = [f"`{job_id}` icin ogrenim dongusu tamamlandi:\n"]
    retro = result.get("retrospective") if isinstance(result.get("retrospective"), dict) else {}
    if retro.get("path"):
        lines.append(f"- retrospective.json yazildi: `{retro['path']}`")
    sp = retro.get("skill_proposal_path")
    if sp:
        lines.append(f"- Skill proposal olusturuldu: `{sp}`")
    mb = result.get("memory_bridge") if isinstance(result.get("memory_bridge"), dict) else {}
    if mb.get("ok"):
        lines.append(
            f"- Memory bridge guncellendi: {mb.get('plate_count', 0)} plaka, "
            f"parmak izi `{str(mb.get('fingerprint', ''))[:12]}`"
        )
    vault = result.get("vault_path")
    if vault:
        lines.append(f"- Manager vault'a eklendi: `{vault}`")
    if not any((retro.get("path"), sp, mb.get("ok"), vault)):
        lines.append(f"Backfill tamamlandi ama yazilan dosya raporu alinamadi. Durum: {result.get('status', 'unknown')}")
    return "\n".join(lines)


def _format_manual_note_written_response(job_id: str, note_path: Any, paths: RuntimePaths) -> str:
    try:
        rel = Path(note_path).relative_to(paths.suite_root)
    except Exception:
        rel = Path(note_path)
    return (
        f"`{job_id}` henuz tamamlanmadigi icin backfill eligibility yok.\n"
        f"Bildirilen hatalar manuel ogrenim notu olarak kaydedildi:\n"
        f"- `{rel}`\n\n"
        "Is tamamlandiginda veya backfill calistirildiginda bu notlar "
        "retrospektif ile birlestirilecek."
    )


def _read_json_file(path: Any) -> Any:
    return read_json(Path(path))


def _issue_tags(text: str) -> list[str]:
    lower = _normalize_turkish(text)
    tags: list[str] = []
    if any(word in lower for word in ("hata", "yanlis", "sorun", "sikinti", "tespit", "tamamlanmadi")):
        tags.append("hata bildirimi")
    if "delik" in lower or "hole" in lower:
        tags.append("delik koordinati")
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
    except Exception as exc:
        log.warning("manager_issue_note_append_failed", job_id=job_id, error=str(exc))
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
