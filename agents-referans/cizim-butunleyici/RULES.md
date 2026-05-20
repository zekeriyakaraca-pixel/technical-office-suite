# Kurallar: cizim-butunleyici

## Bu Agent Şunları YAPABİLİR:
- `../../data/imports/[proje]_geom.json` okuyabilir
- `../../data/imports/[proje]_spatial.json` okuyabilir
- `../../data/imports/[proje]_tables.json` okuyabilir
- `../../data/imports/[proje]_sections.json` okuyabilir
- `../../data/imports/[proje]_gorsel_analiz.json` okuyabilir
- `../../data/imports/[proje]_rotation.json` okuyabilir
- `../../data/imports/[proje]_page_N.png` okuyabilir (Read tool ile görsel)
- `../../outputs/[proje]_parsed_status.json` okuyabilir
- `../../outputs/[proje]_rotation_status.json` okuyabilir
- `../../requirements/[proje].json` okuyabilir
- `../../outputs/YYYY-MM-DD_tekla_modeller_analiz_[proje].md` yazabilir
- `../../outputs/[proje]_analiz_status.json` yazabilir
- `../../journal/` okuyabilir ve yazabilir
- **`codex:rescue` skill'ini çağırabilir** — polygon hesabı başarısız veya güven < 0.60 durumunda (maks. 1 deneme)
- Kendi `MEMORY.md`'sini güncelleyebilir

## Bu Agent Şunları YAPAMAZ:
- Vision Provider'ı çağıramaz
- `../../data/imports/[proje]_gorsel_analiz.json` dosyasını YAZAMAZ — sadece okur
- `../../data/imports/[proje]_rotation.json` dosyasını YAZAMAZ — sadece okur
- `../../outputs/model_[proje].json` üretemez — bu `model-uretici`'nin işi
- `../../knowledge/` dosyalarını düzenleyemez
- Başka agentların status.json dosyalarına yazamaz

## Araç Önceliği Kuralı (KESİN — istisna yok)

| Durum | Yapılacak |
|-------|-----------|
| Polygon hesabı başarısız (≥5 nokta karmaşık) | CODEX_DESTEGI çalıştır (1 deneme) |
| global_confidence < 0.60 | CODEX_DESTEGI çalıştır (1 deneme) |
| Codex başarısız | SORU-XXX ile insana sun |
| Delik doğrulama başarısız (4 kontrol) | Modele yazmadan SORU-XXX aç |
| BOM çakışması | SORU-XXX aç (tahmin yürütme) |

**CODEX_DESTEGI denenmeden insana soru sormak yasaktır.**

## Zorunlu Polygon Kuralı

DXF polyline'da ≥ 5 nokta varsa:
```
contour_points: [
  {"x": ..., "y": ..., "z": 0},
  ...
]
```
Sadece `width` ve `height` yazmak yasaktır.

## Devir Kuralları

### ORCHESTRATOR'a devret:
- `_analiz_status.json` → status: "success", soru_count: 0 → orchestrator model-uretici'yi tetikler
- `_analiz_status.json` → status: "blocked", soru_required: true → orchestrator insan-onay'ı tetikler

## Paylaşılan Dosya Kuralları
- `../../journal/` her döngüde yaz
- `_analiz_status.json` içinde `next_agent` alanını mutlaka doldur
- Codex kurtarma raporu → `../../outputs/YYYY-MM-DD_tekla_modeller_codex_kurtarma_[proje]_analiz.md`

## Skills Listesi (Bu Agent Tarafından Kullanılır)
- `skills/pdf/GENEL_KURALLAR.md` — Her analiz döngüsünde okunur
- `skills/pdf/DANIELI_KURALLAR.md` — project_type: "danieli" ise
- `skills/pdf/BAGLANTI_DETAY.md` — Bağlantı detayları varsa
- `skills/pdf/KAYNAK_ANALIZI.md` — Kaynak sembolleri varsa
- `skills/pdf/BOM_KONTROL.md` — BOM çapraz kontrolü
- `skills/pdf/URETILEBILIRLIK.md` — Üretilebilirlik kontrolü
- `skills/pdf/VERIFIKASYON.md` — Geometrik doğrulama (zorunlu)
- `skills/pdf/CIKTI_FORMAT.md` — Analiz raporu formatı
- `skills/CODEX_DESTEGI.md` — Kurtarma skill'i
