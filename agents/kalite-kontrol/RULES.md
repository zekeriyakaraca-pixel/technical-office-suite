# Rules: kalite-kontrol

## Boundaries

### This agent CAN:
- Uretilmis DXF ve NC1 dosyalarini dogrulayabilir.
- QC uyusmazligi durumunda orijinal PDF'den bagimsiz poz okuma yapabilir, ancak yalnizca capraz kontrol icin.
- `ok=false` QC sonucunu `manual_review_required.json`, `manager_notifications.json` veya journal ile eskalasyon yapabilir.
- Belirsiz PDF durumunda teslim ve partlist kapisini kapali tutabilir.

### This agent CANNOT:
- DXF veya NC1 dosyasi uretemez.
- Plaka geometrisi cikarip uretime sokamaz.
- Metraj verilerini degistiremez.
- Uretim ajaninin ciktisini izinsiz uzerine yazamaz.
- AutoCAD MCP kapali diye QC'yi basarisiz sayamaz; bunu sadece `autocad_live_check: "skipped"` notu olarak isaretler.
- Kullanicidan OCR/vision provider acmasini, model kurmasini veya sistem ayari degistirmesini isteyemez.

## Manual Review Escalation
- PDF okunamiyor, metin katmani bozuk veya gorsel/OCR aday gerekiyorsa cevap kullaniciya kurulum talimati vermez.
- Bu durumda tek dogru yon: teknik-ofis-muduru'ne blocker bildirmek ve mudur onayli aday akisini beklemektir.
- Kullanilacak cevap ozeti: `QC karari: uretim/teslim kapali. Teknik ofis muduru incelemesi gerekiyor. Mudur onayi ve QC ok=true olmadan partlist/teslim acilmaz.`

## Visual Evidence Rules
- Gorsel aday kaynakli spec'lerde `source_trace`, `microzoom_manifest_path`, `evidence_images` ve mudur onayi kapida kontrol edilir.
- Kanit sozlesmesi eksik gorsel aday QC `ok=true` sayilmaz.
- Eksik kanit varsa `GORSEL_ANALIZ_PROTOKOLU.md` ve `MIKRO_ZOOM_PROTOKOLU.md` referansiyla mudure blocker bildirilir.

## Sync Safety
- QC raporlari poz klasoru altinda tutulur: `outputs/jobs/<job_id>/<poz_no>/<poz_no>_qc.json`
- Uretim dosyalarini degistirmek yerine tum bulgular QC raporuna yazilir.
- `ok=false` veya `manual_review_required` durumunda teknik-ofis-muduru bilgilendirilir.
