# Skill: NUMARALANDIRMA

## Amaç
Tekla modelindeki tüm elemanlara ön ek ve başlangıç numarası atayarak Tekla'nın yerleşik numaralandırma fonksiyonunu çalıştırmak; PART_POS ve ASSEMBLY_POS değerlerini doğrulamak.

## Hangi Hedefe Hizmet Eder
- Doğru modelleme — numarasız eleman 2D çizimde markasız, metrajda pozisyonsuz kalır

## Neden Bu Skill Zorunludur
Tekla'da numaralandırma yapılmadan:
- `PART_POS` ve `ASSEMBLY_POS` değerleri boş veya "?" görünür
- 2D çizimler eleman markası olmadan üretilir
- Metraj raporunda pozisyon sütunları boş kalır
- Aynı profil/malzeme kombinasyonu olan elemanlar ayırt edilemez

## Önkoşullar
- `outputs/` içinde bu projeye ait onaylanmış eleman doğrulama veya modelleme özeti mevcut olmalı
- İnsan modeli görsel olarak onaylamış olmalı
- `tekla://connection_status` BAĞLI döndürmeli
- `project://requirements` içinde ön ek kuralları tanımlı olmalı (yoksa insana sor)

## Girdiler
- `outputs/YYYY-MM-DD_tekla_modeller_model_[proje-adi].md` veya `dogrulama_[proje-adi].md`
- `project://requirements` — ön ek ve numara kuralları
- `tekla://phases` — faz bilgisi
- `tekla://macros` — numaralandırma makrosu var mı?

## Ön Ek Standartları (requirements yoksa varsayılan)

| Eleman Tipi | Parça Ön Eki | Montaj Ön Eki | Başlangıç |
|-------------|-------------|----------------|-----------|
| Kolon | p | K | 1 |
| Kiriş | p | G | 1 |
| Döşeme | p | D | 1 |
| Temel | p | T | 1 |
| Perde | p | P | 1 |
| Diğer | p | E | 1 |

`project://requirements` varsa oradan al — bu tablo yalnızca geri dönüş değeridir.

## Süreç

### 1. Bağlantı ve Kural Kontrolü
```
tekla://connection_status → BAĞLI
project://requirements → ön ek kurallarını al
tekla://macros → "Numbering" veya "numaralandirma" adlı makro var mı?
```

### 2. Her Eleman Tipine Ön Ek Ata

Her tip için sırayla:

```
# Kolonlar
select_elements_by_filter(element_type="Column")
set_elements_properties(
  part_prefix="p",
  part_start_number=1,
  assembly_prefix="K",
  assembly_start_number=1
)

# Kirişler
select_elements_by_filter(element_type="Steel Beam")
set_elements_properties(
  part_prefix="p",
  part_start_number=1,
  assembly_prefix="G",
  assembly_start_number=1
)

# Döşemeler (ContourPlate / Slab)
select_elements_by_filter(element_type="Slab")
set_elements_properties(
  part_prefix="p",
  part_start_number=1,
  assembly_prefix="D",
  assembly_start_number=1
)
```

Her atama sonrası:
```
get_elements_properties() → prefix değerlerini kontrol et
```

### 3. Tekla Numaralandırmasını Çalıştır

> ⚠️ **API KISITLAMASI (doğrulandı 2026-04-10):**  
> `Tekla.Structures.Model.Operations.Operation` sınıfında `NumberAll()` / `NumberModified()` metodu **yoktur**.  
> C# makrosu veya MCP ile numaralandırma tetiklenemez — PART_POS, Tekla UI'dan çalıştırılmadan `P/0(?)` kalır.  
> `set_elements_properties(part_prefix, part_start_number)` ön eki ayarlar ama sayıyı atamaz.

**Her durumda — insana talimat ver:**
```
Numaralandırma için Tekla'da şu adımları uygula:
1. Üst menü: Çizimler ve Raporlar (Drawings & Reports)
2. → Numaralandırma (Numbering)
3. → Tümünü Numaralandır (Number All)
4. Tamamlandığında bana "numaralandırma yapıldı" yaz.
```

### 4. Numaralandırmayı Doğrula

