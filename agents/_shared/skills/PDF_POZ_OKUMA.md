# Skill: PDF Poz Okuma

## Purpose
Karisik PDF kaynaklarindan poz numarasi, plaka olcusu ve uretim adaylarini guvenli sekilde cikarmak.

## Serves Goals
- AutoCAD uzmanlari icin PDF kaynakli plaka islerini baslatmak.
- Belirsiz, metin katmani bozuk veya taranmis PDF'leri otomatik uretimden once mudur onayina almak.

## Inputs
- `workspace/imports/jobs/<job_id>/input.pdf` veya job klasorundeki `*.pdf`
- Opsiyonel `positions.csv` veya `positions.json`
- Opsiyonel mudur onayli `approved_plate_specs.json`

## Process
1. PDF'deki metin ve vektor stream adaylarini cikar.
2. Her PDF icin `pdf_diagnostics.json` kaydini uret:
   - metin karakter sayisi
   - vektor operator/line/circle sayisi
   - metin kalite sinifi
   - `visual_text_required`, `text_layer_unreadable` veya standart okunabilirlik karari
3. Poz listesi varsa dosya adini ve poz eslesmesini listeden al.
4. Liste yoksa PDF metnindeki `POZ`, `POS`, `MARK` veya desteklenen title-block/table desenlerini kullan.
5. Metin az, bozuk veya yok ama vektor cizim yogunsa `poz_no_not_found` sayfa hatalari uretme:
   - `extraction_candidates.json` icine `approval_required=true` aday kaydi yaz.
   - `manual_review_required.json` icinde Turkce ve aksiyonlu `visual_text_required` veya `text_layer_unreadable` karari ver.
   - Poz numarasini sayfa numarasi olarak aciklama.
6. Gorsel aday gerekiyorsa once `MIKRO_ZOOM_PROTOKOLU.md` ile tam sayfa + mikro-zoom kanit paketi ve `_microzoom_manifest.json` uret.
7. Yerel OCR veya Codex/opsiyonel vision provider hazirsa yalnizca aday okuma icin kullan; dusuk guvenli adaydan dogrudan DXF/NC1 uretme.
8. Her gorsel aday `GORSEL_ANALIZ_PROTOKOLU.md` sozlesmesine uyar: `source_trace`, `analysis_confidence`, `uncertainties`, `microzoom_manifest_path`, `evidence_images`, `approval_required=true`.
9. Mudur `approved_plate_specs.json` ile poz/olcu/delik bilgisini onaylarsa uretim pipeline'i bu spec'leri kullanir ve QC kapisi aynen isler.

## Outputs
- `PlateSpec` adaylari
- `ManualReview` kayitlari
- `pdf_diagnostics.json`
- `extraction_candidates.json`

## Quality Bar
- Taranmis, metinsiz veya metin katmani bozuk PDF icin geometri tahmini yapma.
- Yerel OCR/vision sonucu yalnizca adaydir; mudur onayi olmadan uretim sayilmaz.
- Mikro-zoom manifesti ve `source_trace` olmayan gorsel aday onaya tasinmaz.
- Poz listesi veya `approved_plate_specs.json`, PDF'deki isimlendirmeden once gelir.
- Hata aciklamalari Turkce olur ve `poz_no` kavramini sayfa numarasi ile karistirmaz.

## Learning Rule
- Tekil PDF basarisizligi kalici skill degisikligi degildir.
- Mudur onayi ve QC `ok=true` ile kapanan gorsel/OCR adaylari journal'a ogrenme olayi olarak yazilir.
- Ayni oruntu en az iki dogrulanmis iste tekrar ederse `journal/skill_proposals/` altinda skill guncelleme onerisi acilir.

## Tools
- `autocad_mcp.technical_office.pdf_reader.extract_pdf_content`
- `autocad_mcp.technical_office.pdf_diagnostics.build_pdf_diagnostics`
- `autocad_mcp.technical_office.pdf_diagnostics.build_extraction_candidates`
- `autocad_mcp.technical_office.positions.load_position_records`
- `autocad_mcp.technical_office.approved_specs.load_approved_plate_specs`

## Integration
- `PLAKA_GEOMETRI_CIKARMA.md` skill'ine guvenli `PlateSpec` girdisi saglar.
- `GORSEL_ANALIZ_PROTOKOLU.md` ve `MIKRO_ZOOM_PROTOKOLU.md` gorsel aday kanit sozlesmesini belirler.
- `OGRENME_VE_HAFIZA_YONETIMI.md` ile QC kanitli tekrar eden oruntuleri skill onerisine tasir.
