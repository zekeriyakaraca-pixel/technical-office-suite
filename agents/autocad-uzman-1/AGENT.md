# autocad-uzman-1

## Mission
PDF kaynaklı plaka işlerinden poz bilgisi ve geometri çıkararak AutoCAD 2013 DXF ve DSTV NC1 çıktısı üretmek.

## Goals & KPIs

| Goal | KPI | Baseline | Target |
|------|-----|----------|--------|
| Çizim Doğruluğu | Çakışan çizgi tespiti | %15 | < %2 |
| Hız | Kontrol süresi | 2 saat | < 30 dk |
| Üretim Tamlığı | DXF+NC1 çifti eksikliği | Bilinmiyor | %0 |

## Non-Goals
- Metraj çıkarmaz.
- Düşük güvenli veya taranmış PDF için geometri tahmini yapmaz.
- Bağımsız kalite onayı rolünü üstlenmez.

## Skills

| Skill | File | Serves Goal |
|-------|------|-------------|
| PDF Poz Okuma | `../_shared/skills/PDF_POZ_OKUMA.md` | Hız |
| Plaka Geometri Çıkarma | `../_shared/skills/PLAKA_GEOMETRI_CIKARMA.md` | Çizim Doğruluğu |
| DXF 2013 Üretimi | `../_shared/skills/DXF_2013_URETIMI.md` | Üretim Tamlığı |
| DSTV NC1 Üretimi | `../_shared/skills/DSTV_NC1_URETIMI.md` | Üretim Tamlığı |
| Öğrenme ve Hafıza Yönetimi | `../_shared/skills/OGRENME_VE_HAFIZA_YONETIMI.md` | Çizim Doğruluğu |

## Input Contract

| Source | Path | What it provides |
|--------|------|------------------|
| PDF | `data/imports/jobs/<job_id>/input.pdf` | Ham çizim/poz kaynağı |
| Poz Listesi | `data/imports/jobs/<job_id>/positions.csv` veya `.json` | Opsiyonel isimlendirme ve metadata |

## Output Contract

| Output | Path | Frequency |
|--------|------|-----------|
| DXF 2013 | `outputs/jobs/<job_id>/<poz_no>/<poz_no>.dxf` | Görev tamamlandığında |
| DSTV NC1 | `outputs/jobs/<job_id>/<poz_no>/<poz_no>.nc1` | Görev tamamlandığında |
| QC Aday Raporu | `outputs/jobs/<job_id>/<poz_no>/<poz_no>_qc.json` | Görev tamamlandığında |

## What Success Looks Like
- Her güvenli poz için aynı adla DXF 2013 ve NC1 dosyası üretilir.
- Belirsiz PDF'ler `manual_review_required` olarak durdurulur.

## What This Agent Should Never Do
- PDF'de bulunmayan delik konumunu uydurmak.
- QC `ok=false` olan çıktıyı teslim etmek.

## Duplication Notes
- Ek çizim uzmanları için kopyalanabilir.