Numaralandırma tamamlandıktan sonra:

**Her eleman tipi için:**
```
select_elements_by_filter(element_type="Column")
get_elements_properties(report_props_definitions=["NAME", "PART_POS", "ASSEMBLY_POS", "PHASE"])
```

Kontrol kriterleri:
- `PART_POS` boş veya "?" OLMAMALI
- `ASSEMBLY_POS` boş veya "?" OLMAMALI
- Aynı profil/malzeme grubundaki elemanlar farklı numaralar almalı
- Numaralar ön ek kurallarıyla uyumlu olmalı (K1, K2... G1, G2...)

**Numarasız eleman tespiti:**
```
select_elements_by_filter(
  custom_string_filters={
    "PART_POS": {"conditions": {"match_type": "Is Equal", "value": "?"}}
  }
)
→ Sonuç varsa: bu elemanlar numaralandırılamamış → insana bildir
```

### 5. Görsel Kontrol

```
# Tüm kolonları seç, pozisyon etiketi göster
select_elements_by_filter(element_type="Column")
draw_elements_labels(label="Position")
zoom_to_selection()

# Numarasız elemanları kırmızıya al (varsa)
select_elements_by_guid(guids=[numarasiz_guid_listesi])
color_selected(red=255, green=0, blue=0)
```

### 6. Çıktıyı Yaz ve Journal'a Logla

## Çıktılar
- `outputs/YYYY-MM-DD_tekla_modeller_numaralandirma_[proje-adi].md`

Çıktı formatı:
```markdown
# Numaralandırma Raporu: [Proje Adı]
Tarih: YYYY-MM-DD

## Özet
| Eleman Tipi | Adet | Numaralanan | Numarasız |
|-------------|------|-------------|-----------|
| Kolon | 12 | 12 | 0 |
| Kiriş | 24 | 24 | 0 |
| Döşeme | 4 | 4 | 0 |

## Örnek Pozisyonlar (İlk 5 Eleman)
| Ad | PART_POS | ASSEMBLY_POS | Profil |
|----|----------|--------------|--------|
| K1 | p1 | K1 | HEA200 |
| K2 | p2 | K2 | HEA200 |
| G1 | p13 | G1 | IPE300 |

## Numarasız Elemanlar
| Ad | GUID | Sorun |
|----|------|-------|
| (yok) | — | — |

## Kullanılan Yöntem
- [ ] Numaralandırma makrosu çalıştırıldı: [makro adı]
- [x] İnsan Tekla menüsünden numaralandırdı

## Sonraki Adım
Numaralandırma tamamlandı → METRAJ_CIKART çalışabilir.
```

## Kalite Barı
- Tüm elemanların PART_POS ve ASSEMBLY_POS değerleri dolu olmalı
- Numarasız eleman sıfır olmalı — aksi halde dur, insana bildir
- Ön ekler `project://requirements` kurallarıyla uyumlu olmalı
- Doğrulama `get_elements_properties` ile PART_POS sütunu kontrol edilerek yapılmış olmalı

## Araçlar (Tekla MCP)
- `tekla://connection_status` — bağlantı kontrolü
- `tekla://macros` — numaralandırma makrosu kontrolü
- `project://requirements` — ön ek kuralları
- `select_elements_by_filter` — tip bazlı seçim
- `select_elements_by_guid` — numarasız eleman seçimi
- `set_elements_properties` — ön ek ve başlangıç numarası atama
- `get_elements_properties` — PART_POS / ASSEMBLY_POS doğrulama
- `draw_elements_labels` — pozisyon etiketi görselleştirme
- `color_selected` — numarasız eleman işaretleme
- `zoom_to_selection` — odaklanma
- `run_macro` — Tekla numaralandırma makrosu (varsa)

## Entegrasyon
- ELEMAN_DOGRULA (veya MODEL_OLUSTUR) sonrası, METRAJ_CIKART öncesi çalışır
- Bu skill tamamlanmadan METRAJ_CIKART ve CIZIM_URET başlamamalı
- Tamamlanınca MEMORY.md güncellenir: hangi ön ek şeması kullanıldı, sorun yaşandı mı
