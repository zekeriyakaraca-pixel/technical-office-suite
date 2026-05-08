# Memory: autocad-uzman-2

## Confirmed Patterns

- Paralel üretim rolü: autocad-uzman-1 ile aynı yeteneklere sahip ikinci üretim mühendisiyim. Müdür journal kontrolüyle müsait olanı seçer.
- ezdxf headless üretim AutoCAD live olmadan da DXF 2013 sorunsuz çalışıyor.
- test-001 sistem testi başarılı; pipeline deterministic sonuç üretiyor.
- Bitirince journal'a "completed job X" kaydı yazarım ki müdür durumu görsün.

## Repeated Failure Modes

- Henüz kayıt yok (sistem yeni başlatıldı).

## PDF Layout Notes

- Düşük çözünürlüklü PDF'lerde geometri çıkarma başarısız olabilir → manual_review_required.
- Poz numaraları 3-6 rakam arasında değişir. Pipeline regex ile tanır.

## Paralel Kapasite Notu

- Çakışma önleme kuralı: aynı job_id için autocad-uzman-1 ile paralel çalışmak yasak.
- İş atanmadan önce job_summary.json status alanını kontrol et.
- Müdür beni boş bulursa (journal'da "started" kaydım yoksa) işi bana atar.
