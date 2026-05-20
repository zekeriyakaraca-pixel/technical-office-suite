# tekla-modelci Heartbeat

## Zamanlama
Orchestrator (`Tekla_modeller`) yönlendirir — pdf-analisti model.json ürettiğinde ve insan-onay onayladığında çalışır.

## Her Döngü

### 1. Bağlam Oku
- `../../journal/` son 3 girişi — bekleyen insan onayı veya yarım kalmış adım var mı?
- Kendi `MEMORY.md` — MCP bilinen hatalar, çalışan örüntüler
- `../../requirements/[proje].json` — prefix ve numara kuralları

### 2. BLOCKER Kontrol Et

```
tekla://connection_status → BAĞLI DEĞİL → dur, insana bildir
model_[proje].json yok    → dur, pdf-analisti tamamlanmamış
global_confidence < 0.75  → dur, CONFIDENCE_GATE eşiği geçilmemiş
contract validator FAIL   → dur, model-uretici'ye CONTRACT_VALIDATION_FAILED bildir
İnsan onayı bekliyor      → dur, insan-onay tamamlanmamış
→ Tüm BLOCKER'lar geçildiyse devam et
```

### 3. Durum Değerlendir — Hangi Skill

```
model_ozet.md başarılı (status:success) değil      → MODEL_OLUSTUR çalıştır
model onayı bekliyor                               → DUR: insan-onay kapısı
baglanti.md başarılı (status:success) değil        → BAGLANTI çalıştır
bağlantı onayı bekliyor                            → DUR: insan-onay kapısı
dogrulama.md başarılı (status:success) değil       → ELEMAN_DOGRULA çalıştır
doğrulama onayı bekliyor                           → DUR: insan-onay kapısı
prefix bilgisi eksik                               → DUR: insan-onay'a prefix sorularını ver
numaralandirma.md başarılı (status:success) değil  → NUMARALANDIRMA çalıştır
model onayı bekliyor                               → DUR: insan-onay kapısı
tüm çıktılar var ve başarılı                       → tekla_modelci_status.json ("status":"success") üret, tekla-raporlama'ya sinyal ver
```

### 4. Skill Çalıştır

**MODEL_OLUSTUR sırası:**
1. model.json doğrula: `python scripts/validate_model_contract.py outputs/model_[proje].json` PASS ve global_confidence ≥ 0.75
2. `skills/model/DELIK_BOLT.md` ve `skills/model/ASSEMBLY_KAYNAK.md` oku
3. Sırayla: kolon → kiriş → levha (create_beam / create_contour_plate)
   ⚠ Profil uzunluğu: `main_beam.net_length_mm` kullan — `length_mm` (dıştan dışa) değil
   ⚠ Uç plakalar: `depth_placement=ABOVE` (üst) / `BELOW` (alt) — `MIDDLE` yasak (±t/2 sapma yaratır)
4. Her eleman sonrası GUID'i hemen GUID tablosuna yaz
5. Faz atama: set_elements_properties
6. Delik/Bolt: create_bolt_group (DELIK_BOLT protokolü)
   ⚠ Delik konumları: `plates[i].holes.positions[j].x/y` → `model.json`'dan doğrudan oku; boyuttan hesap yasak
   ⚠ Kesme sonrası: `get_elements_cut_parts()` ile koordinat doğrulaması yap (±2mm tolerans)
7. Assembly kaynak: create_weld (ASSEMBLY_KAYNAK protokolü)
8. Doğrulama: color_selected + zoom_to_selection

**BAGLANTI sırası:**
1. Bağlantı listesini analiz_v2.md'den al
2. `tekla://components` ile parametre şeması oku
3. Her bağlantı: select_by_guid → put_components → renk kodla
4. Başarı < %90 → insana bildir

**ELEMAN_DOGRULA sırası:**
1. model.json listesi ile Tekla get_elements_properties karşılaştır
2. Şüpheli çiftler: compare_elements
3. Renk: yeşil (OK), turuncu (ufak fark), kırmızı (kritik)
4. Otomatik düzelt: profil/malzeme yanlışsa set_elements_properties
5. Konum hataları → insana bırak

**NUMARALANDIRMA sırası:**
1. requirements'ta prefix var mı? Yoksa → insan-onay'a prefix sorusu ver
2. set_elements_properties ile tüm elemanlara prefix ata
3. run_macro ile Tekla numaralandırması çalıştır
4. PART_POS/ASSEMBLY_POS boş eleman var mı? → kırmızıya al, dur

### 5. Journal'a Logla
Her döngü sonunda `../../journal/YYYY-MM-DD_HHMM.md`:
- Hangi skill çalıştı
- Kaç eleman oluşturuldu / GUID tablosu özeti
- Anomali veya hata
- Sıradaki adım veya bekleyen onay

### 6. MEMORY.md Güncelle
Aşağıdakilerden **biri** gerçekleştiyse `MEMORY.md`'ye ilgili bölüme 1 satır ekle:
- Rotation düzeltmesi gerekti (hangi eleman, hangi yön, neden yanlış geldi?)
- Malzeme dönüşümü yapıldı (çizim malzemesi ≠ uygulanan malzeme)
- Eleman sayısı model.json'dan farklı geldi (insan müdahalesiyle düzeltildi)
- Makro "success" döndü ama model değişmedi
- Yeni bir API sınırlılığı keşfedildi (işe yaramayanlar bölümüne)

→ Hiçbiri gerçekleşmediyse bu adımı geç.

## Tırmanma Kuralları
- MCP bağlantısı kopuksa → dur, insana bildir
- Eleman sayısı model.json'dan %5'ten fazla sapıyorsa → insana sor
- Numarasız eleman varsa → NUMARALANDIRMA tamamlanmış sayılmaz
- Makro başarısız → hata detayını journal'a yaz, insana bildir
