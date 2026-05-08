# Memory: kalite-kontrol

## Confirmed QC Patterns

- AutoCAD live yokken: autocad_live_check: "skipped" yaz, ok=true devam edebilir. Bu bir hata değildir.
- Boyut uyuşmazlığı eşiği: ±0.5 mm (plaka boyutları), ±0.1 mm (delik konumları) — bu eşiği aşan ölçüler QC ok=false.
- test-001 doğrulaması: Pipeline çıktısı QC kontrolünden geçti; deterministic üretim tutarlı sonuç veriyor.
- Her poz için ayrı `<poz_no>_qc.json` üretilir.

## Repeated Failure Modes

- Henüz kayıt yok (sistem yeni başlatıldı).

## Live Validation Notes

- Bağımsız doğrulayıcı rolü: DXF/NC1 asla üretmem, yalnızca kontrol ederim.
- ok=false durumunu müdüre journal kaydıyla bildirim: hangi poz, hangi uzman, neden hata.
- Delik çapı toleransı için QC spec bekleniyor (henüz tanımlanmamış — manual review yolu kullanılıyor).
