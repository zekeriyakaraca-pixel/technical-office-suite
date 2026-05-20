# Skill: METRAJ_CIKART

## Amaç
Tekla'daki 3D modelden MCP araçlarıyla eleman özelliklerini sorgulayarak metraj raporu oluşturmak; GUID takibi ve görsel renklendirme ile doğrulama yapmak.

## Hangi Hedefe Hizmet Eder
- Metraj doğruluğu — Tekla modelinden elde edilen gerçek miktarlar

## Önkoşullar
- `outputs/` içinde bu projeye ait onaylanmış modelleme özeti mevcut olmalı
- İnsan modeli Tekla'da görsel olarak onaylamış olmalı
- `tekla://connection_status` BAĞLI döndürmeli
- **`outputs/` içinde numaralandırma raporu mevcut olmalı (BLOCKER)**
  - Numaralandırma yoksa Excel'deki POZ NO sütunu boş kalır, sonradan düzenleme gerekir
  - Rapor yoksa: `dur — NUMARALANDIRMA önce tamamlanmalı` mesajı ver, bu skill'i başlatma

> ⚠️ **Excel pipeline çıktısıdır, isteğe bağlı değildir.** ERT partlist .md ve Excel aynı skill çalışmasında, insan tetiklemesi beklenmeksizin üretilir. İlk geçişte sadece .md vermek yasaktır.

## Girdiler
- `outputs/YYYY-MM-DD_tekla_modeller_model_[proje-adi].md` — modelleme özeti
- `outputs/YYYY-MM-DD_tekla_modeller_analiz_[proje-adi].md` — orijinal eleman listesi (karşılaştırma için)
- `tekla://phases` — hangi fazların dahil edileceği

## Süreç

### 1. Bağlantı Kontrolü
```
tekla://connection_status → BAĞLI olmalı
tekla://phases → Hangi fazlar sorgulanacak?
```

### 2. Eleman Tiplerine Göre Seçim ve Sorgulama

### HIZLI YOL: get_material_takeoff (BİRİNCİL)

```
1. select_elements_assemblies_or_main_parts(mode="Main Part")  ← tüm ana parçalar
2. get_material_takeoff(group_by="profile", include_weight=True, include_length=True, include_area=True)
   → summary: toplam tonaj, uzunluk, alan, sayı — tek satırda
   → groups: profil bazlı dağılım (her profil için count/weight/length/area)
3. get_weight_summary(breakdown="type")  → tip bazlı dağılım (kolon/kiriş/levha)
4. Faz bazlı: phase_filter=1 ile tekrar çağır
```

Bu 3 çağrı Metraj Raporunun **Özet + Profil Serisi + Tip Bazlı Özet** bölümlerini doldurur.

> Sonuçlar yetersizse (GUID doğrulaması veya anomali tespiti gerekiyorsa) aşağıdaki DETAYLI YOL'a geç.

---

### DETAYLI YOL: Profil Bazlı Elle Sorgulama (GERİ DÖNÜŞ)

Her eleman tipi için sırayla:

**a) Elemanları seç:**

> ⚠️ `element_type` filtresi çalışmıyor — profil adı üzerinden filtrele.

Taranacak profil başlangıçları (sırayla, hepsini dene):
```
IPE, HEA, HEB, HEM, INP, RHS, CHS, CFCHS, SHS, CFSHS, CFRHS, UNP, UPE, L, CC, PL
```

> ⚠️ **Cold-formed profiller ayrı prefix kullanır:**  
> `CHS` → normal yuvarlak içi boş profil  
> `CFCHS` → soğuk şekil yuvarlak içi boş (ör: CFCHS88.9X4.0)  
> `CFSHS` → soğuk şekil kare içi boş, `CFRHS` → soğuk şekil dikdörtgen içi boş  
> `Starts With "CHS"` filtresi CFCHS'yi **yakalamaz** — her birini ayrı sorgula.

Her biri için:
```
select_elements_by_filter(
  standard_string_filters={"profile": {"conditions": {"match_type": "Starts With", "value": "IPE"}}}
)
→ Eleman sayısı > 0 ise get_elements_properties çalıştır
→ Eleman sayısı = 0 ise sonraki profile geç
```
Modele özgü özel profil adları varsa (örn. CC170-2-25-50) requirements dosyasına ekle.

