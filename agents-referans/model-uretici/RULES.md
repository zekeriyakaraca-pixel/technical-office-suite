# model-uretici RULES

---

## Okuyabildiği Dosyalar

- `outputs/[proje]_analiz_status.json`
- `outputs/YYYY-MM-DD_tekla_modeller_analiz_[proje].md`
- `outputs/YYYY-MM-DD_tekla_modeller_analiz_v2_[proje].md`
- `data/imports/[proje]_rotation.json`
- `data/imports/[proje]_gorsel_analiz.json` (referans)
- `requirements/[proje].json`

---

## Yazabildiği Dosyalar

- `outputs/YYYY-MM-DD_tekla_modeller_risk_[proje].md` ✅
- `outputs/model_[proje].json` ✅
- `outputs/YYYY-MM-DD_tekla_modeller_model_[proje].json` ✅
- `outputs/[proje]_model_status.json` ✅
- `outputs/YYYY-MM-DD_tekla_modeller_analiz_v2_[proje].md` ✅ (SORU yanıtı işlenince)
- `outputs/YYYY-MM-DD_tekla_modeller_codex_kurtarma_[proje].md` ✅

---

## Yasak Eylemler

- `data/imports/[proje]_rotation.json` YAZMA — sadece profil-yon-analisti yazar
- `data/imports/[proje]_gorsel_analiz.json` YAZMA — sadece cizim-gorsel-analisti yazar
- `outputs/[proje]_analiz_status.json` YAZMA — sadece cizim-butunleyici yazar
- Vision Provider çağrısı YASAK — model-uretici yalnız `_gorsel_analiz.json` okur
- `mcp__tekla__*` araçlarını ÇAĞIRMA — tekla-modelci'nin tekeli
- Rotation analizi YAPMA — profil-yon-analisti'nin tekeli
- Eleman listesi çıkarma YAPMA — cizim-butunleyici'nin işi

---

## Araç Öncelik Sırası (Değiştirilemez)

```
Düşük güven veya hata tespit edildi
  1. CODEX_DESTEGI (skills/CODEX_DESTEGI.md) — maks. 1 deneme
  2. SORU-XXX — Codex başarısızsa veya güven 0.60–0.79 arası
  3. İnsan yanıtı → Adım 1'e dön
```

Bu sıra atlanamaz. Codex denenmeden SORU-XXX açmak yasaktır (güven < 0.60 veya script crash için).

---

## Model.json Üretim Blocker'ları (KRİT-03)
- **Güven Eşiği (Gate):** `global_confidence < 0.75` olan modeller Tekla'ya otomatik gönderilemez.
- **Otomatik Durdurma:** Eğer `global_confidence < 0.75` ise `model_status.json` içinde `blocker: "CONFIDENCE_GATE_FAILED"` set edilmelidir.
- **İnsan Onayı:** Düşük güvenli modeller mutlaka `insan-onay-kapi-2` üzerinden manuel kontrol edilmelidir.
- **Microzoom Gereksinimi:** Güven %60-%74 arasındaysa, model üretmeden önce mutlaka `cizim-gorsel-analisti`'ne microzoom için geri gönder (Re-run request).

---

## Kullandığı Skill'ler

| Skill | Dosya | Zorunlu mu |
|-------|-------|-----------|
| Confidence Gate | `skills/CONFIDENCE_GATE.md` | ✅ Her zaman |
| Codex Desteği | `skills/CODEX_DESTEGI.md` | Koşullu (güven < 0.60 veya script crash) |

---

## Model Contract Template Rules (2026-04-27)

- `requirements/template.json` ve `requirements/[proje].json` geometri sablonu degildir; sadece proje tercihleri icindir.
- Kanonik model sablonu: `agents/model-uretici/templates/model_contract.template.json`.
- Payload doldurma yardimcisi: `scripts/build_model_from_payload.py`.
- Nihai kontrat: `outputs/model_[proje].json`.
- `elements[]` paralel semasi yasaktir; yalniz `columns[]`, `beams[]`, `plates[]`, `holes[]`, `welds[]` kullanilir.
- `plates[].holes` list olamaz; NC uyumu icin `plates[].holes.positions[]` ve `plates[].holes.diameter_mm` kullanilir.
- Kritik geometri alanlarinda varsayim, plaka olcusunden delik turetme veya genel default kullanimi yasaktir.
- Her kritik alanin `source_trace.fields` kaydi olmali; source_type/reference eksikse model kontrati gecemez.
- Validator gecmeden `confidence_gate: PASSED`, `validation.contract_status: PASSED` veya tekla-modelci sinyali yazilamaz.
