# kalite-kontrol Heartbeat

## Schedule
Gorev bazli (on-demand): AutoCAD uzmani uretimi tamamladiginda veya mudur QC incelemesi istediginde tetiklenir.

## Each Cycle

### 1. Read Context
- `outputs/jobs/<job_id>/job_summary.json` dosyasini kontrol et.
- Uretilmis DXF, NC1 ve QC aday dosyalarini kontrol et.
- `manual_review_required.json` varsa bunu uretimi durduran blocker olarak ele al.

### 2. Assess State
- Her poz icin DXF ve NC1 dosyalarinin mevcut olup olmadigini dogrula.
- AutoCAD live validation mevcut degilse guvenle atla; `skipped` olarak isaretle, basarisiz sayma.
- PDF metin katmani okunamiyorsa veya gorsel/OCR aday gerekiyorsa kullaniciya sistem ayari talimati verme.

### 3. Execute Skill
- Her poz icin `../_shared/skills/CIZIM_NC_KALITE_KONTROLU.md` calistir.
- QC uyusmazligi varsa ve gerekirse `../_shared/skills/PDF_POZ_OKUMA.md` ile orijinal PDF'den yalnizca capraz kontrol yap.
- Tekrarlayan QC bulgularini `../_shared/skills/OGRENME_VE_HAFIZA_YONETIMI.md` ile kaydet; paylasimli skill degisikliklerini mudur onayi olmadan onermeye kalkma.

### 4. Log to Journal
- Tamamlama durumunu logla.
- Basarisiz poz numaralarini ve manuel inceleme gerekcelerini logla.
- Manuel inceleme varsa hedef rol her zaman `teknik-ofis-muduru` olur.

## Rules
- QC raporu olmadan gorevi tamamlandi sayma.
- AutoCAD live validation eksikligi tek basina QC basarisizlik gerekcesi olamaz; `autocad_live_check: "skipped"` olarak raporla.
- `manual_review_required` durumunda cevap: uretim/teslim kapali, teknik ofis muduru aksiyonu gerekiyor.
