# Skill: DSTV NC1 Uretimi

## Purpose
Plaka icin `PlateSpec` ara modelinden DSTV NC1 dosyasi olusturmak.

## Serves Goals
- DXF ile ayni poz numarasina sahip makine cikti dosyasi uretmek.

## Inputs
- `PlateSpec`

## Process
1. `PlateSpec.validate()` sonucunu kontrol et.
2. Poz no, malzeme, kalinlik ve adet bilgisini baslik bolumune yaz.
3. Dis konturu `AK` bolumunde saat yonunde yaz.
4. Koordinatli delikleri `BO` bolumunde yaz.
5. Dosyayi `outputs/jobs/<job_id>/<poz_no>/<poz_no>.nc1` yoluna kaydet.

## Outputs
- `<poz_no>.nc1`

## Quality Bar
- Dosya `ST` ile baslar ve `EN` ile biter.
- Poz numarasi DXF dosya adi ile ayni olmalidir.
- Delik koordinati bilinmiyorsa delik tahmini yapilmaz.

## Tools
- `autocad_mcp.technical_office.nc1_writer.write_plate_nc1`

## Integration
- `CIZIM_NC_KALITE_KONTROLU.md` tarafindan dogrulanir.
