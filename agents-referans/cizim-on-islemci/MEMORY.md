# Hafıza: cizim-on-islemci

Parser çalışmalarından doğrulanmış örüntüler. Boş başlar — öğrenmeler gerçek projelerden birikecek.

<!-- ÖNEMLİ: Sadece birden fazla projede doğrulanmış örüntüleri yaz. Tek seferlik gözlemler journal'a. -->

## DXF Sınırlılıkları

*(cizim-analisti deneyiminden devralındı)*

- **TEXT/MTEXT = 0 ise profil isimleri okunamaz:** Confidence otomatik ≤50% düşer. Bu durumda PDF fallback zorunlu. (000-000-963-378: DXF %42, PDF %94) → DXF analizi başarısız olursa önce PDF ara.
- **Tekla 3D dışa aktarımı ≠ fabrikasyon DXF:** Fabrikasyon DXF'leri 2D çizim (ölçek 1:25 gibi); koordinat sistemi farklı. Ölçek faktörü hesaba katılmalı.

## PDF Tip Tanıma Örüntüleri

*(cizim-analisti deneyiminden devralındı)*

- **Danieli fabrikasyon çizimleri:** Tek assembly yaklaşımı — tüm parçalar tek çatı altında. Bina çelik yapısı değil, ekipman parçası.
- **Versiyon takibi:** Tek PDF'te birden fazla revizyon olabilir (Ver001, Ver002). Her versiyon farklı geometri/ağırlık içerebilir. Kapsam sorgulanmalı.
- **Parantezli boyutlar:** Referans değer, model boyutu değil — modele yansıtma.
- **NR. 8×3 formatı:** 8 eleman × 3 delik anlamına gelir; toplam/delik = eleman sayısı.
- **Alt plan görünüşü:** Stiffener sayımı için birincil referans; ön görünüş eksik sayabilir.

## Mode Karar Tablosu (Doğrulanmış)

| DXF var | TEXT/MTEXT | PDF var | Mode | Not |
|---------|-----------|---------|------|-----|
| Evet | > 0 | Hayır | DXF | En iyi senaryo |
| Evet | > 0 | Evet | DXF | PDF ek bilgi olarak kullanılır |
| Evet | = 0 | Evet | DXF+PDF_FALLBACK | Zorunlu fallback |
| Evet | = 0 | Hayır | BLOCKER | PDF olmadan devam edilemez |
| Hayır | — | Evet | PDF | Danieli projeleri genelde bu |

## Parser Crash Örüntüleri

*(Bu bölüm gerçek crash'lerden sonra doldurulacak)*

| Script | Hata | Çözüm | Proje |
|--------|------|-------|-------|
| _(ilk projeden sonra eklenecek)_ | | | |

## Son Güncelleme

2026-04-22 — İlk versiyon, cizim-analisti MEMORY.md'den DXF sınırlılıkları ve PDF tip tanıma girdileri devralındı.
