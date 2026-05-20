from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .text_normalization import normalize_search_text, repair_text


_SELECTED_CONTEXT_MARKER = "[Secili is baglami:"
_MEMORY_CONTEXT_MARKER = "[Mudur hafiza baglami:"

# Pre-compiled patterns — derleme maliyeti ilk import'ta bir kez ödenir.
_RE_JOB_ID        = re.compile(r"\b[A-Za-z]+-[0-9][A-Za-z0-9_.-]*\b")
_RE_POZ_NO        = re.compile(r"\b([0-9]{3,6})\b")
_RE_NUMERIC_REF   = re.compile(r"\b[0-9]{2,}\b")
_RE_RADIUS_DOT    = re.compile(r"(\d+(?:\.\d+)?)\s*mm")
_RE_RADIUS_COMMA  = re.compile(r"(\d+(?:[.,]\d+)?)\s*mm")
_RE_DIM_PAIR      = re.compile(r"(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)")
_RE_NUM_DOT       = re.compile(r"\b(\d+(?:\.\d+)?)\b")
_RE_PROJECT_NAME  = re.compile(r"(?:proje|project|isim|adi|named?)[:\s]+([^\n,;]+)", re.IGNORECASE)
_RE_ISLE_WORD     = re.compile(r"\bisle\b")
_RE_URET_WORD     = re.compile(r"\buret\b")
_RE_STEP_START    = re.compile(r"^\s*1\s*[\.)]")
_RE_STEP_PATTERNS = (
    re.compile(r"\b([1-4])\s*[\.)]?\s*(?:adim|asama)"),
    re.compile(r"\b(?:adim|asama)\s*([1-4])\b"),
    re.compile(r"\b([1-4])\s*(?:numarali|nolu|no\s*lu)?\s*(?:sec\w*|opsiyon|tercih)"),
    re.compile(r"\b(?:sec\w*|opsiyon|tercih)\s*([1-4])\b"),
)


def _extract_job_id(text: str) -> str | None:
    match = _RE_JOB_ID.search(text)
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


def _looks_like_hole_coordinate_correction_request(text: str) -> bool:
    visible_text = _visible_user_text(text)
    lower = _normalize_turkish(visible_text)
    if not lower:
        return False
    if not any(term in lower for term in ("delik", "hole")):
        return False
    if not any(term in lower for term in ("koordinat", "konum", "x=", "x =", "y=", "y =")):
        return False
    correction_terms = (
        "hatali",
        "yanlis",
        "olmasi gereken",
        "olmasi gereken deger",
        "olmasi gerekenler",
        "olacak",
        "degismeli",
        "duzeltilmeli",
        "duzelt",
        "yerine",
    )
    return any(term in lower for term in correction_terms)


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
    match = _RE_POZ_NO.search(text)
    return match.group(1) if match else None


def _extract_all_poz_nos_from_text(text: str) -> list[str]:
    """Metindeki tüm poz numaralarını (3-6 haneli sayılar) döndürür."""
    return _RE_POZ_NO.findall(text)


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


def _candidates_needing_polygon_vertices(candidates: list[Any]) -> list[dict]:
    result: list[dict] = []
    for i, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            continue
        contour_type = str(item.get("contour_type") or "").strip().lower()
        has_polygon_marker = contour_type in {"polygon", "polygonal"}
        reliefs = item.get("corner_reliefs")
        if isinstance(reliefs, list):
            has_polygon_marker = has_polygon_marker or any(
                isinstance(relief, dict)
                and str(relief.get("type") or relief.get("relief_type") or "").strip().lower() == "polygon_contour"
                for relief in reliefs
            )
        vertices = item.get("polygon_vertices")
        has_vertices = isinstance(vertices, list) and len(vertices) >= 3
        if has_polygon_marker and not has_vertices:
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
    radius_m = _RE_RADIUS_DOT.search(normalized)
    radius: float
    if radius_m:
        radius = float(radius_m.group(1))
    else:
        num_m = _RE_NUM_DOT.search(normalized)
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
    pair = _RE_DIM_PAIR.search(text)
    if pair:
        return float(pair.group(1).replace(",", ".")), float(pair.group(2).replace(",", "."))
    single = _RE_RADIUS_COMMA.search(text)
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


