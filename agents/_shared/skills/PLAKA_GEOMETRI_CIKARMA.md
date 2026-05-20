# Skill: Plaka Geometri Cikarma

## Purpose
PDF metni ve poz listesinden uretilebilir `plate_spec` ara modelini olusturmak.

## Serves Goals
- Plaka DXF ve NC1 uretiminde tek dogruluk kaynagi olusturmak.

## Inputs
- PDF poz okuma sonucu
- `PositionRecord` listesi
- Mudur onayli ve kanitli gorsel adaylar (`source_trace`, `microzoom_manifest_path`, `evidence_images`)

## Process
1. `poz_no`, genislik, yukseklik, kalinlik ve malzemeyi belirle.
2. Delik verisi koordinatli olarak verildiyse `HoleSpec` listesine ekle.
3. Kalinlik veya ana plaka olcusu eksikse manuel inceleme iste.
4. Birimi varsayilan olarak `mm` kabul et.
5. `PlateSpec.validate()` hatalarini uretime gecmeden once kontrol et.
6. Gorsel adaydan gelen veri, `GORSEL_ANALIZ_PROTOKOLU.md` ve `MIKRO_ZOOM_PROTOKOLU.md` kurallarini tasimiyorsa `PlateSpec` girdisi sayilmaz.
7. `contour_type=polygon` olan gorsel aday, CCW sirali `polygon_vertices` olmadan `PlateSpec` girdisi sayilmaz.

## Outputs
- `PlateSpec`
- `ManualReview` hata kayitlari

## Quality Bar
- Koordinati bilinmeyen delik icin otomatik delik konumu uydurma.
- Plaka disina dusen delik veya negatif olcu uretime alinmaz.
- `analysis_confidence`, QC veya uretim guveni degildir; yalnizca okuma guveni olarak saklanir.
- Pah, poligon kontur veya kose bosaltma goruluyorsa bunu `corner_reliefs` ya da poligon notu olarak acik yazmadan dikdortgen kabul etme.
- Poligon vertex listesi net degilse geometri uydurma; aday `awaiting_approval` durumunda kalir.

## Tools
- `autocad_mcp.technical_office.plate_extractor.build_plate_specs`

## Integration
- Gecerli `PlateSpec` ciktilari DXF ve NC1 skill'lerine gider.
