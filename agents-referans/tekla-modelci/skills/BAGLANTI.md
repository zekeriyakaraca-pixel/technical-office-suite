# Skill: BAGLANTI

## Amaç
Tekla'daki çelik iskelet modeline bağlantı bileşenlerini (taban levhası, kiriş-kolon birleşimi, gusset, çapraz bağlantıları) `put_components` ile eklemek.

## Hangi Hedefe Hizmet Eder
- Bağlantı uyumu — modeldeki bağlantı sayısı PDF'tekiyle >%90 örtüşmeli

## Önkoşullar
- `outputs/` içinde bu projeye ait onaylanmış modelleme özeti mevcut olmalı
- İnsan makroyu çalıştırmış ve MODEL_OLUSTUR'u onaylamış olmalı
- `tekla://connection_status` BAĞLI döndürmeli
- PDF Analiz çıktısında bağlantı bölümü dolu olmalı

## Girdiler
- `outputs/YYYY-MM-DD_tekla_modeller_analiz_[proje-adi].md` — Çelik Bağlantılar tablosu
- `outputs/YYYY-MM-DD_tekla_modeller_model_[proje-adi].md` — GUID listesi (hangi elemana bağlantı eklenecek)
- `tekla://components` — kullanılabilir Tekla bağlantı bileşenleri
- `tekla://components/{key}` — bileşen parametre şeması

## Çelik Bağlantı Türleri ve Tekla Bileşenleri

| Bağlantı Türü | Tekla Bileşeni (yaygın) | Açıklama |
|---------------|------------------------|----------|
| Taban levhası (ankrajlı) | `Column base plate (1004)` | Kolon tabanı |
| Kiriş-Kolon cıvatalı | `End plate (144)` | Başlık levhası |
| Çapraz-Çerçeve gusset | `Gusset plate (11)` | Çapraz bağlantı levhası |
| Kiriş-Kiriş cıvatalı | `Bolted moment connection (134)` | Sürekli kiriş |
| Kaynaklı birleşim | `Welded beam to column (28)` | Kaynaklı kiriş-kolon |

## Süreç

### 1. Bağlantı Listesini Hazırla

PDF analiz çıktısındaki "Çelik Bağlantılar" tablosunu oku:
```
Her satır için:
  - Bağlantı tipi → Tekla bileşeni eşleştir (yukarıdaki tablo)
  - Konum → Hangi elemanlar seçilecek (GUID ile)
  - Cıvata/Kaynak parametreleri → bileşen şemasından al
```

### 2. Bileşen Şemasını Al

Her bileşen için:
```
tekla://components → bileşen listesini al
tekla://components/{key} → parametre şemasını oku

Şemadan alınacaklar:
  - bolt_standard (cıvata standardı: ISO/DIN)
  - bolt_size (M16/M20/M24)
  - bolt_grade (8.8/10.9)
  - plate_thickness (levha kalınlığı, mm)
  - weld_size (kaynak boğaz kalınlığı a=)
```

### 3. Bağlantıları Uygula

Her bağlantı için:
```
# a) İlgili elemanları GUID ile seç
select_elements_by_guid(guids=[ana_eleman_guid, ikincil_eleman_guid])

# b) Bileşeni uygula
put_components(
  component_name="[bileşen adı]",
  component_parameters={
    "bolt_size": "M20",
    "bolt_grade": "8.8",
    "plate_thickness": "10",
    ...
  }
)

# c) Her uygulamadan sonra kontrol
→ Hata varsa: detayı kaydet, insana bildir
→ Başarılıysa: yeşil işaretle
```

### 4. Taban Levhası Özel Akışı

Taban levhası için önce kolonun taban GUID'ini al:
```
select_elements_by_filter(element_type="Column")
get_elements_properties(report_props_definitions=["NAME", "GUID"])

# Her kolon için:
select_elements_by_guid(guids=[kolon_guid])
put_components(
  component_name="Column base plate (1004)",
  component_parameters={
    "plate_length": "300",
    "plate_width": "300",
    "plate_thickness": "20",
    "bolt_size": "M24",
    "bolt_grade": "8.8",
    "anchor_count": "4"
  }
)
```

