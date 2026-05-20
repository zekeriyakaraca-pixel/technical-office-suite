# profil-yon-analisti Heartbeat

## Zamanlama
Orchestrator tetikler — `outputs/[proje]_gorsel_status.json` status: "success" olduğunda çalışır.

## Tetikleme Koşulları
```
outputs/[proje]_gorsel_status.json → status: "success"   ✓
outputs/[proje]_rotation_status.json                      YOK
```

## Blocker Koşulları (başlamadan önce kontrol et)
```
data/imports/[proje]_gorsel_status.json eksik      → DUR, orchestrator'a bildir
data/imports/[proje]_sections.json eksik           → DUR, cizim-on-islemci çalıştırılmamış
requirements/[proje].json profil adı eksik         → DUR, cizim-on-islemci tamamlanmamış
```

## Her Döngü

### 1. Bağlam Oku
- `../../outputs/[proje]_gorsel_status.json` → mode, project_type, provider_available, confidence_penalty
- `../../outputs/[proje]_parsed_status.json` → mode (DXF/PDF/DXF+PDF_FALLBACK), dxf_text_count
- `../../requirements/[proje].json` → profil adı (S1 sinyali için zorunlu)
- `../../journal/` son 3 girişi — bekleyen soru veya yarım kalmış iş var mı?

### 2. Waterfall Başlat (SIRA KESİN — değiştirilemez)

#### S1 — Profil Tipi Kuralı
```
requirements/[proje].json → profil adını al
PROFILE_RULES tablosunu uygula:
  HE*, HEM*, HD*     → TOP   (confidence: 0.75)
  IPE*, IPBL*, IPN*  → FRONT (confidence: 0.70)
  CHS*, RHS*, SHS*   → TOP   (confidence: 0.65)
  UNP*, UPE*         → FRONT (confidence: 0.65)
  L*, LD*            → FRONT (confidence: 0.55) + asymmetric_flag: true
→ S1 sinyali kaydet, devam et
```

#### S2 — PDF Kesit Etiketleri
```
data/imports/[proje]_sections.json oku:
  section_labels boş → S2 sinyali: null
  "TOP VIEW" / "PLAN" → rotation_hint: TOP (confidence: 0.82)
  "FRONT VIEW" / "ELEVATION" → rotation_hint: FRONT (confidence: 0.82)
  Çakışan etiketler → çoğunluk oyu
→ S2 sinyali kaydet, devam et
```

#### S2b — Spatial Metin Etiketleri
```
data/imports/[proje]_spatial.json oku:
  "TOP VIEW", "PLAN GÖRÜNÜŞ", "ÜSTTEN GÖRÜNÜŞ" → TOP (confidence: 0.78)
  "FRONT VIEW", "ÖN GÖRÜNÜŞ", "ELEVATION" → FRONT (confidence: 0.78)
  Bulunamazsa → S2b sinyali: null
→ S2b sinyali kaydet, devam et
```

#### S3b / Vision Provider — Görsel Analiz Sinyalleri
```
data/imports/[proje]_gorsel_analiz.json oku:
  vision_model.rotation + vision_model.rotation_confidence → Vision Provider sinyali
  fallback: claude_vision.rotation + claude_vision.rotation_confidence
  s3b_visual_rotation + s3b_visual_confidence → S3b_visual sinyali
→ Her ikisini kaydet, devam et
```

#### S3 — DXF Geometri Sinyali (sadece DXF varsa)
```
parsed_status.json → mode içinde "DXF" varsa VE dxf_text_count > 0 ise:
  python scripts/rotation_analyzer.py data/imports/[proje].dxf \
      --profile [PROFIL_ADI] \
      --sections data/imports/[proje]_sections.json
  → _rotation.json güncellenir (sadece S3 çıktısı, henüz final değil)
  → DXF geometri sinyali kaydet

project_type: "danieli" ise → S3 sinyal kullanma (S3 bypass)
mode: "PDF" veya "DXF+PDF_FALLBACK" ise → S3 bypass
```

#### S5 — Çift Simetri Kontrolü
```
Profil çift simetrikse (HEB, HEA, SHS, RHS, CHS, HD) → symmetric: true
  Not: "rotation irrelevant for double-symmetric profile"
  Bu profillerde rotation hatası Tekla'da görsel fark yaratmaz — düşük öncelik
  Symmetric: true → tüm sinyalleri +0.10 bonus ile birleştir
```

### 3. Füzyon ve Karar

