# Şirket Standartları — Teknik Ofis

> Bu dosya yalnızca okunur. Güncelleme için teknik-ofis-muduru'na öneri sun.

## Üretim Standartları

### DXF Formatı
- Format: DXF 2013 (AC1027)
- Birim: milimetre (mm)
- Katmanlar: kontur için `OUTLINE`, delikler için `HOLES`
- Tolerans: Plaka boyutları ±0.5 mm, delik konumları ±0.1 mm

### NC1 Formatı
- Format: DSTV NC1
- Dosya uzantısı: `.nc1`
- Makine uyumluluğu: Plazma / lazer kesim tezgahları
- Her poz için ayrı NC1 dosyası gereklidir

### QC Süreci
- QC raporu (JSON) her poz için zorunludur
- `ok=false` → yeniden üretim; teslim yapılamaz
- AutoCAD live bağlantısı yoksa `autocad_live_check: "skipped"` yazılır — bu başarısızlık değildir
- QC onayı olmadan partlist'e poz eklenemez

### Partlist (ERT Formatı)
- Format: Excel (.xlsx), sheet adı: `Part_List_holes`
- Yalnızca QC ok=true olan pozlar dahil edilir
- B.ALAN ve B.AĞIRLIK eksikse satır eklenmez; `partlist_manual_review_required.json` üretilir

## İş Akışı Kuralları

- Yeni PDF işi 24 saat içinde bir AutoCAD uzmanına atanmalıdır
- İki uzman aynı iş üzerinde çalışamaz (job_summary.json status kontrol edilmeli)
- Belirsiz PDF → `manual_review_required` → insan müdahalesi → yeniden gönder
- Gecikme 3 günü geçerse müdür escalation açar

## Malzeme Kodları

| Kod | Malzeme |
|-----|---------|
| S235 | Yapısal çelik, standart |
| S275 | Yapısal çelik, yüksek dayanım |
| S355 | Yapısal çelik, yüksek mukavemet |

## Dosya Yolları

| İçerik | Yol |
|--------|-----|
| İş girdileri | `workspace/imports/jobs/<job_id>/` |
| İş çıktıları | `workspace/outputs/jobs/<job_id>/` |
| Haftalık rapor | `workspace/outputs/haftalik_ozet.md` |
| Journal | `journal/entries/` |
