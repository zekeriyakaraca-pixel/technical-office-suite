# Dashboard Pipeline ve Görsel Analiz Akışı Düzeltmesi

## Summary
Sorun üç parçalı: Pipeline butonu deterministik blokajda görsel analizi otomatik tamamlamıyor, müdür chat eski/stale iş bağlamını gereksiz konuşuyor, görsel analiz aday ürettikten sonra `manual_review_required.json` hâlâ eski `plate_geometry_not_found` kayıtlarını aktif gösteriyor. Düzeltme, mevcut senkron akışı koruyacak ama pipeline/run, chat ve UI durum göstergelerini tek tutarlı iş akışına bağlayacak.

## Key Changes
- Pipeline butonu `/api/jobs/{job_id}/run` üzerinden tam akış çalıştıracak:
  - Deterministik pipeline çalışır.
  - `plate_geometry_not_found` gibi görsel adayla çözülebilecek manuel incelemeler varsa, müdür notu/soru oluşturmadan önce otomatik Codex görsel aday taraması başlatılır.
  - Görsel analiz bitmeden `notify_job_blocked`, manager notification veya “ne yapmak istersin?” sorusu oluşmaz.
  - Görsel adaylar üretildiyse iş `awaiting_approval` durumunda kalır; DXF/NC1 üretimi yine ancak müdür onayından sonra yapılır.
  - Görsel analiz hiç aday çıkaramazsa veya kapsanmayan gerçek sayfalar kalırsa ancak o zaman manuel inceleme notu/blokaj yazılır.

- Görsel analiz sonrası manuel inceleme temizliği:
  - Yeni bir reconciliation helper eklenecek: `manual_review_required` kayıtlarını mevcut `codex_candidates` ile karşılaştıracak.
  - Aynı PDF + sayfa + poz için aday üretildiyse o `plate_geometry_not_found` artık aktif manuel inceleme sayılmayacak.
  - `page_exclusions.json` ile atlanan sayfalar eksik görsel aday sayfası sayılmayacak.
  - `job_summary.json`, `manual_review_required.json` ve job detail cevapları aynı aktif manuel inceleme sayısını gösterecek.
  - Mevcut `d-28-20260513155258` işi de bu yeni okuma/yenileme mantığıyla 29 eski blokajı aktif göstermeye devam etmeyecek; sadece gerçekten çözülemeyen sayfalar kalacak.

- Müdür chat davranışı:
  - `nasılsın`, `merhaba`, `selam` gibi hafif sohbetler iş bağlamı taşımayacak; seçili job olsa bile otomatik uzun iş özeti veya eski hata anlatmayacak.
  - “Görsel analizden devam edelim” cevabı tamamlanmış senkron sonucu doğru anlatacak: “başlatıldı” değil, “tamamlandı / aday üretildi / şu kadar kayıt aktif kaldı”.
  - “Görünen manuel inceleme nedir?” cevabı stale dosyayı değil, reconciliation sonrası aktif kayıtları gösterecek.
  - Görsel analiz sonucu aday üretilmiş pozlar manuel inceleme listesinde “aktif blokaj” olarak dönmeyecek.

- UI ve event stream:
  - Görsel analiz sırasında FSM `extracting` olacak; header’daki `Aktif iş` sayacı ve iş pill’i bunu gösterecek.
  - Eski `completed: needs_manager_approval` event’i EventSource bağlantısını öldürmeyecek; aynı işte sonradan gelen görsel analiz/aday event’leri canlı görünecek.
  - Chat cevabı veya pipeline cevabı geldikten sonra seçili job detayı, aday listesi, çıktı listesi, iş listesi ve sistem sayaçları otomatik yenilenecek.
  - Pipeline butonu mesajı sonuç tipine göre değişecek: aday bekliyorsa “Görsel adaylar hazır, onay bekliyor”; gerçek tamamlandıysa “Pipeline tamamlandı”.

## Test Plan
- Runtime API testleri:
  - `/api/jobs/{job_id}/run`, `plate_geometry_not_found` manuel incelemede önce görsel analiz dener; analiz denenmeden manager notification yazmaz.
  - Görsel aday üretilen sayfalarda manual review aktif listeden düşer.
  - Page exclusion uygulanan sayfalar eksik aday kapsamına girmez.
  - Görsel aday varsa FSM sonucu `awaiting_approval`, aktif işlem sırasında `extracting` olur.

- Orchestrator/chat testleri:
  - `nasılsın` hafif sohbet olarak kalır, seçili iş özetini veya eski Codex hata bilgisini dökmez.
  - `görsel analizden devam edelim` cevabında “başlatıldı” yerine tamamlanan analiz sonucu ve kalan aktif blokaj sayısı bulunur.
  - Manuel inceleme detay cevabı reconciliation sonrası listeyi kullanır.

- Dashboard testleri:
  - HTML içinde chat/pipeline sonrası seçili job refresh helper’ı bulunur.
  - EventSource eski `needs_manager_approval` completed event’inde kapanmaz.
  - Pipeline butonu action message’ları aday/onay/tamamlandı durumlarını ayırır.

## Assumptions
- Görsel analiz ayrı background queue’ya taşınmayacak; mevcut senkron istek modeli korunacak.
- Görsel analiz aday üretse bile sistem otomatik onay/üretim yapmayacak; müdür onayı zorunlu kalacak.
- `plate_geometry_not_found` görsel adayla çözülebilir kabul edilecek; görsel analizden sonra hâlâ adayı olmayan gerçek plaka sayfaları aktif manuel inceleme olarak kalacak.
- Atlanmış kapak/profil sayfaları tekrar görsel analiz hedefi veya eksik aday sayfası yapılmayacak.
