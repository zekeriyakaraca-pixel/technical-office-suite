# Agent: model-uretici

## Misyon

Onaylı analiz çıktısını alıp confidence eşik kontrolü yaparak `model_[proje].json` üretmek. Confidence gate geçilmeden `tekla-modelci` başlayamaz.

---

## Tetikleyici Koşullar

Bu agent şu koşulların **tümü** sağlandığında başlar:

1. `outputs/[proje]_analiz_status.json` mevcut ve `"status": "success"`
2. `outputs/[proje]_analiz_status.json` içinde `"soru_count": 0` VEYA tüm SORU-XXX yanıtlanmış
3. `outputs/model_[proje].json` YOK (yoksa zaten tamamlanmış)

---

## Girdiler

| Dosya | Kaynak Agent | Zorunlu |
|-------|-------------|---------|
| `outputs/YYYY-MM-DD_tekla_modeller_analiz_[proje].md` | cizim-butunleyici | ✅ |
| `outputs/[proje]_analiz_status.json` | cizim-butunleyici | ✅ |
| `data/imports/[proje]_rotation.json` | profil-yon-analisti | ✅ |
| `requirements/[proje].json` | insan-onay / cizim-on-islemci | ✅ |
| `data/imports/[proje]_gorsel_analiz.json` | cizim-gorsel-analisti | İsteğe bağlı |

---

## Çıktılar

| Dosya | Açıklama |
|-------|----------|
| `outputs/YYYY-MM-DD_tekla_modeller_risk_[proje].md` | Risk raporu — eleman bazlı güven durumu |
| `outputs/model_[proje].json` | tekla-modelci'nin okuduğu model — kontrat değişmez |
| `outputs/YYYY-MM-DD_tekla_modeller_model_[proje].json` | Tarih damgalı arşiv kopyası |
| `outputs/[proje]_model_status.json` | Orchestrator kontrol noktası |
| `outputs/YYYY-MM-DD_tekla_modeller_codex_kurtarma_[proje].md` | Codex deneme raporu (varsa) |

---

## Sorumluluk Sınırları

- **Yapar:** Confidence hesaplama, risk raporu, CODEX_DESTEGI tetikleme, SORU-XXX üretimi, model.json fabrikası
- **Yapmaz:** Rotation analizi (`profil-yon-analisti`'nin işi)
- **Yapmaz:** Vision Provider çağrısı (`cizim-gorsel-analisti`'nin işi); yalnız `_gorsel_analiz.json` okur
- **Yapmaz:** Eleman listesi çıkarma (`cizim-butunleyici`'nin işi)
- **Yapmaz:** `tekla-modelci` araçlarını çağırma

---

## Devralınan Skill'ler

| Skill | Dosya |
|-------|-------|
| Confidence Gate (6 aşama) | `skills/CONFIDENCE_GATE.md` |
| Codex Kurtarma Protokolü | `skills/CODEX_DESTEGI.md` |

---

## Orchestrator Sinyali

```json
{
  "status": "success | blocked",
  "global_confidence": 0.88,
  "confidence_gate": "PASSED | REVIEW_NEEDED | FAILED",
  "soru_count": 0,
  "blocker": null,
  "model_file": "outputs/model_000-000-933-109.json",
  "next_agent": "insan-onay → tekla-modelci"
}
```

**Blocker durumları:**
- `"CONFIDENCE_GATE_FAILED"` — global_confidence < 0.75 veya yanıtsız SORU var

---

## Canonical Model Contract

`model-uretici` artik `outputs/model_[proje].json` dosyasini kanonik sablondan uretir:

- Sablon: `agents/model-uretici/templates/model_contract.template.json`
- Payload builder: `scripts/build_model_from_payload.py`
- Validator: `scripts/validate_model_contract.py`
- Proje tercihleri: `requirements/[proje].json`
- Geometri kaynagi: `cizim-butunleyici` yapilandirilmis analiz payload'u

Sablon prompt'a buyuk JSON olarak gomulmez; agent sadece sablon yolunu, doldurma kurallarini ve validator sonucunu kullanir. Geometri alanlari analiz kaynagi olmadan doldurulmaz.
