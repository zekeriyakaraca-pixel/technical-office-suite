# Skill: ELEMAN_DOGRULA

## Amaç
MODEL_OLUSTUR tamamlandıktan sonra modeldeki her elemanı analiz dosyasıyla karşılaştırarak uyumu doğrulamak; sorunları renk kodlu görsel rapor ve markdown çıktıyla belgelemek.

## Hangi Hedefe Hizmet Eder
- Doğru modelleme — eleman sayısı uyumu >%95 KPI'sını destekler

## Önkoşullar
- `outputs/` içinde bu projeye ait modelleme özeti mevcut olmalı
- `tekla://connection_status` BAĞLI döndürmeli
- İnsan makronun çalıştığını teyit etmiş olmalı

## Girdiler
- `outputs/YYYY-MM-DD_tekla_modeller_model_[proje-adi].md` — modelleme özeti (GUID listesi dahil)
- `outputs/YYYY-MM-DD_tekla_modeller_analiz_[proje-adi].md` — orijinal eleman listesi
- `tekla://phases` — faz bilgisi

## Bu Skill MODEL_OLUSTUR'dan Farkı Nedir?

MODEL_OLUSTUR kendi içinde hızlı doğrulama yapar.
ELEMAN_DOGRULA daha derin, sistematik bir kalite kontrolüdür:
- Her eleman tek tek `compare_elements` ile karşılaştırılır
- Referans elemanlarla çiftler oluşturulur
- Sonuçlar renk kodlu ve GUID bazlı raporlanır
- İnsan onayı için net bir "geçti/kaldı" özeti üretilir

## Süreç

### 1. Bağlantı ve Referans Elemanları Belirle
```
tekla://connection_status → BAĞLI
tekla://phases → fazları al
```

Analiz dosyasındaki elemanları 3 kategoriye ayır:
- **Doğrulanacak:** modelleme özetinde karşılığı var, GUID bilinen
- **Eksik şüpheli:** modelleme özetinde sorun olarak işaretlenmiş
- **Yeni:** analiz dışında model içinde gözlemlenen

### 2. Her Eleman Tipini Sistematik Doğrula

#### 2a. Kolon Doğrulama
> ⚠ `element_type` filtresi Tekla MCP'de çalışmıyor. Profil adıyla filtrele (bkz. tekla-modelci/MEMORY.md).
```
# HEA/HEB/HEM kolonlar için:
select_elements_by_filter(
  standard_string_filters={"profile": {"conditions": {"match_type": "Starts With", "value": "HE"}}}
)
get_elements_properties(report_props_definitions=["NAME", "PROFILE", "MATERIAL", "PHASE", "LENGTH", "GUID"])
```

PDF analiz listesiyle satır satır karşılaştır:
| Kontrol | Beklenen | Gerçek | Sonuç |
|---------|----------|--------|-------|
| Ad | K1 | K1 | ✓ |
| Profil | HEA200 | HEA200 | ✓ |
| Malzeme | S355 | S275 | ✗ |
| Faz | 1 | 1 | ✓ |

#### 2b. Şüpheli Eleman Çift Karşılaştırması

Profil veya malzeme uyumsuzluğu şüphesi varsa:
```
1. Referans eleman seç (GUID ile): select_elements_by_guid([guid_A])
2. Şüpheli elemanı seç (GUID ile): select_elements_by_guid([guid_B])
   → Her ikisini aynı anda seçmek için: select_elements_by_guid([guid_A, guid_B])
3. compare_elements(ignore_numbering=True)
4. differences_summary'yi oku → sorun kaydına ekle
```

#### 2c. Eksik Eleman Kontrolü
```
select_elements_by_filter(
  standard_string_filters={"name": {"conditions": {"match_type": "Is Equal", "value": "K5"}}}
)
→ Sonuç boşsa: eleman modelde yok → kritik sorun
```

### 3. Görsel Renk Kodlama

Tüm elemanları önce temizle (opsiyonel, önceki renklendirme varsa):
```
# element_type= çalışmıyor; profil adıyla filtrele:
select_elements_by_filter(
  standard_string_filters={"profile": {"conditions": {"match_type": "Starts With", "value": "HE"}}}
)
color_selected(red=0, green=200, blue=0)   # Tüm kolonlar yeşil başlar
```

Sorunlu elemanları kırmızıya al:
```
select_elements_by_guid(guids=[sorunlu_guid_1, sorunlu_guid_2, ...])
color_selected(red=255, green=0, blue=0)    # Sorunlu = kırmızı
```

