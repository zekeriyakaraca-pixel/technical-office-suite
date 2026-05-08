# Skill: AutoCAD MCP Hazirlik

## Purpose
Yeni AutoCAD/DXF isi baslamadan once AutoCAD'in ve MCP File IPC dispatcher'in calisir durumda oldugunu hazirlamak.

## Serves Goals
- Teknik ofis muduru icin minimum insan mudahalesiyle is kabul.
- AutoCAD uzmanlari icin canli `open`, `zoom_extents` ve `plot_pdf` kalite kapisi.

## Inputs
- `data/imports/jobs/<job_id>/input.pdf`
- Opsiyonel `data/imports/jobs/<job_id>/positions.csv` veya `.json`
- AutoCAD MCP `system(operation="ensure_autocad_ready")`

## Process
1. Her yeni plaka/DXF isinde uretimden once `system(operation="ensure_autocad_ready")` cagir.
2. `ok=true` ise isi normal hatta devam ettir; DXF/NC1 uretiminden sonra canli AutoCAD dogrulamasini calistir.
3. `ok=false` ise DXF/NC1 uretimini iptal etme; QC raporunda `autocad_live_check=skipped_autostart_failed` gorunmesini sagla.
4. Hata mesajinda lisans ekrani, guvenlik prompt'u, modal pencere veya `mcp_dispatch.lsp` yuklenememe belirtisi varsa insandan yalnizca bu net aksiyonu iste.
5. AutoCAD acikken dispatcher yuklu degilse sistem bootstrap denemesi yapar; yine de ping alinamazsa manuel mudahale iste.

## Outputs
- `ensure_autocad_ready` sonucu: `ok`, `started_process`, `acad_path`, `backend`, `dispatcher_loaded`, `live_validation_available`, `warnings`
- QC raporunda `autocad_live_check`

## Quality Bar
- AutoCAD acilamadi diye deterministic DXF/NC1 uretimi durmaz.
- Canli AutoCAD dogrulamasi yoksa bu durum acik raporlanir.
- Dusuk guvenli PDF geometrisi icin tahmin uretilmez; `manual_review_required` ayridir.

## Tools
- `autocad-mcp` MCP `system(operation="ensure_autocad_ready")`
- `autocad_mcp.autocad_ready.ensure_autocad_ready_async`
- `autocad_mcp.technical_office.autocad_live.run_autocad_live_validation`

## Integration
- `IS_DAGITIMI.md` oncesinde calisir.
- `CIZIM_NC_KALITE_KONTROLU.md` canli AutoCAD durumunu QC raporuna yazar.
