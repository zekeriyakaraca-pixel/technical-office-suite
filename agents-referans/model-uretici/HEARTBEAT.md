# model-uretici HEARTBEAT

Her döngüde bu adımları sırayla çalıştır. Bir adım başarısız olursa sonrakine geçme.

---

## Adım 0: Bağlam Oku

```
- outputs/[proje]_analiz_status.json → global_confidence, soru_count, eleman listesi
- outputs/YYYY-MM-DD_tekla_modeller_analiz_[proje].md → confidence skorları
- data/imports/[proje]_rotation.json → tekla_rotation_enum
- requirements/[proje].json → prefix, malzeme, faz tercihleri
```

`tekla_rotation_enum` boşsa → `_rotation_status.json`'a bak → hâlâ boşsa `profil-yon-analisti`'ni tetikle, dur.

---

## Adım 1: Confidence Skorlarını Topla (CONFIDENCE_GATE Aşama 1)

```
analiz dosyasındaki her eleman güven değerini oku
global_confidence = ortalama(tüm eleman confidence değerleri)
```

---

## Adım 2: Risk Raporu Üret (CONFIDENCE_GATE Aşama 2)

`outputs/YYYY-MM-DD_tekla_modeller_risk_[proje].md` üret:

```
RISK REPORT — [Proje] — [Tarih]

| Eleman | Veri Sorunu | Güven | Risk |
|--------|------------|-------|------|
| K1     | profil eksik | %40  | ⛔ YÜKSEK |
| B2     | uzunluk tahmini | %65 | ⚠ ORTA |
| B5     | tam veri | %95   | ✅ DÜŞÜK |

Global Confidence: %XX
```

Risk eşikleri:
- `⛔ YÜKSEK` — confidence < 0.60
- `⚠ ORTA` — confidence 0.60–0.79
- `✅ DÜŞÜK` — confidence ≥ 0.80

---

## Adım 3: CODEX_DESTEGI Kontrolü

Aşağıdaki koşullardan **biri** varsa `skills/CODEX_DESTEGI.md` çalıştır:
- Herhangi kritik veri noktası confidence < 0.60 (profil, uzunluk, delik)
- Script crash sonucu güven düşmüş (geom.json boş, parse başarısız)
- Polygon stiffener contour_points eksik

Koşul yoksa (tüm düşük güven 0.60–0.79, script başarılı) → bu adımı atla.

**Maksimum 1 Codex denemesi.** Başarılıysa global_confidence'ı yeniden hesapla (Adım 1'e dön). Başarısızsa Adım 4'e geç.

---

## Adım 4: Global Eşik Kararı (CONFIDENCE_GATE Aşama 4)

```
global_confidence ≥ 0.75 VE soru_count = 0
    → PASSED → Adım 6'ya geç (model.json üret)

global_confidence 0.60–0.74 → REVIEW_NEEDED → SORU-XXX üret (Adım 5)

global_confidence < 0.60 VE Codex denendi → FAILED → SORU-XXX üret (Adım 5)
```

---

## Adım 5: SORU-XXX Üret (CONFIDENCE_GATE Aşama 3.1)

Confidence < 0.80 olan her veri noktası için:

```
[SORU-001]
Eleman: K1
Sorun: Profil bilgisi PDF'te okunamadı
Tahmin: HEA200 (benzer elemanlara göre)
Güven: %40
Onaylıyor musunuz? (E/H) Veya doğru değeri girin:
```

Risk raporuna ekle → `_model_status.json`'a `"soru_count": N`, `"blocker": "CONFIDENCE_GATE_FAILED"` yaz → dur, insan yanıtını bekle.

İnsan yanıtı gelince:
1. Yanıtı analiz dosyasına işle (confidence → 1.00)
2. `outputs/YYYY-MM-DD_tekla_modeller_analiz_v2_[proje].md` kaydet
3. Adım 1'e dön

---

## Adım 6: model.json Üret (CONFIDENCE_GATE Aşama 6)

```json
{
  "project": "[proje-adi]",
  "generated": "YYYY-MM-DD",
  "global_confidence": 0.88,
  "columns": [
    {
      "id": "K1",
      "profile": "HEA200",
      "material": "S355",
      "start_point": [0, 0, 0],
      "end_point": [0, 0, 6000],
      "position": {
        "depth": "MIDDLE",
        "lateral": "MIDDLE",
        "tekla_rotation_enum": 0
      },
      "phase": 1,
      "confidence": 0.95
    }
  ],
  "beams": [],
  "plates": [],
  "connections": []
}
```

---

## V2 Model Contract Template Flow (2026-04-27)

Bu agent `outputs/model_[proje].json` dosyasini serbest bicimde yazmaz. Her model uretimi asagidaki sirayla yapilir:

1. `agents/model-uretici/templates/model_contract.template.json` okunur.
2. `requirements/[proje].json` yalniz proje tercihleri icin okunur: prefix, malzeme, tolerans, NC ayarlari. Geometri buradan alinmaz.
3. `cizim-butunleyici` tarafindan uretilen yapilandirilmis analiz payload'u okunur.
4. Sadece analizde kaynagi bulunan alanlar doldurulur.
5. Yeni parcalar gerekiyorsa ilgili koleksiyona eklenir: `columns[]`, `beams[]`, `plates[]`, `holes[]`, `welds[]`.
6. Her kritik alan icin `source_trace.fields` yazilir.
7. Tercih edilen komut: `python scripts/build_model_from_payload.py outputs/YYYY-MM-DD_tekla_modeller_analiz_payload_[proje].json --archive`
8. `python scripts/validate_model_contract.py outputs/model_[proje].json` gecmeden `confidence_gate: PASSED` yazilmaz.

Kritik kural: Sablonda kalan `TODO_SOURCE_REQUIRED`, `TODO`, bos string veya `null` modellemeyi bloke eder. Base plate icin `position.depth=BELOW`, cap plate icin `position.depth=ABOVE`; uc plakalarda `MIDDLE` yasaktir.

**Zorunlu kurallar:**
- `position.depth` = `"MIDDLE"` (asla boş bırakma)
- `position.lateral` = `"MIDDLE"` (asla boş bırakma)
- `position.tekla_rotation_enum` = `_rotation.json`'dan al (asla boş bırakma → SORU-XXX)
- Koordinatlar milimetre

---

## Adım 7: Dosyaları Yaz

```
outputs/model_[proje].json                                ← tekla-modelci okur (kontrat)
outputs/YYYY-MM-DD_tekla_modeller_model_[proje].json      ← tarih damgalı arşiv
outputs/[proje]_model_status.json                         ← orchestrator kontrol noktası
```

---

## Adım 8: Status JSON Yaz

```json
{
  "status": "success",
  "agent": "model-uretici",
  "timestamp": "YYYY-MM-DDTHH:MM:SS",
  "global_confidence": 0.88,
  "confidence_gate": "PASSED",
  "soru_count": 0,
  "blocker": null,
  "model_file": "outputs/model_[proje].json",
  "next_agent": "insan-onay → tekla-modelci"
}
```
