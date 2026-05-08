# Skill: Plaka Geometri Cikarma

## Purpose
PDF metni ve poz listesinden uretilebilir `plate_spec` ara modelini olusturmak.

## Serves Goals
- Plaka DXF ve NC1 uretiminde tek dogruluk kaynagi olusturmak.

## Inputs
- PDF poz okuma sonucu
- `PositionRecord` listesi

## Process
1. `poz_no`, genislik, yukseklik, kalinlik ve malzemeyi belirle.
2. Delik verisi koordinatli olarak verildiyse `HoleSpec` listesine ekle.
3. Kalinlik veya ana plaka olcusu eksikse manuel inceleme iste.
4. Birimi varsayilan olarak `mm` kabul et.
5. `PlateSpec.validate()` hatalarini uretime gecmeden once kontrol et.

## Outputs
- `PlateSpec`
- `ManualReview` hata kayitlari

## Quality Bar
- Koordinati bilinmeyen delik icin otomatik delik konumu uydurma.
- Plaka disina dusen delik veya negatif olcu uretime alinmaz.

## Tools
- `autocad_mcp.technical_office.plate_extractor.build_plate_specs`

## Integration
- Gecerli `PlateSpec` ciktilari DXF ve NC1 skill'lerine gider.
