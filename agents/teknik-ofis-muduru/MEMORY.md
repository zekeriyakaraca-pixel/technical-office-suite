# Memory: teknik-ofis-muduru

## Confirmed Workload Patterns

- **test-001 pilot iş:** DXF + NC1 + QC tüm akışı başarıyla tamamlandı. AutoCAD live=off modunda ezdxf headless pipeline sorunsuz çalışıyor.
- **Paralel kapasite:** autocad-uzman-1 ve autocad-uzman-2 aynı anda farklı pozlar üzerinde çalışabiliyor. journal/ kontrolü ile çakışma önleniyor.
- **Genel sorular:** "sistemi anlat", "çalışanlar kimler" gibi genel sorularda tool döngüsüne girme — doğrudan bilgi ver.
- **Yeni iş geldiğinde:** Önce `inspect_job` ile input kontrol et, sonra müsait uzmanı belirle, sonra `IS_DAGITIMI.md` skill'ini çalıştır.

## Approved Skill Promotions

- `OGRENME_VE_HAFIZA_YONETIMI`: Tekrarlayan QC bulgularını MEMORY.md'ye taşıma prosedürü onaylandı.
- `IS_DAGITIMI`: Paralel uzman kapasitesi için journal kontrolü adımı onaylandı ve HEARTBEAT.md'ye eklendi.

## Rejected Learning Candidates

- ~~PDF geometrisi belirsiz olduğunda tahmin yap~~ → YASAK. Manual review kuralı her zaman geçerli.
- ~~AutoCAD live yoksa QC'yi başarısız say~~ → YANLIŞ. `skipped` yaz, başarısızlık değil.

## Hata Bildirimi Kararları

- QC ok=false döndüğünde: sorumlu uzmanı belirt, poz no ve hata nedenini raporla, yeniden üretim için talimat ver.
- Job input klasörü yoksa: `_job_not_found` yanıtı kullan, mevcut işleri listele.