**b) Özellikleri al:**
```
get_elements_properties ile:
  report_props_definitions:
    - NAME
    - PROFILE
    - MATERIAL
    - LENGTH (mm)
    - WEIGHT (kg)
    - PART_POS (parça numarası)
    - ASSEMBLY_POS (montaj numarası)
    - PHASE
    - AREA_NET  ← B.ALAN için (mm² cinsinden döner, ÷1,000,000 → m²)
    - GUID  ← takip için
```

> **B.ALAN için `AREA_NET` kullan.**  
> Tekla'nın gerçek property adı `AREA_NET` — kesim uçları dahil net yüzey alanını mm² olarak döndürür.  
> `m²` için: `AREA_NET / 1,000,000` (contentattributes birim etiketi `m2` olsa da değer mm² gelir).  
>
> `PAINTING_AREA` veya `NET_SURFACE_AREA` sorguları da kabul edilir (semantic_overrides.json'da  
> `AREA_NET`'e eşlendi), ama direkt `AREA_NET` en güvenli yoldur.  
>
> Geometrik formül fallback (AREA_NET alınamazsa):
> | Profil | B.ALAN formülü |
> |--------|----------------|
> | IPE240 | `0.875 × L(m)` m² |
> | L50×5 | `0.190 × L(m)` m² |
> | CC170-2-25-50 | `0.680 × L(m)` m² |
> | PL kalınlık×genişlik | `2 × genişlik(mm) × L(mm) / 1,000,000` m² |

**c) GUID'leri kaydet:**
- Her elemanın GUID'ini metraj tablosuna ekle
- Sonraki döngülerde `select_elements_by_guid` ile doğrudan erişim sağlanır

**d) Kesimli parçaları kontrol et:**
```
get_elements_cut_parts → kesim varsa not düş, net ağırlığa yansıt
```

### 3. Anomali Tespiti (compare_elements ile)

Aynı ada/profile sahip elemanlar arasında tutarsızlık varsa:
```
1. İki şüpheli elemanı select_elements_by_guid ile seç
2. compare_elements(ignore_numbering=False) çalıştır
3. Fark varsa sorunlar listesine ekle
```

### 4. Görsel Durum İşaretleme

Metraj doğrulaması tamamlandıktan sonra:
```
# PDF analiziyle uyumlu elemanlar → yeşil
select_elements_by_filter(element_type="Column")
color_selected(red=0, green=200, blue=0)

# Anomali tespit edilen elemanlar → kırmızı
select_elements_by_guid(guids=[anomali_guid_listesi])
color_selected(red=255, green=0, blue=0)

zoom_to_selection()
draw_elements_labels(label="Name")
```

### 5. Metraj Tablosu Oluştur

Verileri aşağıdaki gruplamalarla düzenle:
- **Profil bazlı:** Her farklı profil için toplam uzunluk ve ağırlık
- **Malzeme bazlı:** Her farklı malzeme sınıfı için toplam ağırlık
- **Tip bazlı:** Kolon / kiriş / döşeme / vb. ayrımı
- **Faz bazlı:** Her faz için alt toplam

### 6. PDF Analizi ile Karşılaştır
- Analiz dosyasındaki eleman sayısı ile Tekla'daki eleman sayısını karşılaştır
- Fark varsa hangi elemanlar eksik/fazla — listele
- Fark >%5 ise insana bildir ve dur

### 7. Partlist Üret (İstenirse)

İnsan "partlist" veya "parça listesi" istediğinde **her zaman iki çıktı birlikte üretilir:**

1. **Açıklamalı Markdown** (`outputs/YYYY-MM-DD_tekla_modeller_ert_partlist_[model-adi].md`)
2. **Excel** (`outputs/[model-adi]_partlist.xlsx`) — **her zaman**, sorulmaksızın

Format tanımı: `knowledge/ERT_PARTLIST_FORMAT.md`

