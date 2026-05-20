# BOM (Malzeme Listesi) Kontrolü

---

## 1. Parser BOM'u Oku
- `data/imports/[proje]_tables.json` → Mark, Quantity, Profile, Length, Material

## 2. Vision Provider BOM Karşılaştırması
- `data/imports/[proje]_gorsel_analiz.json` → vision_bom (varsa), yoksa claude_bom fallback
- Parser BOM = Vision BOM → güven yüksek
- Çakışma → SORU-XXX: "Parser BOM: [...], Vision BOM: [...] — hangisi doğru?"

## 3. Çizim BOM → Tespit Edilen Eleman Çapraz Eşleşmesi
Her poz numarasının toplam adedi BOM'daki adetle tutuyor mu?
- Eksik: "BOM'da 4 adet HEA200, planda 3 adet tespit edildi"
- Fazla: "Planda 5 adet C1, BOM'da 4 adet"
- Profil tutarsızlığı: "K1 BOM'da IPE300, planda IPE270"

## 4. Kural
BOM tablosu birincil kaynak — otomatik düzeltme yapma, uyumsuzluğu SORU-XXX ile insana sun.
