# Skill: CIZIM_URET

## Amaç
Tekla'daki onaylanmış 3D modelden makro çalıştırarak 2D çizimler üretmek ve çizim üretim özetini kaydetmek.

## Hangi Hedefe Hizmet Eder
- Doğru modelleme — 3D modelden doğru 2D çizim çıktısı

## Önkoşullar
- `outputs/` içinde bu projeye ait onaylanmış metraj raporu mevcut olmalı
- İnsan metraj raporunu onaylamış olmalı
- `tekla://connection_status` BAĞLI döndürmeli
- `tekla://macros` içinde çizim makrosu mevcut olmalı

## Girdiler
- `outputs/YYYY-MM-DD_tekla_modeller_metraj_[proje-adi].md` — onaylanmış metraj raporu
- `tekla://macros` — kullanılabilir Tekla makroları
- `tekla://filters/view` — görünüm filtreleri
- `new/config/requirements/` — çizim standartları (varsa)

## Süreç

### 1. Bağlantı ve Makro Kontrolü
```
tekla://connection_status → BAĞLI
tekla://macros → Çizim makrosu listesini al
    → Uygun makro yoksa insana bildir, dur
```

### 2. Çizim Üretim Hazırlığı

**Görünüm hazırlığı:**
- `apply_view_filter` ile uygun görünüm filtresini uygula (varsa)
- `redraw_view` ile görünümü tazele

**Eleman hazırlığı:**
- `select_elements_by_filter` ile çizimi alınacak elemanları seç
- `show_only_selected` — sadece ilgili elemanları göster (diğerlerini gizle)
- `zoom_to_selection` ile modeli tam görünümde aç
- `draw_elements_labels(label="Position")` ile pozisyon etiketlerini çiz

**Faz bazlı çizim (gerekirse):**
- Her faz için ayrı ayrı:
  ```
  select_elements_by_filter(standard_string_filters={"phase": {"conditions": {"match_type": "Is Equal", "value": "1"}}})
  show_only_selected()
  zoom_to_selection()
  → Makroyu bu görünüm için çalıştır
  ```

### 3. Çizim Üret

#### 3a. API ile Fabrication Çizimi (Birincil Yol)

```
// 1. Parçaları seç → Assembly'ye yükselt
select_elements_by_filter(...)
select_elements_assemblies_or_main_parts(mode="Assembly")
get_elements_properties(["GUID", "ASSEMBLY_POS"])  // assembly GUID'lerini al

// 2. Fabrication çizimleri üret
smart_create_fabrication_drawing(assembly_guids=[...])
→ {"status": "success", "created_count": N}
```

#### 3b. Dışa Aktarma (İnsan Onayıyla)

```
// Drawing List'te çizimler seçili iken:
export_to_pdf()   // Seçili çizimleri PDF olarak dışa aktar
export_to_dwg()   // Seçili çizimleri DWG olarak dışa aktar (isteğe bağlı)
```

#### 3c. Makro ile Çizim (Yedek Yol)

`smart_create_fabrication_drawing` yetersiz kalırsa veya özel yerleşim planı gerekiyorsa insan onayıyla:
```
run_macro ile:
  macro_name: [Tekla çizim makrosu adı]
```

Makro türleri (öncelik sırasıyla):
1. Genel yerleşim planı makrosu
2. Kat planı makrosu (her kat için)
3. Kesit makrosu
4. Detay makrosu (varsa)

### 4. Sonucu Doğrula
- Makro çalıştıktan sonra Tekla'nın Drawing List'ini kontrol et (mümkünse)
- Oluşturulan çizim sayısını kaydet
- Hata oluştuysa log'a yaz

### 5. Çıktıyı Yaz ve Journal'a Logla

## Çıktılar
- `outputs/YYYY-MM-DD_tekla_modeller_cizim_[proje-adi].md`

Çıktı formatı:
```markdown
# Çizim Üretim Özeti: [Proje Adı]
Tarih: YYYY-MM-DD

## Çalıştırılan Makrolar
| Makro | Durum | Üretilen Çizim Sayısı |
|-------|-------|-----------------------|
| [makro adı] | Başarılı | XX |

## Üretilen Çizimler
| Çizim Tipi | Sayı | Konum |
|------------|------|-------|
| Yerleşim Planı | X | Tekla Drawing List |
| Kat Planı | X | Tekla Drawing List |
| Kesit | X | Tekla Drawing List |

## Sorunlar
- [Varsa: makro hatası, eksik çizim vb.]

## Sonraki Adım
İnsan Tekla'da çizimleri kontrol eder ve teslim eder.
Bu proje döngüsü tamamlandı.
```

## Kalite Barı
- Her makro çalıştırma öncesinde insan onayı alınmış olmalı
- Makro hatası varsa tekrar denemeden önce insana bildir
- Çizim sayısı modeldeki eleman sayısıyla mantıklı oran içinde olmalı

## Araçlar (Tekla MCP)
- `tekla://connection_status` — bağlantı kontrolü
- `tekla://macros` — kullanılabilir makrolar
- `tekla://filters/view` — görünüm filtreleri
- `select_elements_by_filter` — eleman seçimi
- `select_elements_by_guid` — GUID bazlı seçim (metraj raporundaki GUID'ler)
- `apply_view_filter` — görünüm filtresi uygulama
- `show_only_selected` — sadece seçili elemanları gösterme
- `hide_selected` — seçili elemanları gizleme
- `redraw_view` — görünüm yenileme
- `draw_elements_labels` — etiket çizimi
- `zoom_to_selection` — odaklanma
- `select_elements_assemblies_or_main_parts` — Parça → Assembly dönüşümü
- `smart_create_fabrication_drawing` — Assembly çizimi üretme (makro alternatifi)
- `export_to_pdf` — Seçili çizimleri PDF olarak aktar
- `export_to_dwg` — Seçili çizimleri DWG olarak aktar
- `run_macro` — çizim makrosu çalıştırma (yedek yol)

## Entegrasyon
- METRAJ_CIKART + insan onayı sonrası çalışır
- Bu skill projenin son adımıdır
- Tamamlanınca MEMORY.md güncellenir: ne işe yaradı, hangi makro sorunsuz çalıştı
