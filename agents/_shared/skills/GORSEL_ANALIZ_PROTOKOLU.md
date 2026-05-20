# Skill: Gorsel Analiz Protokolu

## Purpose
Metin katmani bozuk, taranmis veya geometri okumasi eksik PDF'lerde plaka adaylarini kanitli sekilde cikarmak.

## Core Rule
Gorsel analiz sonucu uretim gercegi degildir. Sonuc yalnizca `approval_required=true` adaydir; `teknik-ofis-muduru` onayi ve QC `ok=true` olmadan DXF/NC1, partlist veya teslim acilmaz.

## Required Evidence
Her gorsel aday su alanlari tasir:
- `source_pdf`
- `source_page`
- `source_trace`
- `analysis_confidence`
- `uncertainties`
- `microzoom_manifest_path`
- `evidence_images`
- `approval_required=true`

## Decision Rules
1. Mikro-zoom manifesti yoksa gorsel karar verme; adayi `manual_review_required` olarak birak.
2. `analysis_confidence` yalnizca okuma guvenidir; model, DXF/NC1 veya QC guveni yerine gecmez.
3. Gorulmeyen olcu, delik, slot, malzeme veya adet uydurulmaz.
4. Poz numarasi sayfa numarasi degildir; cizimdeki mark/part/poz bilgisi kullanilir.
5. `10x10`, `20x20` gibi esit kose bosaltmalar `pah/chamfer` adayi olarak yazilir.
6. Poligon kontur veya pah goruluyorsa aday dikdortgen gibi onaylanmaz; `contour_type` ve `corner_reliefs` acik yazilir.
7. `contour_type=polygon` olan aday uretilebilir sayilmak icin `polygon_vertices` alaninda tum dis kontur koseleri CCW sirada, mm cinsinden ve `(0,0)=sol alt` referansiyla verilmelidir.
8. Poligon vertex listesi net okunamiyorsa listeyi tahmin ederek doldurma; `polygon_vertices=null`, dusuk guven ve acik `uncertainties` yaz.
9. Belirsiz geometri dusuk guven + acik `uncertainties` ile mudur onayina gider.
10. Shell komutu calistirma, dosya arama yapma veya ek dosya okuma denemesi yapma; runtime tarafindan verilen attached render ve mikro-zoom kanitlarini kullan.

## Response Style
Blokajlarda kullaniciya Python snippet, elle JSON veya dosya duzenleme talimati verme. Mudur formatina bilgi sagla:
- Neden
- Onerilen duzeltme
- Onay verirsen uygulayacagim

## Integration
- `PDF_POZ_OKUMA.md` gorsel adaylari bu protokole gore uretir.
- `MIKRO_ZOOM_PROTOKOLU.md` kanit goruntu paketini uretir.
- `PLAKA_GEOMETRI_CIKARMA.md` yalnizca mudur onayli adaylari `PlateSpec` girdisi sayar.
