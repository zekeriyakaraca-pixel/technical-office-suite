# Memory: dokuman-kontrol

## Confirmed Formatting Lessons

- ERT partlist formatı: Excel (.xlsx), sheet adı `Part_List_holes`, A-K kolonları. Kaynak: templates/ERT_PARTLIST_FORMAT.md.
- T.ALAN ve T.AĞIRLIK sütunları Excel formülü ile doldurulur: `=+G{row}*E{row}`, `=+H{row}*E{row}`.
- Kolon genişlikleri: A=10, B=10, C=12, D=12, E=8, F=10, G=12, H=12, I=12, J=12, K=14.
- safe_project_name üretimi: özel karakter (`/ \ : * ? " < > |`) temizlenir.

## Partlist Delivery Lessons

- QC kapısı zorunlu: QC ok=false olan pozlar partlist'e eklenmez. Bu kural hiçbir zaman atlanamaz.
- B.ALAN veya B.AĞIRLIK eksikse satır eklenmez; `partlist_manual_review_required.json` üretilir ve müdüre bildirilir.
- Teslim edilen her partlist `outputs/jobs/<job_id>/` altına kaydedilir.
- Dosya adı: `<safe_project_name>_partlist.xlsx`.

## Repeated Blocking Issues

- Henüz kayıt yok (sistem yeni başlatıldı).
