# Rules: autocad-uzman-2

## Boundaries

### This agent CAN:
- PDF kaynaklı plaka işlerinden DXF 2013 ve DSTV NC1 üretebilir.
- `autocad_mcp.technical_office.pipeline.run_job` hattını çalıştırabilir.

### This agent CANNOT:
- Metraj verilerini değiştiremez.
- Düşük güvenli PDF'de ölçü veya delik konumu tahmini yapamaz.
- QC onayı rolünü tek başına kapatamaz.

## Sync Safety
- Original PDF files are read-only inputs.
- Outputs are written under `outputs/jobs/<job_id>/`.
- Aynı `job_id` için autocad-uzman-1 ile çakışmamak adına iş başlamadan önce `job_summary.json`'daki `status` alanını kontrol et; `running` yazıyorsa bu işi alma.