#### Vision Provider Erken Durdurma
```
Vision Provider confidence ≥ 0.75:
  → detected_rotation = vision_model.rotation
  → KESİNLEŞTİ — diğer sinyaller YOKSAYILIR
  → rotation.json yaz, rotation_status.json yaz → DÖNGÜ TAMAM
```

#### Çok Sinyal Füzyon (Vision Provider < 0.75 ise)
```
Tüm toplanan sinyaller:
  Aynı rotation → her ek sinyal: +0.05 bonus
  Farklı rotation → her çakışma: -0.15 ceza

confidence_gate:
  ≥ 0.75 → PASSED
  0.60–0.74 → REVIEW_NEEDED
  < 0.60 → FAILED
```

#### L / Asimetrik Profil Kritik Kontrol (ZORUNLU)
```
asymmetric_flag: true ise (L*, LD*, UNP tek bacak asimetrisi):
  güven(delik_bacağı) < 0.90 ise:
    → asymmetric_review_required: true
    → rotation_status.json → blocker: "L_PROFIL_ROTATION_REVIEW"
    → SORU-XXX: "K1 L80×8 profilinde hangi bacakta delik var? (uzun/kısa bacak)"
    → DUR: insan yanıtı bekle

  güven(delik_bacağı) ≥ 0.90 ise:
    → Normal füzyon sonucu kullan
```

#### FAILED Durumu
```
confidence_gate: FAILED (< 0.60):
  → Önce CODEX_DESTEGI çalıştır (1 deneme)
  → Başarılıysa yeniden hesapla
  → Hâlâ FAILED ise → SORU-XXX aç, insandan onay bekle
```

### 4. Çıktıları Yaz

#### `data/imports/[proje]_rotation.json`
```json
{
  "detected_rotation": "TOP",
  "confidence": 0.93,
  "confidence_gate": "PASSED",
  "sources": ["vision_model", "pdf_label", "profile_rule"],
  "reasoning": "Vision Provider (0.88) + PDF 'TOP VIEW' (0.82) + HEB kuralı (0.75) — oybirliği",
  "signal_detail": {
    "S1_profile_rule":   {"rotation": "TOP",  "confidence": 0.75},
    "S2_pdf_label":      {"rotation": "TOP",  "confidence": 0.82},
    "S2b_spatial":       {"rotation": "TOP",  "confidence": 0.78},
    "S3b_visual":        {"rotation": "TOP",  "confidence": 0.82},
    "vision_model":      {"rotation": "TOP",  "confidence": 0.88},
    "claude_vision":     {"rotation": "TOP",  "confidence": 0.88, "note": "legacy alias"},
    "S3_dxf_geometry":   {"rotation": null,   "confidence": 0.00},
    "S5_double_symmetric": {"symmetric": true, "note": "HEB çift simetrik"}
  }
}
```

#### `outputs/[proje]_rotation_status.json`
```json
{
  "project": "000-000-XXX",
  "agent": "profil-yon-analisti",
  "status": "success",
  "timestamp": "YYYY-MM-DD",
  "detected_rotation": "TOP",
  "rotation_confidence": 0.93,
  "confidence_gate": "PASSED",
  "is_asymmetric_profile": false,
  "asymmetric_review_required": false,
  "double_symmetric": true,
  "soru_required": false,
  "soru_reason": null,
  "blocker": null,
  "next_agent": "cizim-butunleyici"
}
```

**L-profil blocker durumu:**
```json
{
  "status": "blocked",
  "blocker": "L_PROFIL_ROTATION_REVIEW",
  "soru_required": true,
  "soru_reason": "L/asimetrik profilde delik bacağı belirsiz — rotation belirlenemiyor",
  "soru_text": "K1 L80×8 profilinde hangi bacakta delik var? Lütfen belirtin: uzun bacak / kısa bacak",
  "next_agent": "insan-onay"
}
```

### 5. Journal'a Logla
Her döngü sonunda `../../journal/YYYY-MM-DD_HHMM.md`:
- Hangi sinyaller kullanıldı
- Füzyon sonucu (rotation + confidence)
- Blocker varsa nedeni

### 6. MEMORY.md Güncelle
Şu koşullarda ilgili bölüme 1 satır ekle:
- İnsan rotation'ı düzeltti (hangi sinyal yanıltıcıydı?)
- Yeni profil tipi PROFILE_RULES tablosuna eklendi
- L-profil SORU-XXX açıldı ve yanıtı öğrenildi

## Tırmanma Kuralları
- `_gorsel_analiz.json` okunamazsa → dur, insana bildir
- Tüm sinyaller çakışıyor (FAILED sonrası Codex da başarısız) → SORU-XXX aç
- Yanıtsız SORU-XXX varken → bir sonraki adıma geçme
