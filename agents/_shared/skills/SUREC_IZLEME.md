# Skill: Surec Izleme

## Purpose
Teknik ofis islerinin durumunu, manuel inceleme ihtiyacini ve teslim dosyalarini izlemek.

## Serves Goals
- Teknik ofis muduru icin surec takibi.
- Geciken veya bloklanan islerin erken gorulmesi.

## Inputs
- `outputs/jobs/<job_id>/job_summary.json`
- `outputs/jobs/<job_id>/manual_review_required.json`
- `outputs/jobs/<job_id>/partlist_manual_review_required.json`
- `outputs/jobs/<job_id>/<poz_no>/<poz_no>_qc.json`
- `data/imports/jobs/<job_id>/job.json`
- `journal/`

## Process
1. Aktif islerin `job_summary.json` dosyalarini oku.
2. `manual_review_required` varsa isi bekleyen onay olarak isaretle.
3. QC raporlarinda `ok=false` olan pozlari tekrar uretim veya insan kontrolune yonlendir.
4. AutoCAD canli kontrolu `skipped` ise bunu teslim riski olarak degil, dogrulama notu olarak kaydet.
5. ERT partlist Excel yoksa ve QC `ok=true` ise `dokuman-kontrol` gorevi ac.
6. `partlist_manual_review_required.json` varsa teslim kapisini beklemede isaretle.
7. Haftalik ozet icin tamamlanan, bekleyen ve bloklanan isleri say.

## Outputs
- `outputs/haftalik_ozet.md`
- `journal/` durum kaydi

## Quality Bar
- Her blokajda neden, poz no ve dosya yolu belirtilir.
- Dusuk guvenli PDF kararlarinda tahmin yapilmaz.
- Partlist metrik eksiginde birim alan/agrlik tahmin edilmez.

## Tools
- JSON rapor okuma
- `templates/WEEKLY_REVIEW.md`

## Integration
- `IS_DAGITIMI.md` tarafindan acilan isleri kapatir veya insana eskale eder.
