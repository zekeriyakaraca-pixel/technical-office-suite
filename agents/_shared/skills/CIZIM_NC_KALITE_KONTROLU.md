# Skill: Cizim ve NC Kalite Kontrolu

## Purpose
Uretilen DXF ve NC1 dosyalarinin ayni `PlateSpec` ile tutarli oldugunu kontrol etmek.

## Serves Goals
- AutoCAD uzman-2 icin bagimsiz kalite kapisi.
- Teknik ofis muduru icin teslim riski gorunurlugu.

## Inputs
- `<poz_no>.dxf`
- `<poz_no>.nc1`
- `PlateSpec`

## Process
1. DXF dosyasini ac ve `AC1027` surumunu kontrol et.
2. Kapali dis kontur ve delik sayisini `PlateSpec` ile karsilastir.
3. `PLATE_SLOTS` katmanindaki kapali polyline sayisini `PlateSpec.slots` sayisiyla karsilastir.
4. Kose rolyef sayisini (bulge yay sayisi + ekstra polygon kosesi) `PlateSpec.corner_reliefs` ile karsilastir.
5. NC1 dosyasinda `ST`, poz no ve `EN` kontrollerini yap.
6. AutoCAD MCP canli degilse uretimi durdurma; `autocad_live_check=skipped_autostart_failed` veya ilgili durum kodunu yaz.
7. Raporu `workspace/outputs/jobs/<job_id>/<poz_no>/<poz_no>_qc.json` olarak kaydet.

QC raporu alanlari: `ok`, `dxf.ok`, `dxf.circle_count`, `dxf.expected_holes`,
`dxf.slot_polyline_count`, `dxf.expected_slots`, `dxf.corner_relief_count`,
`nc1.ok`, `autocad_live_check`, `plate_spec`.

## Outputs
- `workspace/outputs/jobs/<job_id>/<poz_no>/<poz_no>_qc.json`

## Quality Bar
- QC `ok=false` ise dosya teslim edilmez.
- Slot sayisi eslesmiyor bile olsa `manual_review_required` olarak isaretle, uretimi durdurma.
- AutoCAD canli kontrolunun atlanmasi ayri bir nottur; DXF/NC1 uretimini bozmaz.
- `ok`, `failed_open`, `failed_zoom`, `ok_plot_failed` ve `skipped_autostart_failed` durumlarini acik raporla.

## Tools
- `autocad_mcp.technical_office.qc.build_qc_report`
- `autocad_mcp.technical_office.autocad_live.run_autocad_live_validation`
- Opsiyonel `autocad-mcp` MCP `drawing.open`, `view.zoom_extents`, `drawing.plot_pdf`

## Integration
- `SUREC_IZLEME.md` QC raporlarini takip eder.
