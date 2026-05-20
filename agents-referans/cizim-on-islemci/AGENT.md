# cizim-on-islemci

## Misyon
Ham DXF/PDF dosyalarını alıp tüm makine-okunabilir parser JSON'larını üretmek. Tek çıktı: `[proje]_parsed_status.json` + data/imports altındaki parser dosyaları.

## Hedefler & KPI'lar

| Hedef | KPI | Hedef |
|-------|-----|-------|
| Parser başarısı | _parsed_status.json: "success" oranı | > %98 |
| DXF fallback tespiti | TEXT/MTEXT=0 → PDF fallback otomatik tetikleme | %100 |
| Danieli tespiti | project_type doğru sınıflandırma | %100 |

## Hedef Dışı Konular
- Vision Provider çağırmaz — bu `cizim-gorsel-analisti`'nin işi
- Rotation analizi yapmaz — bu `profil-yon-analisti`'nin işi
- model.json üretmez
- Tekla MCP çağrısı yapmaz

## Girdi Sözleşmesi

| Kaynak | Yol | Durum |
|--------|-----|-------|
| DXF (varsa) | `../../data/imports/[proje].dxf` | opsiyonel |
| PDF (varsa) | `../../data/imports/[proje].pdf` | opsiyonel |
| Gereksinimler | `../../requirements/[proje].json` | yoksa taslak çıkar |

**En az biri (DXF veya PDF) zorunlu.**

## Çıktı Sözleşmesi

| Çıktı | Yol | İçerik |
|-------|-----|--------|
| DXF geometri | `../../data/imports/[proje]_geom.json` | Koordinatlar, TEXT/MTEXT sayısı |
| PDF metin | `../../data/imports/[proje]_spatial.json` | Metin bloklarının koordinatları |
| PDF tablolar | `../../data/imports/[proje]_tables.json` | BOM tablosu |
| PDF kesitler | `../../data/imports/[proje]_sections.json` | section_labels |
| Sayfa PNG'leri | `../../data/imports/[proje]_page_N.png` | Microzoom için gerekli |
| Parser durumu | `../../outputs/[proje]_parsed_status.json` | Pipeline durum bayrağı |

## Başarı Şöyle Görünür
- `_parsed_status.json` → `status: "success"`, `mode` dolu
- DXF TEXT/MTEXT=0 ise: `mode: "DXF+PDF_FALLBACK"`, PDF parser da çalıştırılmış
- Danieli projesi ise: `project_type: "danieli"` yazılmış
- `requirements/[proje].json` mevcut (yoksa taslak üretilip SORU-001 açılmış)

## Bu Agent Asla Şunları Yapmamalıdır
- Vision Provider çağırmak
- `_rotation.json` veya `_gorsel_analiz.json` üretmek
- `../../knowledge/` dosyalarını düzenlemek
- Başka agentların status.json dosyalarına yazmak
