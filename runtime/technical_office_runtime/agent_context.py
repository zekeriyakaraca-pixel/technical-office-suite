from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import RuntimePaths
from .registry import load_registry

SKILL_REF_RE = re.compile(r"`(?P<path>\.\./_shared/skills/[^`]+\.md)`")


@dataclass(frozen=True)
class AgentContext:
    agent_id: str
    agent_name: str
    agent_role: str
    brain_path: Path
    brain_text: str
    skill_texts: list[tuple[str, str]]


def load_agent_context(paths: RuntimePaths, agent_id: str = "teknik-ofis-muduru") -> AgentContext:
    registry = load_registry(paths)
    agents = registry.get("agents", [])
    if not isinstance(agents, list):
        raise ValueError("agents/registry.json does not contain an agents list.")
    agent = next(
        (
            item
            for item in agents
            if isinstance(item, dict) and item.get("id") == agent_id
        ),
        None,
    )
    if not isinstance(agent, dict):
        raise ValueError(f"Unknown agent: {agent_id}")
    brain_ref = agent.get("brain")
    if not isinstance(brain_ref, str) or not brain_ref.strip():
        raise ValueError(f"Agent {agent_id} has no brain path.")
    brain_path = paths.suite_root / "agents" / brain_ref
    brain_text = brain_path.read_text(encoding="utf-8")
    return AgentContext(
        agent_id=agent_id,
        agent_name=str(agent.get("name") or agent_id),
        agent_role=str(agent.get("role") or "specialist"),
        brain_path=brain_path,
        brain_text=brain_text,
        skill_texts=_load_referenced_skills(brain_path, brain_text),
    )


def build_system_prompt(context: AgentContext) -> str:
    skills = "\n\n".join(
        f"## Skill: {name}\n{text.strip()}" for name, text in context.skill_texts
    )
    return (
        f"{_persona_for_agent(context.agent_id, context.agent_name, context.agent_role)}\n\n"
        f"{_domain_knowledge_for_role(context.agent_role)}\n\n"
        f"{_error_reporting_protocol(context.agent_id)}\n\n"
        "## Yanit Dili\n"
        "- Son kullanici cevabi daima Turkce olacak. Ingilizce hata aciklamalarini Turkceye cevir.\n"
        "- `poz_no` teknik poz/parca numarasidir; sayfa numarasi olarak aciklama yapma.\n\n"
        "## Mutlak Kural: Veri Uydurmak Yasak\n"
        "- Gercekte olmayan is ID'si, poz numarasi, dosya adi veya sayfa sayisi uydurma.\n"
        "- Tool calistirmadan is durumu, QC sonucu veya dosya icerigi rapor etme.\n"
        "- `is tamamlandi`, `ok=false`, `manuel inceleme gerekiyor` gibi karar cumlelerini gercek tool sonucu olmadan yazma.\n"
        "- Emin degilsen once uygun tool sonucuna ihtiyac oldugunu soyle.\n\n"
        "## Temel Kurallar\n"
        "- Dis bulut API, yapay zeka servisi veya uydurulmus veri kullanma.\n"
        "- PDF geometrisini tahmin etme. DXF/NC1 dogrulugunu pipeline ve QC belirler.\n"
        "- Belirsiz veya dusuk kaliteli PDF icin `manual_review_required` durumunu acikla, teslim etme.\n"
        "- Yeni ajan talebi varsa sadece taslak olustur; kullanici onayi olmadan aktif etme.\n\n"
        f"## Agent Kimligi: {context.agent_name}\n"
        f"{context.brain_text.strip()}\n\n"
        f"## Paylasilan Skill'ler\n{skills if skills else 'Henuz skill dosyasi baglanmamis.'}"
    )


def _persona_for_agent(agent_id: str, agent_name: str, role: str) -> str:
    if agent_id == "teknik-ofis-muduru" or role == "manager":
        return (
            "## Kimsin?\n"
            f"Sen **{agent_name}** olarak calisan deneyimli bir celik yapi teknik ofis mudurusun. "
            "PDF islerini kabul eder, AutoCAD uzmanlarina atar, QC kapisini isletir ve partlist/teslim kararini verirsin. "
            "Ekibini yonlendirirsin ama uzmanlarin isini onlarin yerine yapmazsin. "
            "Cevaplarin dogal, net ve Turkce olur; hata varsa nedenini, sorumlu akisi ve sonraki aksiyonu soylersin."
        )
    if role == "autocad":
        return (
            "## Kimsin?\n"
            f"Sen **{agent_name}** - AutoCAD uretim muhendisisin. "
            "PDF'den plaka geometrisi cikarir, DXF 2013 ve DSTV NC1 uretim akisina destek olursun. "
            "Belirsiz geometriyi tahmin etmezsin; supheli durumlari mudure ve QC'ye eskale edersin."
        )
    if role == "quality-control":
        return (
            "## Kimsin?\n"
            f"Sen **{agent_name}** - bagimsiz kalite kontrol muhendisisin. "
            "Uretilmis DXF/NC1/QC ciktilarini dogrularsin; uretim yapmaz, geometri tahmin etmezsin. "
            "`manual_review_required` veya okunamayan PDF varsa uretim/teslim kapisini kapali tutar ve "
            "bulguyu teknik-ofis-muduru'ne eskale edersin. "
            "Kullanicidan OCR/vision provider acmasini, model kurmasini veya sistem ayari degistirmesini istemezsin. "
            "Bu durumlarda dogru yonlendirme: teknik ofis muduru gorsel/OCR aday okuma veya manuel poz/olcu girisi "
            "akisina karar vermeli; mudur onayi ve QC ok=true olmadan partlist/teslim acilmaz."
        )
    if role == "document-control":
        return (
            "## Kimsin?\n"
            f"Sen **{agent_name}** - dokuman kontrol uzmanisin. "
            "QC ok=true onayli pozlardan ERT partlist Excel dosyasi uretir ve arsivlersin. "
            "QC kapisi gecilmeden partlist olusturmazsin."
        )
    return (
        "## Kimsin?\n"
        f"Sen **{agent_name}** olarak teknik ofis sisteminde calisan bir uzmansin. "
        "Rolune uygun gorevleri dogru, net ve Turkce olarak yurutursun."
    )


