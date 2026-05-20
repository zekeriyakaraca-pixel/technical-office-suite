# Danieli / Endüstriyel Çizim Kuralları

Bu dosya yalnızca project_type: "danieli" (Danieli, SMS, Primetals, Metso vb.) için okunur.

---

## Tek Assembly Kuralı
- Tüm parçalar **tek assembly** altında toplanır
- Alt assembly oluşturulmaz

## Boy Notasyonu (L1 / L2)
- **L1 (Net Boy):** Profilin asıl kesim boyu — modelleme için
- **L2 (Brüt/Dış Boy):** Alın levhaları dahil toplam → kontrol ölçüsü
- `(L2 - L1) / 2 = Levha Kalınlığı` — bu formülü doğrula

## Stiffener Sayımı
- Alt plan görünüşünde (aşağıdan bakış) en net görünür
- `NR. 8×3` → 8 eleman × 3 delik = 24 cıvata (toplam cıvata ÷ eleman başına = eleman sayısı)

## Danieli Kaynak Sembolü Notasyonu

| Sembol | Anlamı |
|--------|--------|
| `12.5` (üçgen yanında) | Yüzey pürüzlülüğü Ra=12.5 — kaynak boğazı DEĞİL |
| `4×50 (50)` | Kesikli köşe kaynağı: 4 adet × 50mm, 50mm aralıklı |
| `TH.5` / `TH.10` levha öneki | Kaynaklı bağlı — cıvatalı DEĞİL |
| `45×45° TYP.` | Tüm köşelerde 45° pah, tipik |
| Danieli STD 2.8.006 | Boğaz belirtilmemişse a = **0.7 × t_min** |

## Ağırlık Doğrulama
- Tekla tonajı ile çizimdeki `Weight (Kg)` karşılaştır
- Fark > %3 → eksik eleman veya hatalı levha boyutu — insana bildir

## Kesit Eşleştirme (KRİTİK)
- A-A = alt bağlantı plakası (base)
- B-B = üst bağlantı plakası (cap)
- Ters eşleştirme → Tekla'da aynı boyut farklı levhalara gider (proje 000-000-917-763 hatası)
