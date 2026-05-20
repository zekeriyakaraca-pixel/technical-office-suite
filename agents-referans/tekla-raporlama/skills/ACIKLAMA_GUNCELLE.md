# Skill: ACIKLAMA_GUNCELLE

## Amaç
NC/DXF export sonrasında partlist Excel'indeki AÇIKLAMA sütununu (Deliksiz → Delikli) güncellemek.

## Hangi Hedefe Hizmet Eder
- Metraj doğruluğu — NC çıktısıyla örtüşen gerçek imalat bilgisi

## Tetikleyici
NC_EXPORT skill'i tamamlandıktan sonra **otomatik çalıştırılır** — insan ayrıca istemez.
İnsan `NC bitti`, `NC exportlandı` veya `delikli/deliksiz güncelle` derse de çalışır.

## Önkoşullar
- `agents/Tekla_modeller/outputs/[model-adi]_partlist.xlsx` mevcut olmalı
- `tekla://connection_status` BAĞLI döndürmeli

## Süreç

### Adım 1 — Cıvatalı Parçaları Tekla'dan Sorgula

Tekla'da bir parçanın "Delikli" olması = cıvata grubu (bolt group) tarafından delinmiş olması.

```
# Yöntem A: Cıvata filtresine sahip elemanları seç
select_elements_by_filter(
  standard_string_filters={
    "name": {"conditions": {"match_type": "Contains", "value": "BOLT"}}
  }
)
→ Sonuç > 0 ise: get_elements_properties(report_props_definitions=["PART_POS","ASSEMBLY_POS"])
→ Bu PART_POS değerleri "Delikli" parçalardır
```

> ⚠️ BOLT filtresi 0 döndürürse → **Yöntem B**'ye geç.

```
# Yöntem B: cut_elements_with_zero_class_parts çalıştır
# Ardından get_elements_cut_parts ile kesim bilgisi sorgula
# Cıvata kesimi olan parçalar → "Delikli"
```

> ⚠️ Her iki yöntem de başarısız olursa → **Adım 2**'ye geç (manuel liste).

### Adım 2 — Manuel Liste (Fallback)

Tekla sorgusundan sonuç alınamazsa insana göster:

```
Tekla'dan cıvata bilgisi alınamadı.
Hangi POZ NO'lar deliklidir? (virgülle gir, örn: P/5, P/12, P/31)
Ya da "hepsini delikli" / "hepsini deliksiz" yazabilirsin.
```

İnsan listesi onaylar → Adım 3'e geç.

### Adım 3 — Excel AÇIKLAMA Sütununu Güncelle

```python
import openpyxl

xlsx_path = "agents/Tekla_modeller/outputs/[model-adi]_partlist.xlsx"
wb = openpyxl.load_workbook(xlsx_path)
ws = wb.active

# Sütun 11 = AÇIKLAMA
# Başlık satırını atla (row 1), grup satırlarını atla (ADET sütunu boş)
for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
    poz_no = row[0].value        # sütun 1 = POZ NO
    adet   = row[4].value        # sütun 5 = ADET
    if poz_no is None or adet is None:
        continue  # grup başlığı veya TOPLAM satırı, atla
    if poz_no in delikli_poz_listesi:
        row[10].value = "Delikli"   # sütun 11 = AÇIKLAMA (0-tabanlı: index 10)

wb.save(xlsx_path)
```

`delikli_poz_listesi` = Adım 1'den veya Adım 2'den gelen POZ NO seti.

### Adım 4 — MD Dosyasını da Güncelle

`outputs/YYYY-MM-DD_tekla_modeller_ert_partlist_[model-adi].md` içindeki `Deliksiz` değerlerini
aynı POZ NO'lar için `Delikli` yap.

### Adım 5 — Journal'a Logla

```
Güncelleme: [X] parça Delikli olarak işaretlendi
Kaynak: Tekla MCP bolt sorgusu / Manuel liste
Dosya: [model-adi]_partlist.xlsx
```

## Çıktılar
- Güncellenmiş `outputs/[model-adi]_partlist.xlsx` (AÇIKLAMA sütunu düzeltilmiş)
- Güncellenmiş `outputs/YYYY-MM-DD_tekla_modeller_ert_partlist_[model-adi].md`
- Journal kaydı

## Kalite Barı
- TOPLAM satırı ve grup başlıkları değiştirilmemiş olmalı
- Excel kayıt sırasında hata yoksa `OK` yaz, hata varsa insana bildir
- Delikli parça oranı toplam elemanın %50'sini aşıyorsa insana sor (olası veri hatası)

## Araçlar (Tekla MCP)
- `tekla://connection_status` — bağlantı kontrolü
- `select_elements_by_filter` — cıvata/bolt filtresi
- `get_elements_properties` — PART_POS sorgulama
- `get_elements_cut_parts` — kesim/delik bilgisi

## Entegrasyon
- NC_EXPORT skill'i tamamlandıktan hemen sonra çalışır
- METRAJ_CIKART çıktısını günceller, sıfırdan partlist oluşturmaz
