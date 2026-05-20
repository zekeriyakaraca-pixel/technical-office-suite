# Hafıza: profil-yon-analisti

Rotation tespitinden doğrulanmış örüntüler. Boş başlar — öğrenmeler gerçek projelerden birikecek.

<!-- ÖNEMLİ: Sadece birden fazla projede doğrulanmış örüntüleri yaz. Tek seferlik gözlemler journal'a. -->

## L / Asimetrik Profillerde Rotation + Delik Bacağı Belirsizliği — KRİTİK

*(cizim-analisti deneyiminden devralındı — proje 000-000-955-615, 2026-04-15)*

- **L profil analizinde `güven(delik_bacağı) < 0.90` ise `rotation` da belirsiz sayılır.**
  İkisi bağımlı: hangi bacakta delik olduğu bilinmeden, profilin hangi yönde durduğu da belirlenemez.
  Bu durumda **güven skoru ne olursa olsun** SORU-XXX açılır; "blok etkisi düşük" gerekçesiyle devam edilemez.
- **İsometrik görünümlü DXF/PDF'lerde rotation doğrudan okunamaz.** 3D görünümden yönü tespit etmek güvenilmez — kesit veya detay görünüşü aramak zorunda.
- **Hata zinciri:** rotation belirsiz → modelleme varsayılan → delik Y/Z koordinatları yanlış bacağa denk gelir → `performed_cuts=18` başarılı görünür → parça ıskarta → proje sıfırdan yapılır.
- `rotation_analyzer.py` L profillerinde zorunlu çalıştırılmalı. Sonuç < 0.90 güven ise analiz raporuna "rotation belirsiz — SORU-XXX açılacak" notu bırak.

## Rotation Analizi — DXF Yoksa PDF Üzerinden

*(cizim-analisti deneyiminden devralındı — proje 000-000-522-607, 2026-04-18)*

- **`rotation_analyzer.py` v3 DXF ve PDF modunu destekler.** DXF varsa `proje.dxf`, yoksa `--pdf proje.pdf` ile çalıştırılır.
- **PDF modunda aktif sinyaller:** S1 profil kuralı + S2 spatial/sections etiket + S3b microzoom PNG. S3 (DXF geometrisi) devreye girmez.
- **S3b_visual:** Script PNG üretir, `cizim-gorsel-analisti` agent zaten okumuş ve `_gorsel_analiz.json`'a yazmış — bu agent o dosyadan okur.
- **S5 çift simetri kontrolü her iki modda da çalışır.** HEB/HEA/SHS/RHS → b=h → rotation irrelevant.
- 000-000-522-607 örneği: `--pdf --profile HEB140` → S1=TOP(0.75) + S2=TOP(0.80) → oybirliği → TOP(0.85).
- **Rotation analizi tamamlanmadan model.json üretilemez.** `position.tekla_rotation_enum` boş bırakılamaz.

## Position (Depth / Lateral) Varsayılanları — ZORUNLU KURAL

*(cizim-analisti deneyiminden devralındı — proje 000-000-964-152, 2026-04-17)*

- **UPN, IPE, HEA, HEB, L ve benzeri standart profillerde `position.depth` her zaman `MIDDLE` yazılır.**
  Tekla `create_beam` aracı `depth` almaz ve varsayılan olarak `BEHIND` atar.
- model.json'a yazılması zorunlu (bu agent rotation.json'a yazar, model-uretici bundan okur):
  ```json
  "position": {
    "rotation_analiz_etiketi": "FRONT",
    "tekla_rotation_enum": "BELOW",
    "depth": "MIDDLE",
    "lateral": "MIDDLE"
  }
  ```
- `tekla_rotation_enum` boş bırakılamaz. Belirsizse SORU-XXX aç.

## Sinyal Güvenilirlik Tablosu

| Sinyal | Güvenilirlik | Notlar |
|--------|-------------|--------|
| Vision Provider (≥ 0.75) | **En yüksek** | Tek başına kesinleştirir |
| PDF kesit etiketi (S2) | 0.82 sabit | "TOP VIEW" / "FRONT VIEW" açık ise güvenilir |
| Spatial metin (S2b) | 0.78 sabit | Kesit etiketine alternatif |
| DXF geometri (S3) | 0.80-0.88 | Sadece DXF modda, Danieli'de bypass |
| Profil tipi kuralı (S1) | 0.55-0.75 | Son çare — tek başına yetersiz |

## Vision Provider Doğrulama Örüntüleri

| Proje | Profil | Vision Provider | Diğer sinyaller | İnsan kararı | Kazanan kaynak |
|-------|--------|--------------|----------------|--------------|----------------|
| _(ilk projeden sonra eklenecek)_ | | | | | |

## BEAM Elemanları — Üst Görünüm Zorunlu, TOP Varsayılanı (doğrulandı 2026-04-30, 000-000-484-204_001_00)

- **BEAM elemanı için rotation varsayılanı TOP.** rotation_analyzer FRONT döndürse bile
  üst görünüm (`zoom_uc_ust.png` veya çizimin plan/üst görünüşü) açılıp kontrol edilmeli.
- **Test sorusu:** "Üstten bakınca ne görünüyor?"
  - Flanş (yatay plaka yüzeyi) → **TOP** — doğru standart BEAM konumu
  - Web/gövde (ince dikey levha) → **FRONT**
- rotation_analyzer çıktısı ile üst görünüm çelişiyorsa → **insan onayı zorunlu**, modellemeye başlanmaz.
- **Cascade riski:** Bu kural kaçırılırsa rotation yanlış → tüm bağlantı koordinatları yanlış →
  delikler yanlış → NC1 geçersiz. Tek hata tüm çıktı zincirini geçersiz kılar.
- **Vision Provider Doğrulama tablosuna ekle:**

| Proje | Profil | rotation_analyzer | Üst görünüm | İnsan kararı | Kazanan |
|-------|--------|------------------|-------------|--------------|---------|
| 000-000-484-204_001_00 | HEB160 | FRONT (0.96) | Flanş görünür → TOP | TOP | Üst görünüm |

## Son Güncelleme

2026-04-30 — BEAM rotation TOP kuralı eklendi (000-000-484-204_001_00 retrospektifi).
2026-04-22 — İlk versiyon, cizim-analisti MEMORY.md'den rotation ile ilgili girdiler devralındı.
