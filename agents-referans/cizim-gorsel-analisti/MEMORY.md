# Hafıza: cizim-gorsel-analisti

Görsel analiz döngülerinden doğrulanmış örüntüler. Boş başlar — öğrenmeler gerçek projelerden birikecek.

<!-- ÖNEMLİ: Sadece birden fazla projede doğrulanmış örüntüleri yaz. Tek seferlik gözlemler journal'a. -->

## Microzoom — PyMuPDF ile Bölgesel Kırpma (doğrulandı 2026-04-18, proje 000-000-522-607)

*(cizim-analisti deneyiminden devralındı)*

- **Microzoom her zaman SORU-XXX'ten önce gelir.** PDF parser'ın metin çıktısı belirsiz semboller üretir (`P18`, `P35` gibi). Zoom bu belirsizlikleri sıfırlar.
- **Uygulama:** `fitz.Matrix(4, 4)` + `page.get_pixmap(clip=rect)` → PNG kaydedilir → Read tool ile görüntü okunur.
- **Çözülen belirsizlikler (000-000-522-607):**
  - `NR.4x P1812.5` → zoom ile → `NR.4x Ø18^12.5▽` (Ø18mm delik, Ra12.5 yüzey)
  - `NR.4x P35` → zoom ile → `NR.4x Ø35^12.5▽` (Ø35mm ankraj deliği, Ra12.5)
  - `12.5▽` → kalınlık değil, yüzey pürüzlülüğü sembolü (Ra 12.5 μm)
  - Plaka kalınlığı → kesit değil, **ana görünüş uç detayından** okunur (`15` boyutu)
- **Danieli boyut zinciri örüntüsü:** `15 (3885) 15 = 3915` → her iki uçtaki `15` = plaka kalınlığı (mm), parantez içi = net gövde boyu.
- **Zoom bölgeleri:** her kesit görünüşü (A-A, B-B) + üst/alt uç detayları ayrı ayrı kırpılmalı. Tek bölge yeterli değil.
- Microzoom sonrası global confidence 0.72 → **0.95** atladı. SORU açılmadı.

## Vision Provider Doğrulama Örüntüleri

*(Bu tablo gerçek projelerden doldurulacak)*

| Proje | Profil | Vision Provider Conf. | S3b_visual Conf. | İnsan kararı | Kazanan kaynak |
|-------|--------|---------------------|-----------------|--------------|----------------|
| _(ilk projeden sonra eklenecek)_ | | | | | |

## API Kullanılamazlık Yönetimi

*(cizim-analisti deneyiminden devralındı — proje 000-000-522-607, 2026-04-18)*

- **`api_available: false` → confidence_penalty: -0.10** uygulanır, pipeline çökmez.
- Bu durumda S3b_visual sinyali (microzoom görsel değerlendirmesi) birincil kaynak olur.
- `profil-yon-analisti` bu cezayı füzyon hesabında otomatik uygular.

## Rotation S3b_visual Okuma Kuralları

- **I-profil kesitinde:** Gövde yatay ise → TOP; gövde dikey ise → FRONT
- **"TOP VIEW" / "PLAN GÖRÜNÜŞ" etiketi görünürse:** Doğrudan TOP (confidence: 0.82)
- **"FRONT VIEW" / "ELEVATION" etiketi görünürse:** Doğrudan FRONT (confidence: 0.82)
- **İzometrik / 3D görünüm:** Rotation doğrudan okunamaz — kesit veya detay görünüşü aranmalı

## Analysis Confidence ≠ Model Confidence (doğrulandı 2026-04-30, 000-000-484-204_001_00)

- **`analysis_confidence`**: Bu agent'ın ürettiği skor — PDF/DXF çizim okuma kalitesini ölçer.
- **`model_confidence`**: Tekla'da üretilen modelin geometrik doğruluğu — insan görsel
  karşılaştırması ile belirlenir, bu agent tarafından üretilemez ve bilinmez.
- **Kritik fark:** `analysis_confidence = 0.87` yüksek görünse de Tekla modeli tamamen
  hatalı olabilir. 000-000-484-204_001_00: analysis=0.87, model_confidence≤0.40 (5 kritik hata).
- **Raporlama kuralı:** Çıktı dosyalarında `analysis_confidence` adını kullan.
  `model_confidence` alanını boş bırak — tekla-modelci aşaması sonrası insan onayıyla doldurulur.
- **Uyarı:** `global_confidence` etiketini analysis skoruna eşitle; model doğruluğunu
  temsil ettiği izlenimini vermekten kaçın.

## Son Güncelleme

2026-04-30 — Analysis vs model confidence ayrımı eklendi (000-000-484-204_001_00 retrospektifi).
2026-04-22 — İlk versiyon, cizim-analisti MEMORY.md'den microzoom ve görsel analiz girdileri devralındı.
