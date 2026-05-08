# Skill: Dokuman Formatlama

## Purpose
Uretim ciktilarini teslim klasoru icin okunabilir ve tutarli duzene getirmek.

## Serves Goals
- Dokuman kontrol ajani icin format uyumu.

## Inputs
- `outputs/jobs/<job_id>/`
- `data/imports/jobs/<job_id>/job.json`
- QC raporlari

## Process
1. Her poz klasorunde DXF, NC1 ve QC dosyalarinin varligini kontrol et.
2. `job_summary.json` dosyasina gore teslim listesini hazirla.
3. QC `ok=true` olan AutoCAD plaka islerinde `ERT_PARTLIST_EXCEL_URETIMI.md` ciktisini kontrol et.
4. `manual_review_required.json` veya `partlist_manual_review_required.json` varsa teslimi beklemede isaretle.

## Outputs
- Teslim kontrol notu
- ERT partlist Excel kontrol sonucu

## Quality Bar
- Teknik icerik degistirilmez.
- Eksik dosya varsa teslim paketi tamamlanmis sayilmaz.
- ERT partlist icin birim alan/agrlik degerleri tahmin edilmez.

## Tools
- Dosya sistemi ve JSON raporlari

## Integration
- `ARSIVLEME.md` skill'ine girdi saglar.
- `ERT_PARTLIST_EXCEL_URETIMI.md` tamamlandiktan sonra arsivlemeye girdi saglar.
