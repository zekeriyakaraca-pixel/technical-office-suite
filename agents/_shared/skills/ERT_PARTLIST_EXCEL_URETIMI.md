# Skill: ERT Partlist Excel Uretimi

## Purpose
Basarili QC almis AutoCAD plaka pozlari icin `templates/ERT_PARTLIST_FORMAT.md` standardinda Excel parca listesi uretmek.

## Serves Goals
- Dokuman kontrol ajani icin format uyumu.
- Teknik ofis muduru icin proje teslim paketinin tamamlanmasi.

## Inputs
- `data/imports/jobs/<job_id>/job.json`
- `outputs/jobs/<job_id>/job_summary.json`
- `outputs/jobs/<job_id>/<poz_no>/<poz_no>_qc.json`
- `templates/ERT_PARTLIST_FORMAT.md`

## Process
1. `job.json` icinden mudurun verdigi `project_name` alanini oku.
2. `job_summary.json` icinde `ok=true` olan uretilmis pozlari sec.
3. Her pozun QC raporundaki `plate_spec` alanindan plaka bilgilerini oku.
4. `unit_surface_area_m2` ve `unit_weight_kg` yoksa tahmin yapma; `partlist_manual_review_required.json` uret.
5. Excel dosyasini `Part_List_holes` sheet adi ve ERT kolon sirasi ile olustur.
6. Dosyayi `outputs/jobs/<job_id>/<safe_project_name>_partlist.xlsx` olarak kaydet.

## Column Mapping
- `POZ NO`: `plate_spec.poz_no`
- `CİNSİ`: `PL<thickness>`
- `GENİŞLİK`: plaka kisa olcusu, `plate_spec.height`
- `UZUNLUK`: plaka uzun olcusu, `plate_spec.width`
- `ADET`: `plate_spec.quantity`
- `KALİTE`: `plate_spec.material`
- `B.ALAN`: `plate_spec.unit_surface_area_m2`
- `B.AĞIRLIK`: `plate_spec.unit_weight_kg`
- `T.ALAN`: Excel formulu `=+G{row}*E{row}`
- `T.AĞIRLIK`: Excel formulu `=+H{row}*E{row}`
- `AÇIKLAMA`: delik veya slot varsa `Delikli`, yoksa `Deliksiz`

## Outputs
- `outputs/jobs/<job_id>/<safe_project_name>_partlist.xlsx`
- Gerekirse `outputs/jobs/<job_id>/partlist_manual_review_required.json`

## Quality Bar
- QC `ok=true` olmayan poz Excel'e girmez.
- Birim alan ve birim agirlik tahmin edilmez.
- Excel dosya adi mudur metadata'sindaki proje adindan uretilir.

## Tools
- `autocad_mcp.technical_office.partlist.create_partlist`
- `autocad-mcp-server/scripts/run-document-control-partlist.cmd`
- `openpyxl`
