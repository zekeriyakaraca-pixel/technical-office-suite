# PDF Bağlantı Detay Kuralları

Bu dosya yalnızca PDF'te çelik bağlantı detayı (cıvata, kaynak, levha) varsa okunur.

---

## Çelik Bağlantı Bilgileri (her bağlantı tipi için)

- Bağlantı tipi: kaynaklı / cıvatalı / karma
- **Cıvatalı ise:** Sınıf (8.8 / 10.9), çap (M16 / M20 / M24), adet, dizilim (2×2, 2×3 vb.)
- **Kaynaklı ise:** Tür (küt / köşe), boğaz kalınlığı a= (mm), kaynak boyu
- Levha bilgileri: end plate, gusset, taban levhası — boyut + kalınlık (mm)
- Ankraj: çap, sınıf, gömme derinliği (taban levhası varsa)

---

## Kaynak Boğaz Hesabı (kalınlık belirtilmemişse)

```
a = 0.7 × t_min
t_min = birleşimdeki en ince parçanın kalınlığı (mm)
```

---

## Boyut Okuma Kuralları

- Parantez içindeki boyutlar `(522)` → referans/hesaplanmış, ölçü değil
- `NR. 8×3` → 8 eleman × 3 delik = 24 toplam cıvata
- `nx50 (50)` → n adet delik, 50mm aralıklı, son delikten uca 50mm

---

## Çıktı Tablosu (analiz_[proje].md'e ekle)

| # | Bağlantı Tipi | Konum | Cıvata Sınıfı | Çap | Adet | Levha Boyutu | Kaynak |
|---|---------------|-------|---------------|-----|------|--------------|--------|
| 1 | Kiriş-Kolon (cıvatalı) | K1-G1 | 8.8 | M20 | 4 | 200×150×10 | — |
| 2 | Taban levhası (kaynaklı) | K1 tabanı | — | — | — | 300×300×20 | a=8 |