```
Sütunlar (bu sırayla):
POZ NO | CİNSİ | GENİŞLİK | UZUNLUK | ADET | KALİTE | B.ALAN | B.AĞIRLIK | T.ALAN | T.AĞIRLIK | AÇIKLAMA

Kurallar:
- GENİŞLİK sadece plakalarda dolu, profillerde boş
- T.ALAN = B.ALAN × ADET
- T.AĞIRLIK = B.AĞIRLIK × ADET
- AÇIKLAMA: "Deliksiz" başlangıç değeri — **NC_EXPORT + ACIKLAMA_GUNCELLE tamamlanmadan bu sütun kesinleşmez, partlist teslim edilmez**
- Sıralama: IPE → HEA/HEB → RHS/SHS → CHS → CFCHS → UNP/UPE → L → CC → PL
- Her grup içinde POZ NO sıralı
```

**Excel üretim kodu (openpyxl):**
```python
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Parça Listesi"

headers = ["POZ NO", "CİNSİ", "GENİŞLİK", "UZUNLUK", "ADET", "KALİTE",
           "B.ALAN", "B.AĞIRLIK", "T.ALAN", "T.AĞIRLIK", "AÇIKLAMA"]

# Başlık satırı — koyu zemin, beyaz yazı
header_fill = PatternFill("solid", fgColor="1F4E79")
header_font = Font(bold=True, color="FFFFFF", size=10)
for col, h in enumerate(headers, 1):
    cell = ws.cell(row=1, column=col, value=h)
    cell.fill = header_fill
    cell.font = header_font
    cell.alignment = Alignment(horizontal="center", vertical="center")

# Profil grubu satırları — gri zemin, kalın yazı
group_fill = PatternFill("solid", fgColor="D9E1F2")
group_font = Font(bold=True, size=10)

# Veri satırları: satır listesini buraya ekle (her dict: headers ile aynı sıra)
row_num = 2
total_adet = 0
total_t_alan = 0.0
total_t_agirlik = 0.0
keys = ["POZ NO","CİNSİ","GENİŞLİK","UZUNLUK","ADET","KALİTE",
        "B.ALAN","B.AĞIRLIK","T.ALAN","T.AĞIRLIK","AÇIKLAMA"]
for item in partlist_rows:  # partlist_rows: dicts listesi
    is_group_header = item.get("is_group")
    if not is_group_header:
        total_adet      += item.get("ADET", 0)
        total_t_alan    += item.get("T.ALAN", 0.0)
        total_t_agirlik += item.get("T.AĞIRLIK", 0.0)
    for col, key in enumerate(keys, 1):
        cell = ws.cell(row=row_num, column=col, value=item.get(key, ""))
        if is_group_header:
            cell.fill = group_fill
            cell.font = group_font
        else:
            cell.font = Font(size=10)
            cell.alignment = Alignment(horizontal="center" if col not in (2, 11) else "left")
        cell.border = border
    row_num += 1

# Toplam satırı — sarı zemin
# Sütun referansı: 5=ADET, 9=T.ALAN, 10=T.AĞIRLIK  (diğerleri boş bırakılır)
total_fill = PatternFill("solid", fgColor="FFEB9C")
for col in range(1, 12):
    c = ws.cell(row=row_num, column=col)
    c.fill = total_fill
    c.font = Font(bold=True, size=10)
    c.border = border
ws.cell(row=row_num, column=1,  value="TOPLAM").alignment = Alignment(horizontal="center")
ws.cell(row=row_num, column=5,  value=total_adet).alignment = Alignment(horizontal="center")   # ADET toplam (sayı)
ws.cell(row=row_num, column=9,  value=round(total_t_alan, 3)).alignment = Alignment(horizontal="center")   # T.ALAN toplam
ws.cell(row=row_num, column=10, value=round(total_t_agirlik, 3)).alignment = Alignment(horizontal="center")  # T.AĞIRLIK toplam

# Sütun genişlikleri
col_widths = [10, 18, 10, 10, 7, 10, 9, 12, 9, 12, 12]
for i, w in enumerate(col_widths, 1):
    ws.column_dimensions[get_column_letter(i)].width = w

# Kenarlık
thin = Side(style="thin")
border = Border(left=thin, right=thin, top=thin, bottom=thin)
for row in ws.iter_rows(min_row=1, max_row=row_num, min_col=1, max_col=11):
    for cell in row:
        cell.border = border

wb.save(f"outputs/{model_adi}_partlist.xlsx")
```

### 8. Çıktıyı Yaz ve Journal'a Logla

