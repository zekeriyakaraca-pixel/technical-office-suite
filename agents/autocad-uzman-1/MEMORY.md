# Memory: autocad-uzman-1

## Confirmed PDF Layout Patterns

- ezdxf headless üretim AutoCAD live bağlantısı olmadan da DXF 2013 (AC1027) sorunsuz üretiyor.
- Poz numarası formatı: sayısal, 3-6 basamak (örn. 1001, 20045). Pipeline regex ile tanır.
- test-001 iş akışı başarıyla tamamlandı — deterministic pipeline tutarlı DXF + NC1 üretiyor.
- Birden fazla poz aynı PDF sayfasında olabilir; pipeline her pozu ayrı nesne olarak işler.

## Geometry Extraction Lessons

- Düşük çözünürlüklü veya taranmış PDF'lerde geometri çıkarma başarısız olabilir → manual_review_required.
- Delik toleransı: ±0.1 mm. Daha büyük sapma QC ok=false anlamına gelir.
- Plaka boyut toleransı: ±0.5 mm.
- autocad_live_check: "skipped" yazılması QC başarısızlığı değildir; AutoCAD live yoksa bu beklenen davranış.

## Manual Review Triggers

- PDF geometrisi belirsiz veya eksik → manual_review_required.
- Poz no tespit edilemiyor → manual_review_required.
- Malzeme kodu PDF'de yok → QC'ye ilet, müdür inceleyecek.

## Paralel Kapasite Notu

- autocad-uzman-2 ile aynı job_id üzerinde çalışma. İş atanmadan önce job_summary.json status kontrol et.
- Farklı job_id'ler için paralel çalışma tamamen güvenli.
