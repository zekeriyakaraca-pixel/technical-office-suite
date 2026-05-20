# Rules: autocad-uzman-2

## Boundaries

### This agent CAN:
- PDF kaynaklı plaka işlerinden DXF 2013 ve DSTV NC1 üretebilir.
- `autocad_mcp.technical_office.pipeline.run_job` hattını çalıştırabilir.

### This agent CANNOT:
- Metraj verilerini değiştiremez.
- Düşük güvenli PDF'de ölçü veya delik konumu tahmini yapamaz.
- QC onayı rolünü tek başına kapatamaz.

## Visual Evidence Rules
- Gorsel adaylarda `GORSEL_ANALIZ_PROTOKOLU.md` ve `MIKRO_ZOOM_PROTOKOLU.md` uygulanir.
- Dusuk guvenli aday `approval_required=true` ile teknik-ofis-muduru onayina gider.
- Mikro-zoom manifesti ve `source_trace` olmayan gorsel adayi uretim gercegi sayilmaz.

## Sync Safety
- Original PDF files are read-only inputs.
- Outputs are written under `outputs/jobs/<job_id>/`.
- Aynı `job_id` için autocad-uzman-1 ile çakışmamak adına iş başlamadan önce `job_summary.json`'daki `status` alanını kontrol et; `running` yazıyorsa bu işi alma.
