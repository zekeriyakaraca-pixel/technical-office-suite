# Kaynak Sembolojisi Çözümleyici (Weld Decoder)

---

## 1. Kaynak Tipi
- **Dik Üçgen (Fillet):** Köşe kaynağı
- **V, U, Y veya İki Düz Çizgi:** Küt, tam penetrasyon veya kısmi penetrasyon
- **Daire (Weld All Around):** Etrafı dönen kaynak

## 2. Saha / Fabrika Ayrımı
- **Bayrak Sembolü:** Şantiye/Saha Kaynağı (Site Weld)
- Bayrak yoksa → Fabrika Kaynağı (Shop Weld)

## 3. Aralıklı (Kesintili) Kaynaklar
```
3x50(100) → 3 adet × 50mm uzunlukta kaynak, 100mm merkez-merkez aralık
```

## 4. Çıktı Formatı
`[KAYNAK TİPİ] - [BOYUT: a=6] - [UZUNLUK: Sürekli/Aralıklı] - [LOKASYON: Fabrika/Saha]`
