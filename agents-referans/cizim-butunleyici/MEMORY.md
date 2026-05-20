# Hafıza: cizim-butunleyici

Analiz birleştirme döngülerinden doğrulanmış örüntüler. Boş başlar — öğrenmeler gerçek projelerden birikecek.

<!-- ÖNEMLİ: Sadece birden fazla projede doğrulanmış örüntüleri yaz. Tek seferlik gözlemler journal'a. -->

## Polygon Levha Geometrisi (Stiffener / Bağlantı Plakası)

*(cizim-analisti deneyiminden devralındı)*

- **Stiffenerlar ve bağlantı plakaları her zaman dikdörtgen değildir.** DXF'te polyline nokta sayısı >4 ise mutlaka poligon — köşe kesimi, kaynak boşluğu (coping) veya profil içi kesim olabilir.
- **model.json'a sadece dış ölçüler (genişlik × yükseklik) yazmak yetersiz.** DXF polyline'dan sıralı x,y,z noktaları çıkarılmalı; `contour_points: [{x,y,z}, ...]` formatında yazılmalı.
- Düşük polyline nokta sayısı (4 nokta) → dikdörtgen kabul edilebilir. 5+ nokta → polygon zorunlu.

## Kesit Görünüşleri — Plaka Boyutu Eşleştirmesi

*(cizim-analisti deneyiminden devralındı)*

- **Her kesit görünüşünü (A-A, B-B vb.) ilgili plakaya bire bir eşleştir.** Kesit ismi hangi elemana ait olduğunu açıkça belirt. Eşleştirme yanlışsa Tekla'da farklı levhalar aynı boyutta çizilir.
- **Danieli fabrikasyon çizimlerinde:** A-A = alt bağlantı plakası (base), B-B = üst bağlantı plakası (cap). v1/v2 analizinde bu ters eşleştirilmişti — model.json v3'te düzeltildi. Bir dahaki projede kesit ismi + plaka konumunu PDF'ten çapraz doğrula.

## Profil Eşleştirme Öğrenmeleri

*(cizim-analisti deneyiminden devralındı)*

| Çizimde | Tekla'da | Kaynak | Proje |
|---------|----------|--------|-------|
| IPBL240 | HEA240 | DIN 1025-3 — ağırlık doğrulama: HEA240≈355 kg, HEB240≈491 kg, hedef 445 kg → HEA240 ✓ | 000-000-917-763 |

- **Ağırlık doğrulama yöntemi:** Profil belirsizse her adayı modelle, ağırlığı karşılaştır. Hedef ağırlığa %2'den yakın olan doğru profil.

## Delik Konumları — Kritik Doğrulama

*(cizim-analisti deneyiminden devralındı — 2026-04-10)*

- **Delik koordinatları en kritik çıktıdır.** Yanlış konumda delik = ıskarta parça.
- 4 zorunlu kontrol (HEARTBEAT.md Adım 9'da detaylı):
  1. Sayı kontrolü (PDF delik adedi = model adedi?)
  2. Sınır kontrolü (merkez + yarıçap ≥ 1×çap uzak)
  3. Aralık kontrolü (delikler arası ≥ 2×çap)
  4. Simetri kontrolü (PDF simetrikse koordinatlar da simetrik mi?)
- 000-000-955-617'de delik konumları PDF'e uymuyordu — kontroller yapılmamıştı.

## Danieli Standart Notları

*(cizim-analisti deneyiminden devralındı)*

- Kaynak boğazı = **0.7 × t_min** (Danieli STD 2.8.006)
- Tek assembly yaklaşımı — tüm parçalar tek çatı altında
- A-A = alt plaka (base), B-B = üst plaka (cap) — ters okuma hatası riski

## Codex Kurtarma Örüntüleri

*(Bu bölüm gerçek Codex denemelerinden sonra doldurulacak)*

| Tetikleyici | Codex Görevi | Sonuç | Proje |
|-------------|-------------|-------|-------|
| _(ilk proje sonrası eklenecek)_ | | | |

## Güven Skoru Örüntüleri

*(cizim-analisti deneyiminden devralındı)*

| Senaryo | Güven | Açıklama |
|---------|-------|----------|
| PDF, profil isimleri tam okunuyor | %84–94 | Danieli fabrikasyon PDF'leri |
| PDF + insan soruları yanıtlandı | %92–100 | SORU-XXX sonrası güven yükseliyor |
| DXF, TEXT/MTEXT yok | %42 | Profil isimleri okunamıyor |

## Plaka Kalınlığı — PDF'ten Bireysel Okuma Zorunlu (2026-04-27)

- **Her plakanın kalınlığı PDF'ten ayrı ayrı okunmalı; varsayılan, türetme veya kopyalama yasak.**
  - 000-000-484-202: BP=PL20, CP=PL10 iken model.json her ikisini PL25 olarak üretmişti.
  - `profile: "PL{thickness}"` alanı mutlaka PDF ölçüsüyle eşleşmeli.
  - Kalınlık belirsizse → `SORU-XXX` aç; varsayılan atama yasak.
- **model.json doğrulama:** `plates[i].thickness_mm` ile `plates[i].profile` tutarlı mı? (`PL20` → `thickness_mm: 20`)

## Taban Bölgesi Plaka Tespiti — Zorunlu Detay Tarama (2026-04-27)

- **Taban bölgesi (z=0'dan itibaren 200mm) her zaman detay microzoom kapsamında.**
  - Bu bölgede stiffener, geçiş plakası, haç levhası gibi elemanlar olabilir.
  - 000-000-484-202: Taban plakası üstündeki 10mm geçiş plakaları hiç modellenmedi.
- **Kural:** PDF'te taban yakınında herhangi bir yatay çizgi / plaka görünüyorsa → ayrı eleman olarak model.json'a yaz; atlamak yasak.
- Eğer PDF'te görülüp de boyutu bilinemiyorsa → `SORU-XXX` aç.

## İnsan Onay Kapısı İçin model.json Kontrol Maddesi (2026-04-27)

model.json teslim edilmeden önce aşağıdaki kontroller zorunlu:

- [ ] Tüm PDF plakaları model.json'da mı? (sayım kontrolü)
- [ ] Her plaka `thickness_mm` PDF ile bire bir mi?
- [ ] `net_length_mm` ve `gross_length_mm` ayrı ve doğru alanlar mı?
- [ ] Delik konumları PDF boyutlandırma çizgisinden mi okundu?
- [ ] Taban bölgesinde (0–200mm) ek eleman var mı?

## Son Güncelleme

2026-04-27 (2. güncelleme) — 000-000-484-202 insan retrospektifi: plaka kalınlığı bireysel okuma kuralı; taban bölgesi zorunlu tarama kuralı; insan onay kapısı kontrol maddesi eklendi.
