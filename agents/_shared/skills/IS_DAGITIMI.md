# Skill: Is Dagitimi

## Purpose
Teknik ofis islerini dogru ajana, dogru girdi sozlesmesiyle atamak.

## Serves Goals
- Teknik ofis muduru icin is dagitim hizi.
- AutoCAD plaka uretim hattinda islerin takip edilebilir olmasi.

## Inputs
- `data/imports/jobs/<job_id>/input.pdf`
- `data/imports/jobs/<job_id>/job.json`
- Opsiyonel `data/imports/jobs/<job_id>/positions.csv`
- Opsiyonel `data/imports/jobs/<job_id>/positions.json`
- `journal/` altindaki onceki durum kayitlari

## Process
1. Her yeni isi benzersiz `job_id` ile kaydet.
2. `job.json` icinde mudur onayli `project_name`, `manager_agent`, `created_at` alanlarini olustur veya kontrol et.
3. PDF varligini kontrol et; yoksa isi insana geri dondur.
4. Poz listesi varsa `poz_no`, `page`, `quantity`, `thickness_mm`, `material`, `name_override`, `notes` alanlarini bekle.
5. AutoCAD/DXF/NC isi ise once `AUTOCAD_MCP_HAZIRLIK.md` ile canli dogrulama hazirligini baslat.
6. Plaka/DXF/NC isi icin asil uretimi musait autocad-uzman'a ata: `journal/` altindaki son log girisi okunarak `autocad-uzman-1` ve `autocad-uzman-2` arasinda aktif isi olmayan secilir. Ikisi de mesgulse yeni is `job_summary.json`'a `status: "queued"` olarak yazilir ve bir sonraki heartbeat dongusu beklenilir.
7. Bagimsiz kalite kontrolunu `kalite-kontrol` ajanina ata.
8. QC `ok=true` oldugunda `dokuman-kontrol` ajanina `ERT_PARTLIST_EXCEL_URETIMI.md` gorevini ata.
9. Tekla metraj ajanlarini bu hatta dahil etme; onlar parked durumdadir.

## Outputs
- `outputs/jobs/<job_id>/job_summary.json`
- `outputs/jobs/<job_id>/manual_review_required.json` gereken durumlarda
- `data/imports/jobs/<job_id>/job.json`
- `journal/` icin atama ve durum notu

## Quality Bar
- Her is tek bir `job_id` altinda izlenir.
- Dusuk guvenli PDF veya belirsiz geometri otomatik uretime alinmaz.
- Ajan atamasi ve beklenen ciktilar acik yazilir.

## Tools
- `autocad_mcp.technical_office.pipeline.run_job`
- `agents/_shared/skills/AUTOCAD_MCP_HAZIRLIK.md`
- `agents/_shared/skills/CIZIM_NC_KALITE_KONTROLU.md`

## Integration
- `PDF_POZ_OKUMA.md` ile baslar.
- `DXF_2013_URETIMI.md` ve `DSTV_NC1_URETIMI.md` ciktilarini kalite kapisina gonderir.
