# Skill: CONFIDENCE_GATE

## Amaç

Analiz çıktısındaki confidence skorlarını değerlendirerek Risk Raporu ve SORU listesi üretmek; global eşik sağlanmadan `model_[proje].json` üretimini engellemek.

## Önkoşullar

- `outputs/[proje]_analiz_status.json` mevcut ve `status: success`
- Analiz dosyası confidence skorlarını içeriyor (eleman bazlı)
- Tüm SORU-XXX yanıtlanmış (ilk geçişte 0 olmalı; değilse bekle)

---

## Aşama 1: Confidence Skorlarını Topla

```
Her eleman için:
  - Her veri noktasının confidence değerini analiz dosyasından çek
  - Eleman bazlı ortalama hesapla

Global Confidence = Ortalama(tüm veri noktası confidence değerleri)
```

---

## Aşama 2: Risk Raporu Üret

`outputs/YYYY-MM-DD_tekla_modeller_risk_[proje].md`:

```
RISK REPORT — [Proje Adı] — [Tarih]

Eleman Bazlı Durum:
| Eleman | Veri | Güven | Risk |
|--------|------|-------|------|
| K1     | profil eksik | %40 | ⛔ YÜKSEK |
| B2     | uzunluk tahmini | %65 | ⚠ ORTA |
| B5     | tüm veriler mevcut | %95 | ✅ DÜŞÜK |

Özet:
- ⛔ Yüksek risk (< %60): X eleman
- ⚠ Orta risk (%60–%79): X eleman
- ✅ Düşük risk (≥ %80): X eleman
- Global Confidence: %XX
```

Risk seviyeleri:
| Confidence | Seviye | Aksiyon |
|-----------|--------|---------|
| < 0.60 | ⛔ YÜKSEK | CODEX_DESTEGI → başarısızsa SORU-XXX |
| 0.60–0.79 | ⚠ ORTA | Doğrudan SORU-XXX |
| ≥ 0.80 | ✅ DÜŞÜK | Otomatik geç |

---

## Aşama 3: CODEX_DESTEGI Denemesi

**Yalnızca şu koşullarda çalışır:**
- Herhangi kritik veri noktası confidence < 0.60 (profil, uzunluk, delik konumu)
- Script crash → geometry/spatial JSON boş
- Polygon stiffener contour_points eksik

**Koşul yoksa (0.60–0.79 arası, script başarılı) → Bu aşamayı atla.**

```
Codex görevi hazırla → skills/CODEX_DESTEGI.md çalıştır (maks. 1 deneme)

Başarılıysa:
  → Parser çıktısını yenile
  → Global Confidence yeniden hesapla (Aşama 1'e dön)
  → Güven ≥ 0.75 → model.json üret (Aşama 6'ya geç)

Başarısızsa:
  → Kurtarma raporuna "BAŞARISIZ" yaz
  → Aşama 3.1'e geç (SORU-XXX akışı)
```

---

## Aşama 3.1: SORU-XXX Üret

Confidence < 0.80 olan her veri noktası için:

```
[SORU-001]
Eleman: K1
Sorun: Profil bilgisi PDF'te okunamadı
Tahmin: HEA200 (benzer elemanlara göre)
Güven: %40
Onaylıyor musunuz? (E/H) Veya doğru değeri girin:
```

Kurallar:
- Bir elemanda birden fazla belirsizlik → her biri ayrı soru
- Numaralandırma döngü genelinde artar: SORU-001, SORU-002, ...
- Tahmin yoksa → `Tahmin: Belirsiz`

---

## Aşama 4: Global Eşik Kararı

```
global_confidence ≥ 0.75 VE soru_count = 0
    → PASSED → Aşama 6'ya geç

global_confidence < 0.75 VEYA soru_count > 0
    → BLOCKED → _model_status.json'a blocker yaz, bekle
```

**Minimum eşik: 0.75 — bu eşiğin altında model.json üretilmez.**

---

## Aşama 5: İnsan Yanıtlarını İşle

SORU'lar yanıtlandıktan sonra:
1. Her yanıtı ilgili elemana uygula
2. Onaylanan veri → confidence = 1.00
3. Global Confidence yeniden hesapla
4. Güncellenmiş analiz dosyasını yaz: `outputs/YYYY-MM-DD_tekla_modeller_analiz_v2_[proje].md`
5. Aşama 1'e dön

---

## Aşama 6: model.json Üret

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

**Zorunlu alanlar:**
- `position.depth` = `"MIDDLE"` (Tekla varsayılanı BEHIND — hatalıdır)
- `position.lateral` = `"MIDDLE"` (Tekla varsayılanı RIGHT — hatalıdır)
- `position.tekla_rotation_enum` = `_rotation.json`'dan al (boş bırakılamaz)
- Koordinatlar milimetre cinsinden

---

## Çıktılar

- `outputs/YYYY-MM-DD_tekla_modeller_risk_[proje].md` — risk raporu + soru listesi
- `outputs/model_[proje].json` — tekla-modelci kontratlı dosya
- `outputs/YYYY-MM-DD_tekla_modeller_model_[proje].json` — tarih damgalı arşiv
- `outputs/YYYY-MM-DD_tekla_modeller_analiz_v2_[proje].md` — yanıt işlenmiş analiz (varsa)

---

## 2026-04-27 Contract Override

Yukaridaki eski mini JSON ornegi sadece tarihsel referanstir. Guncel uretim kurali:

- Model serbest JSON olarak yazilmaz; `agents/model-uretici/templates/model_contract.template.json` doldurulur.
- `requirements/[proje].json` geometri kaynagi degildir; yalniz proje tercihleri icindir.
- Uc plakalarda `position.depth=MIDDLE` yasaktir: base plate `BELOW`, cap plate `ABOVE`.
- `plates[].holes` list olamaz; `plates[].holes.positions[]` kullanilir.
- `scripts/validate_model_contract.py outputs/model_[proje].json` PASS olmadan `confidence_gate: PASSED` yazilamaz.

## 2026-04-30 Confidence Ayrımı — KRİTİK

`global_confidence` yalnızca **çizim okuma kalitesini** ölçer (`analysis_confidence`).
Tekla modelinin geometrik doğruluğunu (`model_confidence`) ölçmez.

- `_model_status.json`'a şu iki alanı **ayrı** yaz:
  ```json
  {
    "analysis_confidence": 0.87,
    "model_confidence": null
  }
  ```
- `model_confidence` bu aşamada `null` bırakılır — tekla-modelci sonrası insan görsel
  onayı ile `"verified_visual"` veya sayısal skor girilir.
- `global_confidence: 0.87` yüksek görünse de Tekla modeli tamamen hatalı olabilir.
  000-000-484-204_001_00: analysis=0.87, gerçek model doğruluğu ≤0.40 (5 kritik hata).
- Orchestrator sinyalinde `confidence_gate: PASSED` demek yalnızca **analiz** eşiğinin
  geçildiği anlamına gelir; modelin doğru çizildiği garantisi değildir.
