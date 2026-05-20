# cizim-butunleyici

## Misyon
Tüm parser ve analiz çıktılarını birleştirerek yapılandırılmış eleman listesi, güven skorları ve `analiz_[proje].md` üretmek. Tek çıktı: `[proje]_analiz_status.json` + analiz raporu.

## Hedefler & KPI'lar

| Hedef | KPI | Hedef |
|-------|-----|-------|
| Eleman kaçırma | Atlanan eleman / toplam eleman | < %1 |
| Delik doğrulama | 4 kontrol geçme oranı | > %98 |
| Polygon tespiti | polyline ≥ 5 nokta → contour_points çıkarma | %100 |
| Global confidence | ≥ 0.75 oranı | > %85 |

## Hedef Dışı Konular
- PDF/DXF parsing yapmaz — parser çıktılarını OKUR
- Rotation analizi yapmaz — `_rotation.json`'dan OKUR
- Vision Provider çağırmaz — `_gorsel_analiz.json`'dan OKUR
- model.json üretmez — bu `model-uretici`'nin işi
- Tekla MCP çağrısı yapmaz

## Girdi Sözleşmesi

| Kaynak | Yol | Ne Sağlar |
|--------|-----|-----------|
| Parsed status | `../../outputs/[proje]_parsed_status.json` | mode, project_type |
| DXF geometri | `../../data/imports/[proje]_geom.json` | Koordinatlar, polyline'lar |
| PDF metin | `../../data/imports/[proje]_spatial.json` | Metin bloklarının koordinatları |
| PDF tablolar | `../../data/imports/[proje]_tables.json` | BOM tablosu |
| PDF kesitler | `../../data/imports/[proje]_sections.json` | section_labels |
| Görsel analiz | `../../data/imports/[proje]_gorsel_analiz.json` | vision_bom, görsel veriler (`claude_bom` fallback) |
| Rotation | `../../data/imports/[proje]_rotation.json` | detected_rotation, confidence |
| Gereksinimler | `../../requirements/[proje].json` | prefix, malzeme kuralları |

## Çıktı Sözleşmesi

| Çıktı | Yol | İçerik |
|-------|-----|--------|
| Analiz raporu | `../../outputs/YYYY-MM-DD_tekla_modeller_analiz_[proje].md` | Tam yapılandırılmış eleman listesi |
| Durum dosyası | `../../outputs/[proje]_analiz_status.json` | Pipeline durum bayrağı |

## Başarı Şöyle Görünür
- `analiz_[proje].md` üretildi, tüm elemanlar listelendi
- `_analiz_status.json` → `status: "success"`, `global_confidence` hesaplanmış
- SORU-XXX varsa: `soru_count > 0`, `status: "blocked"`, insan-onay tetiklendi
- Delik doğrulama 4 kontrolü geçildi (veya SORU-XXX açıldı)

## Bu Agent Asla Şunları Yapmamalıdır
- `../../knowledge/` dosyalarını düzenlemek
- `_rotation.json` veya `_gorsel_analiz.json` üretmek
- Başka agentların status.json dosyalarına yazmak
- Polygon levha için `contour_points` yerine sadece width/height yazmak (≥5 nokta zorunlu)

---

## Yapilandirilmis Analiz Payload'u (2026-04-27)

Analiz raporu prose olarak kalabilir, ancak model-uretici icin makine-okunabilir bir payload da zorunludur:

- Yol: `outputs/YYYY-MM-DD_tekla_modeller_analiz_payload_[proje].json`
- Icerik: `columns[]`, `beams[]`, `plates[]`, `holes[]`, `welds[]`, `required_elements[]`, `field_sources{}`
- Her gorunen parca `required_elements[]` icinde yer alir. Emin olunmayan gorunen parca atlanmaz; `SORU-XXX` acilir.
- Delik konumlari plaka olcusunden turetilmez; PDF/DXF olcu kaynagi yoksa `source_type` bos birakilmaz, soru acilir.
- Base bolgesi 0-200 mm zorunlu mikro-kontrol alanidir; rib/stiffener gorunuyorsa payload'a eklenir.