Kısmen uyumlu (ufak fark, düzeltilebilir):
```
select_elements_by_guid(guids=[kismen_guid_1, ...])
color_selected(red=255, green=165, blue=0)  # Uyarı = turuncu
```

Görsel son kontrol:
```
select_elements_by_filter(
  standard_string_filters={"profile": {"conditions": {"match_type": "Starts With", "value": "HE"}}}
)
show_only_selected()
zoom_to_selection()
draw_elements_labels(label="Name")
```

### 4. Otomatik Düzeltme (Basit Sorunlar İçin)

Profil veya malzeme yanlışsa ve düzeltme açıksa:
```
select_elements_by_guid(guids=[sorunlu_guid])
set_elements_properties(
  profile="HEA200",   # doğru değer
  material="S355"     # doğru değer
)
get_elements_properties() ile doğrula
color_selected(red=0, green=200, blue=0)   # Düzeltildi → yeşil
```

Konum/geometri sorunlarını düzeltme — **insana bırak**.

### 5. Doğrulama Raporu Yaz ve Journal'a Logla

## Çıktılar
- `outputs/YYYY-MM-DD_tekla_modeller_dogrulama_[proje-adi].md`

Çıktı formatı:
```markdown
# Eleman Doğrulama Raporu: [Proje Adı]
Tarih: YYYY-MM-DD

## Özet
| Kategori | Sayı |
|----------|------|
| Toplam eleman (PDF) | XX |
| Doğrulanan (yeşil) | XX |
| Uyarı — ufak fark (turuncu) | XX |
| Sorunlu — kritik fark (kırmızı) | XX |
| Eksik (modelde yok) | XX |
| Genel uyum | %XX |

## Kolon Doğrulama
| Ad | GUID | Profil | Malzeme | Faz | Durum |
|----|------|--------|---------|-----|-------|
| K1 | abc-123 | HEA200 ✓ | S355 ✓ | 1 ✓ | Yeşil |
| K5 | def-456 | HEA200 ✓ | S275 ✗ | 1 ✓ | Kırmızı |

## Kiriş Doğrulama
| Ad | GUID | Profil | Malzeme | Faz | Durum |
|----|------|--------|---------|-----|-------|
| G1 | ghi-789 | IPE300 ✓ | S355 ✓ | 1 ✓ | Yeşil |

## Kritik Sorunlar (Kırmızı)
| Eleman | GUID | Sorun | Önerilen Eylem |
|--------|------|-------|----------------|
| K5 | def-456 | Malzeme S275, olmalı S355 | set_elements_properties ile düzelt |
| G7 | — | Modelde bulunamadı | Makro tekrar çalıştırılmalı |

## Otomatik Düzeltmeler
| Eleman | GUID | Düzeltme | Sonuç |
|--------|------|----------|-------|
| K5 | def-456 | S275 → S355 | Başarılı |

## İnsan Eylemi Gereken Sorunlar
- [ ] G7 modelde bulunamadı — geometri kontrolü gerekiyor
- [ ] D3 döşeme kontur noktaları eksik görünüyor

## Sonraki Adım
Tüm kırmızı ve turuncu sorunlar çözüldükten sonra insan son görsel onayı verir → METRAJ_CIKART çalışır.
```

## Kalite Barı
- Her eleman tipi için `get_elements_properties` GUID dahil çalıştırılmış olmalı
- Kritik sorun olan her eleman kırmızı renkte ve raporda yer almalı
- Otomatik düzeltme sonrası tekrar `get_elements_properties` ile teyit yapılmış olmalı
- Genel uyum <%95 ise insana bildir ve METRAJ_CIKART başlatma

## Araçlar (Tekla MCP)
- `tekla://connection_status` — bağlantı kontrolü
- `tekla://phases` — faz bilgisi
- `select_elements_by_filter` — tip bazlı seçim
- `select_elements_by_guid` — GUID bazlı seçim
- `get_elements_properties` — özellik okuma (GUID dahil)
- `set_elements_properties` — otomatik düzeltme
- `compare_elements` — çift karşılaştırma
- `color_selected` — renk kodlama (yeşil/turuncu/kırmızı)
- `draw_elements_labels` — görsel etiket
- `show_only_selected` — odaklı görünüm
- `zoom_to_selection` — odaklanma

## Entegrasyon
- MODEL_OLUSTUR sonrası, METRAJ_CIKART öncesi çalışır (opsiyonel ama önerilen)
- Çıktı METRAJ_CIKART'a "anomaliler" girdisi olarak kullanılabilir
- AGENT.md'deki >%95 uyum KPI'sının kontrol noktasıdır
- Tamamlanınca MEMORY.md güncellenir: hangi profil/malzeme hataları sık tekrarlandı
