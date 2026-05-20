# Skill: Mikro Zoom Protokolu

## Purpose
PDF sayfalarindan gorsel aday uretmeden once kanit goruntuleri ve manifest olusturmak.

## Render Standard
- PyMuPDF kullanilir.
- Tam sayfa render korunur.
- Mikro-zoom render icin `fitz.Matrix(4, 4)` kullanilir.
- En az sayfa geneli ve kose/detay bolgeleri uretilir.
- Poligon kontur gereken sayfalarda mevcut kose/detay bolgelerine ek olarak sayfa 3x3 grid bolgeleri de kanit setine eklenir.
- Cikti klasoru `.state/codex-runs/<run_id>/microzoom/` altidir.

## Manifest Contract
Her render turu `_microzoom_manifest.json` uretir:
- `schema_version`
- `job_id`
- `created_at`
- `microzoom_valid`
- `render_scale`
- `full_page_images`
- `evidence_images`
- `source_pdf`
- `source_page`
- `region`

## Validity Rules
1. Manifest yoksa paket gecersizdir.
2. `microzoom_valid=false` ise gorsel aday kanitli sayilmaz.
3. Manifestte listelenen PNG dosyalari mevcut degilse stale kanit kabul edilir ve aday onaylanmaz.
4. Eski `_zoom_*.png` veya manifest disi PNG dosyalari kaynak kanit sayilmaz.
5. Her aday kendi `source_pdf` + `source_page` icin manifestte en az bir `evidence_images` kaydina baglanir.
6. `contour_type=polygon` icin vertex cikarma karari, manifestteki mikro-zoom/grid kanitlariyla desteklenmiyorsa uretilebilir kabul edilmez.

## Failure Behavior
- Render yoksa: `MICROZOOM_FAILED`.
- Paket eksik veya kalitesizse: `MICROZOOM_INSUFFICIENT`.
- Bu durumlarda DXF/NC1 uretimi degil, mudur onayli manuel/gorsel aday akisi isletilir.
