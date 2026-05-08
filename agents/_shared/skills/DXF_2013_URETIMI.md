# Skill: DXF 2013 Uretimi

## Purpose
`PlateSpec` ara modelinden AutoCAD 2013 uyumlu DXF uretmek.

## Serves Goals
- AutoCAD uzmanlari icin basli, tekrar uretilebilir DXF ciktilari saglamak.

## Inputs
- `PlateSpec`

## Process
1. `PlateSpec.validate()` sonucunu kontrol et.
2. Yeni `ezdxf` dokumanini `R2013` olarak olustur.
3. Dis konturu `PLATE_OUTER` katmaninda kapali polyline olarak yaz.
4. Delikleri `PLATE_HOLES` katmaninda circle olarak yaz.
5. Poz no, kalinlik ve malzemeyi `PLATE_TEXT` katmaninda not olarak ekle.
6. Dosyayi `outputs/jobs/<job_id>/<poz_no>/<poz_no>.dxf` yoluna kaydet.

## Outputs
- AutoCAD 2013 DXF: `AC1027`

## Quality Bar
- DXF dosyasi `AC1027` olmalidir.
- Dis kontur kapali polyline olmalidir.
- Delik sayisi `PlateSpec.holes` ile ayni olmalidir.

## Tools
- `autocad_mcp.technical_office.dxf_writer.write_plate_dxf`
- Opsiyonel AutoCAD MCP acma/goruntu/plot kontrolu

## Integration
- `CIZIM_NC_KALITE_KONTROLU.md` tarafindan dogrulanir.
