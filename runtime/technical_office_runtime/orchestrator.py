from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .agent_context import build_system_prompt, load_agent_context
from .codex_bridge import CodexBridge, CodexRunRequest
from .config import RuntimePaths, get_paths
from .tools import ToolRegistry


MANAGER_CODEX_READ_TIMEOUT_SECONDS = 90
MANAGER_CODEX_WRITE_TIMEOUT_SECONDS = 180
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

    def run(self, user_text: str, history: list[dict[str, str]] | None = None) -> AgentRunResult:
        text = user_text.strip()
        if not text:
            return AgentRunResult(content="Bir is veya soru yazarsan teknik ofis adina ilerletebilirim.")
        if self.agent_id != "teknik-ofis-muduru":
            return self._run_codex_manager(text, history or [])
        if _looks_like_lightweight_manager_chat(text):
            return AgentRunResult(
                content=_format_lightweight_manager_response(self._available_job_ids()),
                fallback_reason="local_manager_chat",
            )
        if _looks_like_job_restart_request(text):
            return self._handle_job_restart_request(text, history or [])
        if _looks_like_job_status_request(text):
            return self._handle_job_status_request(text, history or [])
        if _looks_like_project_edit_request(text):
            if self.allow_codex:
                return self._run_codex_manager(text, history or [])
            return self._run_fallback(text, reason="codex_disabled")
        if _looks_like_issue_discussion_request(text):
            return AgentRunResult(
                content=_format_issue_discussion_response(self.paths, text),
                fallback_reason="local_manager_issue_discussion",
            )
        if _should_route_locally(text):
            routed = self._run_fallback(text, reason="local_tool_router")
            if routed.tool_results:
                return routed
        if _looks_like_runtime_ready_request(text) and not getattr(self.bridge, "executable", None):
            return AgentRunResult(content=_format_runtime_ready_response(), fallback_reason="local_runtime_status")
        if self.allow_codex:
            return self._run_codex_manager(text, history or [])
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
        prompt = _manager_prompt(build_system_prompt(context), user_text, history, project_edit=project_edit)
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
        if _looks_like_confirmed_job_reset(lower):
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
        return AgentRunResult(
            content=_format_job_status_response(self.paths, job_id),
            fallback_reason="local_job_status",
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
        "kontrol et",
        "incele",
    )
    return any(phrase in lower for phrase in phrases)


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
    open_notes = _open_manager_issue_note_count(output_dir)

    produced_count = len(produced) if isinstance(produced, list) else 0
    manual_review_count = len(manual_reviews) if isinstance(manual_reviews, list) else 0
    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    ok_value = summary.get("ok") if isinstance(summary, dict) and "ok" in summary else "bilinmiyor"
    project_name = metadata.get("project_name") if isinstance(metadata, dict) else None

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
    if isinstance(page_count, int) and page_count > produced_count and any(item in {"visual_text_required", "text_layer_unreadable"} for item in classifications):
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
    lowered = text.lower().translate(
        str.maketrans(
            {
                "\u0131": "i",
                "\u0130": "i",
                "\u011f": "g",
                "\u011e": "g",
                "\u00fc": "u",
                "\u00dc": "u",
                "\u015f": "s",
                "\u015e": "s",
                "\u00f6": "o",
                "\u00d6": "o",
                "\u00e7": "c",
                "\u00c7": "c",
            }
        )
    )
    normalized = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _visible_user_text(text: str) -> str:
    cut = len(text)
    for marker in (f"\n\n{_SELECTED_CONTEXT_MARKER}", f"\n\n{_MEMORY_CONTEXT_MARKER}"):
        idx = text.find(marker)
        if idx >= 0:
            cut = min(cut, idx)
    return _repair_mojibake_text(text[:cut].strip())


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
    affected_pozs = _extract_poz_numbers(visible_text)
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


def _extract_poz_numbers(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\b[0-9]{2,6}\b", text)))


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