### 5. Doğrulama

Her bileşen uygulamasından sonra:
```
# Bileşenli elemanları sorgula
get_elements_properties(report_props_definitions=["NAME", "GUID"])

# Başarılı bağlantıları yeşile al
select_elements_by_guid(guids=[basarili_guid_listesi])
color_selected(red=0, green=200, blue=0)

# Başarısız / eksik bağlantıları kırmızıya al
select_elements_by_guid(guids=[basarisiz_guid_listesi])
color_selected(red=255, green=0, blue=0)

zoom_to_selection()
```

### 6. Çıktıyı Yaz ve Journal'a Logla

## Çıktılar
- `outputs/YYYY-MM-DD_tekla_modeller_baglanti_[proje-adi].md`

Çıktı formatı:
```markdown
# Bağlantı Modelleme Raporu: [Proje Adı]
Tarih: YYYY-MM-DD

## Özet
| Bağlantı Türü | PDF'teki | Modellenen | Başarısız |
|---------------|----------|------------|-----------|
| Taban levhası | 8 | 8 | 0 |
| Kiriş-Kolon (cıvatalı) | 24 | 22 | 2 |
| Gusset (çapraz) | 16 | 16 | 0 |
| **Toplam** | **48** | **46** | **2** |

## Uygulanan Bileşenler
| Bileşen | Konum | Cıvata/Kaynak | Durum |
|---------|-------|---------------|-------|
| Column base plate (1004) | K1 tabanı | M24/8.8, 4 adet | Başarılı |
| End plate (144) | K1-G1 | M20/8.8, 4 adet | Başarılı |
| Gusset plate (11) | K2-Ç1 | Kaynaklı a=8 | Başarılı |

## Başarısız / Manuel Müdahale Gereken Bağlantılar
| Bağlantı | Konum | Sorun | Önerilen Eylem |
|----------|-------|-------|----------------|
| End plate | K3-G7 | Geometri uyumsuzluğu | Kiriş kotunu kontrol et |

## Kullanılan Bileşenler (Tekla Katalog)
- Column base plate (1004): v2.3 parametreleri
- End plate (144): bolt_size=M20, bolt_grade=8.8

## Sonraki Adım
Bağlantı modelleme tamamlandı → ELEMAN_DOGRULA veya NUMARALANDIRMA çalışabilir.
```

## Kalite Barı
- PDF'teki her bağlantı tipi için en az bir bileşen denenmiş olmalı
- `put_components` öncesinde `tekla://components/{key}` ile şema okunmuş olmalı
- Başarısız bağlantılar kırmızı renkle işaretlenmiş ve raporda yer almalı
- Başarı oranı <%90 ise insana bildir
- İnsan onayı olmadan başlık levhası parametreleri (kalınlık, cıvata boyutu) değiştirme

## Araçlar (Tekla MCP)
- `tekla://connection_status` — bağlantı kontrolü
- `tekla://components` — bileşen kataloğu
- `tekla://components/{key}` — bileşen parametre şeması
- `select_elements_by_guid` — GUID bazlı eleman seçimi
- `put_components` — bileşen uygulama
- `remove_components` — hatalı bileşeni kaldırma
- `get_elements_properties` — doğrulama
- `color_selected` — durum renklendirmesi (yeşil=OK, kırmızı=sorun)
- `zoom_to_selection` — odaklanma

## Tırmanma Kuralları
- `tekla://components` boşsa veya bileşen bulunamazsa: insana sor, varsayım yapma
- Bileşen parametresi PDF'te belirsizse: insana sor
- `put_components` başarısız olursa: `remove_components` ile temizle, insana bildir
- Tüm taban levhası bağlantıları başarısız olursa: makro yolunu öner (insana talimat ver)

## Entegrasyon
- MODEL_OLUSTUR (iskelet onaylandı) sonrası, ELEMAN_DOGRULA veya NUMARALANDIRMA öncesi çalışır
- METRAJ_CIKART bağlantı levhası ağırlıklarını da raporlar (GUID ile)
- Tamamlanınca MEMORY.md güncellenir: hangi Tekla bileşeni hangi bağlantı tipine uymadı