## Çıktılar
- `outputs/YYYY-MM-DD_tekla_modeller_metraj_[proje-adi].md`
- `outputs/YYYY-MM-DD_tekla_modeller_ert_partlist_[model-adi].md` (partlist istenirse)
- `outputs/[model-adi]_partlist.xlsx` (partlist istenirse — **md ile birlikte her zaman üretilir**)

Çıktı formatı:
```markdown
# Metraj Raporu: [Proje Adı]
Tarih: YYYY-MM-DD
Model: [Tekla model adı]

## Özet (Çelik Tonajı)
| Kategori | Değer |
|----------|-------|
| Toplam çelik ağırlığı | XX kg / XX ton |
| Kolon ağırlığı | XX kg |
| Kiriş ağırlığı | XX kg |
| Çapraz / Brace ağırlığı | XX kg |
| Levha / Bağlantı ağırlığı | XX kg |
| Toplam eleman sayısı | XX |

## Profil Serisi Bazlı Özet
| Seri | Adet Profil | Toplam Uzunluk (m) | Toplam Ağırlık (kg) | Oran (%) |
|------|-------------|-------------------|---------------------|----------|
| HEA  | X | XX | XX | %XX |
| IPE  | X | XX | XX | %XX |
| RHS  | X | XX | XX | %XX |

## Profil Bazlı Detay Metrajı
| Profil | Malzeme | Adet | Toplam Uzunluk (m) | kg/m | Toplam Ağırlık (kg) |
|--------|---------|------|-------------------|------|---------------------|
| HEA200 | S355 | 12 | 42.0 | 42.3 | 1.777 |
| IPE300 | S355 | 24 | 144.0 | 42.2 | 6.077 |

## Tip Bazlı Özet
| Eleman Tipi | Adet | Ağırlık (kg) | GUID Örneği |
|-------------|------|--------------|-------------|
| Kolon | 12 | XX | abc-123... |
| Kiriş | 24 | XX | def-456... |

## Faz Bazlı Özet
| Faz | Eleman Sayısı | Ağırlık (kg) |
|-----|---------------|--------------|
| 1   | XX | XX |

## PDF Uyumu
- Beklenen eleman: XX
- Modeldeki eleman: XX
- Fark: XX (%XX)

## Anomaliler
| Eleman | GUID | Sorun |
|--------|------|-------|
| G5 | abc-123 | Profil uyumsuzluğu: IPE250 vs IPE300 |

## Dikkat Edilmesi Gerekenler
- [Kesimli parçalar, özel durumlar, vb.]
```

## Kalite Barı
- Her eleman tipi için ayrı sorgulama yapılmış olmalı
- GUID'ler kaydedilmiş ve raporlanmış olmalı
- `compare_elements` ile tespit edilen her anomali sorunlar tablosunda yer almalı
- Görsel renklendirme yapılmış olmalı (yeşil/kırmızı)
- PDF analizi ile eleman sayısı farkı <%5 olmalı — fazlaysa insana bildir

## Araçlar (Tekla MCP)
- `tekla://connection_status` — bağlantı kontrolü
- `tekla://phases` — faz listesi
- `get_material_takeoff` — profil/faz bazlı toplu metraj (BİRİNCİL — 17 döngüyü tek çağrıya indirger)
- `get_weight_summary` — toplam ağırlık + tip bazlı dağılım
- `get_part_list` — PART_POS sıralı liste, ERT partlist için ham veri kaynağı
- `select_elements_assemblies_or_main_parts` — tüm ana parçalar / assembly seçimi
- `select_elements_by_filter` — profil bazlı seçim (DETAYLI YOL)
- `select_elements_by_guid` — GUID bazlı seçim
- `get_elements_properties` — özellik ve miktar sorgulama (GUID dahil)
- `get_elements_cut_parts` — kesim kontrolü
- `compare_elements` — anomali tespiti
- `color_selected` — durum renklendirmesi
- `draw_elements_labels` — görsel etiket
- `zoom_to_selection` — odaklanma

## Entegrasyon
- MODEL_OLUSTUR + insan onayı sonrası çalışır
- Çıktı insan tarafından kontrol edilir
- Onay sonrası NC_EXPORT devreye girer → ACIKLAMA_GUNCELLE → ardından CIZIM_URET
- ELEMAN_DOGRULA çalıştıysa sorunlar listesi oradan devralınabilir
