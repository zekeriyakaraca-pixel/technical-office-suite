# PDF Genel Çıkarım Kuralları

Bu dosya her PDF analizinde okunur.

---

## 0. Ön İşleme Çıktılarını Kullan

cizim-on-islemci'nin ürettiği dosyalar mevcuttur — önce bunları oku:

```
[proje]_page_N.png    → Read aracıyla aç → görsel analiz (semboller, boyutlar, kaynak işaretleri)
[proje]_geom.json     → boyut çizgisi ve yapısal sınır koordinatları
[proje]_tables.json   → BOM tablosu (varsa) — BOM kontrolü buradan
[proje]_spatial.json  → metin koordinatları — aks grid ve eleman etiketi konumları
[proje]_sections.json → section_labels — kesit görünüş etiketleri
```

---

## 1. Çıkarılacak Bilgiler

**Aks Sistemi:**
- Yatay aks adları ve açıklıkları (mm)
- Düşey aks adları ve açıklıkları (mm)
- Kat kotları (m veya mm)

**Taşıyıcı Sistem Elemanları** (her eleman için):
- Eleman tipi: kolon / kiriş / döşeme / temel / perde / merdiven / çapraz
- Eleman adı / referansı (PDF'teki etiket)
- Profil (HEA200, IPE300, RHS100×5, vb.)
- Malzeme (S235, S355, C25/30, vb.)
- Başlangıç koordinatı (aks + kot)
- Bitiş koordinatı (aks + kot)
- Faz (varsa)

**Malzeme Tablosu** (PDF'te varsa):
- Profil listesi ve adetler

**Notlar ve Belirsizlikler:**
- Okunamayan veya çelişkili bilgiler
- Bağlantı tipi belirsizse → varsayım yapma, insana sor

---

## 2. Görünüş Okuma Kuralları

### Levha Kalınlığı
Levha kalınlığını her zaman o levhanın kenardan göründüğü görünüşten oku.
**KRİTİK:** Yan görünüş ile detay kesit çelişirse → detay kesiti (A-A, B-B) kazanır.

### Stiffener Adedi — Okuma Önceliği
```
Adım 1 — Yan görünüşten pozisyon sayısını oku
Adım 2 — Kesit görünüşünden yüz sayısını oku
  Stiffener ön ve arka yüzde görünüyor → her pozisyonda 2 yüz
  Sadece bir yüz → 1 yüz
Adım 3 — Toplam = pozisyon × yüz sayısı
Adım 4 — Kesit yoksa üst/plan görünüşü:
  Düz çizgi → ön yüz VAR, Kesik → arka yüz VAR
```

### TYP. (Typical) Notasyonu
PDF'te "TYP." yazılmışsa → o detayı aynı profil/konum ilişkisine sahip tüm noktalara uygula.

### Revizyon Bulutları
Revizyon bulutları varsa → analiz önceliğini tamamen o bölgeye ver.

### Ölçek Hiyerarşisi
- Detay Kesitler (1:5, 1:10) → en güvenilir
- Tipik Planlar (1:50, 1:100) → genel yerleşim, detayda kesit kazanır

### Pah Geometrisi
N×M pah → her köşeyi ayrı uygula → sonuç poligon (dikdörtgen değil).

---

## 3. Metin-Öncelikli Kural
Geometri (DXF ölçüsü) ile yazılı metin çelişirse → yazılı metin kazanır.
Modelleme için her zaman yazılı boyutu kullan.

---

## 4. Analiz Doğrulama
Her analizde `skills/pdf/VERIFIKASYON.md` okunur ve kontroller yapılır.

---

## 5. Kalite Barı
- Tüm elemanlar kayıt altına alınmalı — atlanan eleman olmamalı
- "Sorular" bölümü boşsa PDF eksiksiz demektir; doluysa mutlaka doldurulmalı
