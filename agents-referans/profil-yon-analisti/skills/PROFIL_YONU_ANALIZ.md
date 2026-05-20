# Skill: PROFIL_YONU_ANALIZ (v3 — 5-Sinyal Waterfall)

## Amaç
Tekla rotation enum değerini (TOP / FRONT / BELOW / BEHIND) 5 bağımsız sinyalin waterfall füzyonuyla belirlemek ve `[proje]_rotation.json` üretmek.

Bu skill yalnızca `profil-yon-analisti` agent tarafından kullanılır. Vision Provider bu agent tarafından çağrılmaz — `cizim-gorsel-analisti`'nin ürettiği `_gorsel_analiz.json` okunur.

## Waterfall Sırası (KESİN — değiştirilemez)
```
S1 Profil kuralı → S2 PDF etiket → S2b Spatial → S3b Vision Provider (gorsel_analiz.json) → S3 DXF geometri
→ S5 Çift simetri → Füzyon → Karar
```

---

## Adım S1: Profil Tipi Kuralı

```
requirements/[proje].json → profil adını al (zorunlu)

PROFILE_RULES:
  HE*, HEM*, HD*     → TOP   (confidence: 0.75)
  IPE*, IPBL*, IPN*  → FRONT (confidence: 0.70)
  CHS*, RHS*, SHS*   → TOP   (confidence: 0.65)
  UNP*, UPE*         → FRONT (confidence: 0.65)
  L*, LD*            → FRONT (confidence: 0.55) + asymmetric_flag: true

→ S1 sinyali kaydet, S2'ye devam et
```

## Adım S2: PDF Kesit Etiketleri

```
data/imports/[proje]_sections.json oku:
  section_labels boş → S2 = null
  "TOP VIEW" / "PLAN" → rotation_hint: TOP (confidence: 0.82)
  "FRONT VIEW" / "ELEVATION" → rotation_hint: FRONT (confidence: 0.82)
  Çakışan etiketler → çoğunluk oyu

→ S2 sinyali kaydet, S2b'ye devam et
```

## Adım S2b: Spatial Metin Etiketleri

```
data/imports/[proje]_spatial.json oku:
  "TOP VIEW", "PLAN GÖRÜNÜŞ", "ÜSTTEN GÖRÜNÜŞ" → TOP (confidence: 0.78)
  "FRONT VIEW", "ÖN GÖRÜNÜŞ", "ELEVATION" → FRONT (confidence: 0.78)
  Bulunamazsa → S2b = null

→ S2b sinyali kaydet, S3b'ye devam et
```

## Adım S3b: Vision Provider + Görsel Sinyal (gorsel_analiz.json'dan OKU)

```
data/imports/[proje]_gorsel_analiz.json oku:
  vision_model.rotation + vision_model.rotation_confidence → Vision Provider sinyali
  (fallback: claude_vision.rotation + claude_vision.rotation_confidence)
  s3b_visual_rotation + s3b_visual_confidence → S3b_visual sinyali

  vision_model.rotation_confidence ≥ 0.75:
    → KESİNLEŞ: detected_rotation = vision_model.rotation
    → Füzyon atlama — doğrudan çıktı yaz
    → DÖNGÜ TAMAM

  < 0.75 → Her iki sinyali kaydet, S3'e devam
```

## Adım S3: DXF Geometri Sinyali (koşullu)

```
outputs/[proje]_parsed_status.json oku:
  mode içinde "DXF" VAR ve dxf_text_count > 0 VE project_type ≠ "danieli":
    python scripts/rotation_analyzer.py data/imports/[proje].dxf \
        --profile [PROFIL_ADI] \
        --sections data/imports/[proje]_sections.json
    → _rotation.json (geçici S3 çıktısı) → DXF geometri sinyali kaydet

  Diğer durumlarda (PDF only / danieli / DXF+PDF_FALLBACK) → S3 = null (bypass)

→ S5'e devam
```

## Adım S5: Çift Simetri Kontrolü

```
HEB, HEA, SHS, RHS, CHS, HD → symmetric: true
  Not: "rotation irrelevant for double-symmetric profile"
  Tüm sinyallere +0.10 bonus ekle

Asimetrik profil → symmetric: false, bonus yok
```

---

## Füzyon Kararı

### Vision Provider Erken Durdurma (S3b'de gerçekleşir)
```
vision_model.rotation_confidence ≥ 0.75 → KESİNLEŞ
  Diğer sinyaller yoksayılır
```

### Çok Sinyal Füzyon (Vision Provider < 0.75 ise)
```
Toplanan sinyaller (S1 + S2 + S2b + S3b_visual + S3):
  Aynı rotation → her ek sinyal: +0.05 bonus
  Farklı rotation → her çakışma: -0.15 ceza

confidence_gate:
  ≥ 0.75 → PASSED
  0.60–0.74 → REVIEW_NEEDED
  < 0.60 → FAILED
```

### L / Asimetrik Profil Kritik Kontrol (ZORUNLU)
```
asymmetric_flag: true ise:
  güven(delik_bacağı) < 0.90 ise:
    → asymmetric_review_required: true
    → blocker: "L_PROFIL_ROTATION_REVIEW"
    → SORU-XXX aç, DUR

  güven(delik_bacağı) ≥ 0.90 ise:
    → Normal füzyon sonucu kullan
```

### FAILED Durumu
```
confidence_gate: FAILED (< 0.60):
  → CODEX_DESTEGI çalıştır (1 deneme)
  → Başarılıysa yeniden füzyon hesapla
  → Hâlâ FAILED → SORU-XXX aç
```

---

## Çıktı: `data/imports/[proje]_rotation.json`

```json
{
  "detected_rotation": "TOP",
  "confidence": 0.93,
  "confidence_gate": "PASSED",
  "sources": ["vision_model", "pdf_label", "profile_rule"],
  "reasoning": "Vision Provider (0.88) + PDF 'TOP VIEW' (0.82) + HEB kuralı (0.75) — oybirliği",
  "signal_detail": {
    "S1_profile_rule":     {"rotation": "TOP",  "confidence": 0.75},
    "S2_pdf_label":        {"rotation": "TOP",  "confidence": 0.82},
    "S2b_spatial":         {"rotation": "TOP",  "confidence": 0.78},
    "S3b_visual":          {"rotation": "TOP",  "confidence": 0.82},
    "vision_model":        {"rotation": "TOP",  "confidence": 0.88},
    "claude_vision":       {"rotation": "TOP",  "confidence": 0.88, "note": "legacy alias"},
    "S3_dxf_geometry":     {"rotation": null,   "confidence": 0.00},
    "S5_double_symmetric": {"symmetric": true,  "note": "HEB çift simetrik"}
  }
}
```

---

## Kurallar
- Vision Provider çağrısı yasak — `_gorsel_analiz.json`'dan oku
- `tekla_rotation_enum` boş bırakılamaz — belirsizse SORU-XXX aç
- L-profil: `güven(delik_bacağı) < 0.90` → güven ne olursa SORU-XXX (istisna yok)
- CODEX_DESTEGI denenmeden insana soru sormak yasak
- Waterfall sırası kesin — adım atlanamaz