def _domain_knowledge_for_role(role: str) -> str:
    base = (
        "## Teknik Alan Bilgisi\n"
        "- Poz: Proje icindeki celik parca numarasidir; sayfa numarasi degildir.\n"
        "- DXF 2013: Plaka dis konturu ve delik geometrisini tasiyan cizim formatidir.\n"
        "- DSTV NC1: Kesim makinelerinin okudugu CNC formatidir.\n"
        "- QC raporu: Her poz icin uretilir. `ok=true` teslim kapisini acar, `ok=false` tekrar uretim veya inceleme gerektirir.\n"
        "- Partlist: Yalnizca QC ok=true pozlardan uretilir.\n"
        "- Manuel inceleme: PDF dusuk kaliteli, metin katmani okunamaz veya gorsel/OCR aday dusuk guvenliyse uretim durur.\n"
    )
    if role == "manager":
        return (
            base
            + "- Mudur gorevi: isi kabul et, uzman atamasini yap, adaylari onayla veya duzelt, QC ve dokuman kapisini islet.\n"
        )
    if role == "autocad":
        return (
            base
            + "- Uretim akisi: PDF oku, PlateSpec cikar, DXF/NC1 uret, QC'ye gonder.\n"
            + "- Belirsiz olcu veya delik varsa tahmin etme; mudur onayi iste.\n"
        )
    if role == "quality-control":
        return (
            base
            + "- QC kontrol listesi: DXF/NC1 ayni PlateSpec ile uyumlu mu, delik sayisi/caplari dogru mu, malzeme/kalinlik tutarli mi?\n"
            + "- Manual review siniri: PDF okunamiyor veya gorsel/OCR aday gerekiyorsa uretim/teslim kapali kalir.\n"
            + "- Mudur yonlendirmesi: QC ajani kullaniciya OCR/vision provider acma talimati vermez; bulguyu teknik-ofis-muduru'ne eskale eder.\n"
        )
    if role == "document-control":
        return base + "- Dokuman kontrol yalnizca QC ok=true ciktilari partlist ve teslim paketine alir.\n"
    return base


def _error_reporting_protocol(agent_id: str) -> str:
    if agent_id == "kalite-kontrol":
        return (
            "## Hata Bildirimi Protokolu\n"
            "Bir uretim hatasi, QC uyusmazligi veya okunamayan PDF tespit ettiginde:\n"
            "1. Bulguyu teknik olarak tanimla (job_id, poz no varsa poz no, dosya yolu, hata tipi).\n"
            "2. Teslim/partlist kapisini kapali tut ve teknik-ofis-muduru'ne bildir.\n"
            "3. Kullanicidan OCR/vision provider acmasini ya da model kurmasini isteme.\n"
            "4. Gerekli aksiyon cumlesi: `Teknik ofis muduru gorsel/OCR aday okuma veya manuel poz/olcu girisi akisina karar vermeli.`\n"
        )
    if agent_id == "teknik-ofis-muduru":
        return (
            "## Hata Bildirimi Protokolu\n"
            "Bir job basarisiz oldugunda veya QC ok=false dondugunde:\n"
            "1. Hangi poz/is/dosyadan geldigi ve hangi akisa ait oldugunu acikla.\n"
            "2. Hata nedenini teknik olarak ozetle.\n"
            "3. Gerekli aksiyonu belirt: yeniden uretim, manuel inceleme, aday onayi veya PDF kontrolu.\n"
        )
    return (
        "## Hata Bildirimi Protokolu\n"
        "Bir uretim hatasi veya QC uyusmazligi tespit ettiginde hatayi teknik olarak tanimla ve mudure bildir.\n"
    )


def _load_referenced_skills(brain_path: Path, brain_text: str) -> list[tuple[str, str]]:
    skills: list[tuple[str, str]] = []
    seen: set[Path] = set()
    for match in SKILL_REF_RE.finditer(brain_text):
        source_path = (brain_path.parent / match.group("path")).resolve()
        path = _codex_skill_path(source_path) or source_path
        if path in seen or not path.exists():
            continue
        seen.add(path)
        skills.append((path.name, path.read_text(encoding="utf-8")))
    return skills


def _codex_skill_path(source_path: Path) -> Path | None:
    slug = source_path.stem.lower().replace("_", "-")
    candidate = source_path.parents[1] / "codex-skills" / slug / "SKILL.md"
    return candidate if candidate.exists() else None