def _format_polygon_vertices_question(job_id: str, pending: list[dict]) -> str:
    lines = [f"`{job_id}` isinde poligon kontur var ama `polygon_vertices` eksik:"]
    for item in pending:
        poz_no = item.get("poz_no") or "?"
        width = item.get("width")
        height = item.get("height")
        evidence = str(item.get("evidence") or "").strip()
        dims = f"{width}x{height}mm" if width is not None and height is not None else ""
        lines.append(f"- Satir {item.get('_row_index', '?')}: {poz_no} {dims}".strip())
        if evidence:
            lines.append(f"  Kanit: {evidence[:180]}")
    lines.append("")
    lines.append("Bu aday uretime giremez. Dis konturun tum kose koordinatlarini `polygon_vertices` JSON alanina CCW sirada girin.")
    lines.append('Ornek: [{"x":0,"y":0},{"x":156.5,"y":0},{"x":156.5,"y":140},{"x":120,"y":175},{"x":0,"y":175}]')
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
    for pattern in _RE_STEP_PATTERNS:
        match = pattern.search(lower)
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
    if _RE_STEP_START.search(lower) and any(word in lower for word in ("basla", "baslayalim", "bslayalim", "gec", "yap")):
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

def _manager_flow_session_id(session_id: str | None) -> str:
    return (session_id or "agent:teknik-ofis-muduru:default").strip() or "agent:teknik-ofis-muduru:default"


def _looks_like_manager_action_confirmation(text: str) -> bool:
    lower = _normalize_turkish(_visible_user_text(text))
    if _looks_like_bare_manager_action_confirmation(text):
        return True
    return any(
        phrase in lower
        for phrase in (
            "bunu yap",
            "bunlari yap",
            "uygula",
            "onayliyorum",
            "devam et",
            "ne gerekiyorsa yap",
            "geregini yap",
            "gerekeni yap",
            "tamam uygula",
            "tamam yap",
        )
    )


def _looks_like_bare_manager_action_confirmation(text: str) -> bool:
    lower = _normalize_turkish(_visible_user_text(text)).strip()
    return lower in {
        "yap",
        "uygula",
        "devam",
        "devam et",
        "onayliyorum",
        "tamam yap",
        "tamam uygula",
        "ne gerekiyorsa yap",
        "geregini yap",
        "gerekeni yap",
    }

def _extract_numeric_reference(text: str) -> str | None:
    match = _RE_NUMERIC_REF.search(text)
    return match.group(0) if match else None


def _extract_poz_no(text: str) -> str | None:
    match = _RE_POZ_NO.search(text)
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
    match = _RE_PROJECT_NAME.search(text)
    return match.group(1).strip() if match else None


def _looks_like_run_request(lower: str) -> bool:
    if "run job" in lower or "pipeline calistir" in lower:
        return True
    if any(phrase in lower for phrase in ("calistir", "isle", "uretime al", "uretimi baslat", "yeniden uret")):
        if _RE_ISLE_WORD.search(lower) and any(
            phrase in lower
            for phrase in (
                "isle ilgili",
                "bu isle ilgili",
                "numarali isle ilgili",
                "numrali isle ilgili",
                "is ile ilgili",
            )
        ):
            without_context_phrase = lower
            for phrase in (
                "isle ilgili",
                "bu isle ilgili",
                "numarali isle ilgili",
                "numrali isle ilgili",
                "is ile ilgili",
            ):
                without_context_phrase = without_context_phrase.replace(phrase, "")
            if not any(
                phrase in without_context_phrase
                for phrase in ("calistir", "uretime al", "uretimi baslat", "yeniden uret")
            ) and _RE_URET_WORD.search(without_context_phrase) is None:
                return False
        return True
    return _RE_URET_WORD.search(lower) is not None


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


def _looks_like_learning_write_intent(text: str) -> bool:
    lower = _normalize_turkish(_visible_user_text(text))
    write_verbs = (
        "yazalim", "olusturalim", "kaydelim", "baslatalim",
        "yaz", "olustur", "kaydet", "cikar", "cikaralim",
        "not al", "not alalim", "ekleyelim", "ekle",
    )
    context_words = (
        "retrospektif", "retrospective", "ogrenim", "ogrenelim",
        "ders", "hatalardan", "ogrenilmeler", "learning",
        "ogrenilenler", "cikaralim", "ogrenelim",
    )
    has_write = any(v in lower for v in write_verbs)
    has_context = any(c in lower for c in context_words)
    return has_write and has_context


def _looks_like_approval_queue_request(text: str) -> bool:
    lower = _normalize_turkish(_visible_user_text(text))
    return any(
        phrase in lower
        for phrase in (
            "onayima ihtiyacin",
            "onayim gerekiyor",
            "onay bekleyen",
            "onay bekliyor",
            "onaylanacak",
            "onaya sunulan",
            "onaya sunulmus",
            "ne onaylanacak",
            "ne onaylanmali",
            "onay listesi",
            "hangi isler onay",
            "onay gerektiren",
            "onay gerekiyor",
            "awaiting approval",
            "pending approval",
        )
    )


def _job_id_from_recent_history(history: list[dict[str, str]]) -> str | None:
    for item in reversed(history[-12:]):
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        matches = _RE_JOB_ID.findall(_visible_user_text(content))
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

