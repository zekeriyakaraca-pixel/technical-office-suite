# Skill: NC_EXPORT

## Amaç
Tekla modelindeki plakalar için DXF-NC, profiller için DSTV-NC dosyaları üretmek; CNC makinelere hazır çıktı klasörü oluşturmak.

## Hangi Hedefe Hizmet Eder
- Doğru modelleme — 3D modelden üretilebilir NC çıktısı

## Önkoşullar
- `outputs/` içinde onaylanmış numaralandırma raporu mevcut olmalı
- İnsan `model onaylandı` yazmış olmalı
- `tekla://connection_status` BAĞLI döndürmeli
- Tekla modeli numaralandırılmış olmalı (PART_POS atamalı)

## Girdiler
- `outputs/YYYY-MM-DD_tekla_modeller_numaralandirma_[proje-adi].md` — pozisyon listesi
- `new/config/requirements/[proje].json` — NC çıktı klasörü, isimlendirme kuralı
- `tekla://macros` — kullanılabilir makrolar
- İnsan cevabı: çıktı klasörü, hangi plakalar, hangi profil tipleri dahil

## Sabit Parametreler (İnsana Sorma)

Aşağıdaki parametreler tüm projeler için sabittir — insana **sormaya gerek yok**, doğrudan uygula:

| Parametre | Sabit Değer |
|-----------|-------------|
| **NC çıktı klasörü** | `C:\Users\pc\Desktop\Tekla-Agent\outputs\nc\` |
| **Plaka kapsamı** | Tüm PL prefixli parçalar (DXF-NC) |
| **Profil kapsamı** | Tüm profil tipleri (DSTV-NC) |
| **Dosya isimlendirme** | Tekla'nın ürettiği varsayılan (PART_POS bazlı) |
| **DSTV versiyonu** | NC1 (varsayılan) |

> Klasör mevcut değilse Python ile oluştur: `os.makedirs(r"C:\Users\pc\Desktop\Tekla-Agent\outputs\nc", exist_ok=True)`

## Süreç

### 1. Bağlantı ve Makro Kontrolü
```
tekla://connection_status → BAĞLI
list_nc_settings()  → Mevcut NC ayar profillerini listele → geçerli nc_settings_name seç
tekla://macros → "DSTVtoDXFConverter.cs" mevcut mu? (fallback için)
```

> **Güncel Akış (2026-04-18):** `export_nc_files` API başarısızlığını otomatik tespit eder.
> Agent önce bu MCP aracını çalıştırır. `api_failed` → `manual_steps` insana iletilir.
> `DSTVtoDXFConverter.cs` makrosu GUI açtığından doğrudan çalıştırılamaz.

### 2. Plaka DXF-NC Ayarları (Sabit Kurallar)

DXF export öncesinde Tekla'nın DXF NC ayarları şu şekilde olmalıdır — bu değerler her projede aynıdır, insana sorma:

| Ayar | Değer |
|------|-------|
| Draw crosshair | **None** |
| Text options → tümü | **Kapalı (off)** |
| Layers → Bend | **Kapalı (off)** |

Bu ayarlar `DSTVtoDXFConverter.cs` makrosu çalıştırılmadan önce requirements dosyasına ve journal'a not düşülür. Makro bu ayarları desteklemiyorsa insana manuel olarak Tekla'da ayarlaması gerektiğini bildir.

### 3. Plaka NC1 Üretimi

**a) Plakaları seç:**
```
select_elements_by_filter:
  standard_string_filters: {"profile": {"conditions": {"match_type": "Starts With", "value": "PL"}}}
get_elements_properties(["name", "profile", "part position"])  → plaka listesi özeti
```

**b) NC1 Üretim Sırası (3 yöntem — sırayla dene):**

> ⚠️ **2026-04-20 Doğrulandı:** Tekla NC export API (Python.NET MCP + C# makro, 3 yöntem)
> Tekla 2025'te evrensel olarak başarısız — 0 dosya üretiyor. Yöntem 1 ve 2'yi dene,
> başarısız olursa **Yöntem 3 (Python NC1 üretimi) BİRİNCİL fallback'tir**.

**Yöntem 1 — MCP:**
```
NC_FOLDER = "C:\\Users\\pc\\Desktop\\Tekla-Agent\\outputs\\nc"
export_nc_files(output_folder=NC_FOLDER, nc_settings_name="standard")
  → status="success" + nc1_count > 0 → Adım 4'e geç
  → status="api_failed" veya nc1_count=0 → Yöntem 2'ye geç
```

**Yöntem 2 — C# Makro:**
```
run_macro("522607_nc_export_v5.cs")   # proje-özel makro varsa
  → DisplayPrompt "BASARILI" + dosya var → Adım 4'e geç
  → "BASARISIZ" veya 0 dosya → Yöntem 3'e geç
```

**Yöntem 3 — Python NC1 Üretimi (BİRİNCİL FALLBACK — doğrulandı):**
```bash
python scripts/generate_nc1_from_model.py <proje-kodu>
```
- `outputs/model_[proje].json` + `outputs/[proje]_partlist.xlsx` okur
- Her PL plakası için DSTV NC1 üretir (AK dikdörtgen + IK oktagon delikler)
- Çıktı: `outputs/nc/<PART_POS>.nc1`
- `OK N dosya, 0 hata` → Adım 4'e geç
- Hata → model JSON'da `plates[].holes` veya boyut verisi eksik demek; insana bildir

> Yöntem 3 model JSON + partlist'ten tamamen bağımsız çalışır — Tekla bağlantısı gerekmez.

**c) Doğrula:**
```
get_nc_export_status(output_folder=NC_FOLDER)
  → unmatched_nc1: DXF'siz kalan NC1 dosyaları listeler
  → Beklenen .nc1 sayısı = seçili plaka pozisyon sayısı
```

---

### 4. NC1 → DXF Otomatik Dönüşümü (Agent Yapar — İnsan Onayı Gerekmez)

İnsan "nc export yapıldı" dedikten veya `export_nc_files` başarılı olduktan hemen sonra çalıştır.

**BİRİNCİL — MCP aracı:**
```
convert_nc1_to_dxf(input_folder=nc_folder, output_folder=nc_folder, overwrite=True)

DURUM KONTROLÜ:
  status="success": converted_count logla
  status="partial":  conversions listesinde "error" olanları incele
  status="error":    ezdxf yüklü değil veya klasör boş → GERİ DÖNÜŞ yoluna geç

Doğrulama (conversions[i] üzerinden):
  - hole_count: NC1'deki IK bloğu sayısıyla eşleşmeli
  - DELIK katmanında CIRCLE (LWPOLYLINE değil) — MEMORY.md kuralı geçerli
```

**Renklendirme:**
```
DXF üretilen plakalar → mavi (color_selected R=0, G=120, B=255)
Hatalı plakalar       → kırmızı (color_selected R=255, G=0, B=0)
```

**GERİ DÖNÜŞ (convert_nc1_to_dxf başarısızsa — ezdxf kurulu değil veya MCP erişilemez):**
`NC_EXPORT.md → NC1 → DXF Dönüşümü` bölümündeki kanıtlanmış Python desenini kullan.
Zorunlu kurallar: `AK` → `KONTUR` LWPOLYLINE, `IK` → `DELIK` **CIRCLE** — poligon değil.

### 5. Profil DSTV-NC Üretimi

**a) Profilleri tip bazlı seç:**
```
Her profil tipi için ayrı döngü:
  select_elements_by_filter(
    standard_string_filters={"profile": {"conditions": {"match_type": "Starts With", "value": "HEA"}}}
  )
  → Ardından IPE, RHS, CHS, UNP vb.
```

**b) Seçimi doğrula:**
```
get_elements_properties ile:
  report_props_definitions: ["profile", "length", "part position", "material"]
```

**c) DSTV-NC makrosunu çalıştır:**
```
run_macro ile:
  macro_name: "DSTVtoDXFConverter.cs"
  → Seçili profiller için DSTV-NC üretir
```

### 6. Görsel Durum İşaretleme
```
# NC dosyası üretilen elemanlar → mavi
color_selected(red=0, green=120, blue=255)

# NC dosyası üretilemeyen elemanlar → kırmızı
select_elements_by_guid(guids=[hata_guid_listesi])
color_selected(red=255, green=0, blue=0)

zoom_to_selection()
draw_elements_labels(label="Part Position")
```

### 7. Çıktıyı Yaz ve Journal'a Logla

## Çıktılar
- `outputs/YYYY-MM-DD_tekla_modeller_nc_[proje-adi].md`

Çıktı formatı:
```markdown
# NC Export Raporu: [Proje Adı]
Tarih: YYYY-MM-DD
Model: [Tekla model adı]
NC Klasörü: [çıktı yolu]

## Özet
| Kategori | Beklenen | Üretilen | Hatalı |
|----------|----------|----------|--------|
| Plaka DXF-NC | XX | XX | XX |
| Profil DSTV-NC | XX | XX | XX |
| **Toplam** | **XX** | **XX** | **XX** |

## Plaka DXF-NC Listesi
| Pozisyon | Profil | Dosya Adı | Durum |
|----------|--------|-----------|-------|
| PL1 | PL10x200x300 | PL1.dxf | ✓ |
| PL2 | PL12x150x400 | PL2.dxf | ✓ |

## Profil DSTV-NC Listesi
| Pozisyon | Profil | Uzunluk (mm) | Dosya Adı | Durum |
|----------|--------|-------------|-----------|-------|
| K1 | HEA200 | 3500 | K1.nc | ✓ |
| K2 | IPE240 | 4620 | K2.nc | ✓ |

## Hatalı Elemanlar
| Pozisyon | GUID | Hata |
|----------|------|------|
| [varsa] | [guid] | [hata mesajı] |

## Sonraki Adım
İnsan NC dosyalarını CNC makineye aktarır ve kontrol eder.
```

## NC1 → DXF Dönüşümü (Python ezdxf)

**DSTVtoDXFConverter.cs otonom çalışmadığı için** NC1 dosyaları Python `ezdxf` ile DXF'e dönüştürülür.

### Kritik Kural: Delikler CIRCLE, Kontur LWPOLYLINE

| NC1 Bloğu | Tip | Açıklama | DXF Entity |
|-----------|-----|----------|------------|
| `AK` | Dış kontur | Çokgen köşe noktaları | **LWPOLYLINE (closed=True)** |
| `IK` | İç kesim (delik) | Daire yaklaşımı poligon | **CIRCLE** ← zorunlu |

> ⚠️ **IK bloklarını LWPOLYLINE olarak çizme.** NC1, delikleri dairesel poligon (genellikle 8 nokta) olarak saklar. LWPOLYLINE geometriyi taklit eder; CIRCLE gerçek geometridir ve CNC makinelerde doğru işlenir.

### Katman Yapısı (Sabit)

| Katman | Renk | İçerik |
|--------|------|--------|
| `KONTUR` | 7 (beyaz) | AK bloğu → LWPOLYLINE |
| `DELIK` | 1 (kırmızı) | IK blokları → CIRCLE |
| `BILGI` | 3 (yeşil) | Parça adı, malzeme, tarih |

### Python Kod Deseni (Kanıtlanmış — 2026-04-10)

```python
import ezdxf, math, re

def parse_xy(line, first_line=False):
    """NC1 satırından (x, y) çıkar.
    İlk satır formatı: v  0.00u  74.42 ...  (x değeri 'u' harfiyle bitişik)
    Sonraki satırlar:   29.32  0.00 ...
    """
    parts = line.split()
    if not parts:
        return None
    if first_line:
        x = float(re.sub(r'[a-zA-Z]', '', parts[1]))  # '0.00u' → 0.00
        y = float(parts[2])
    else:
        x = float(parts[0])
        y = float(parts[1])
    return (x, y)

def circle_center(pts):
    """IK poligon noktalarından merkez ve yarıçap hesapla."""
    unique = pts[:-1] if (len(pts) > 1 and pts[-1] == pts[0]) else pts
    cx = sum(p[0] for p in unique) / len(unique)
    cy = sum(p[1] for p in unique) / len(unique)
    r  = sum(math.dist((cx, cy), p) for p in unique) / len(unique)
    return cx, cy, r

# NC1 parse
with open(nc1_path, "r") as f:
    lines = f.readlines()

ak_pts, ik_list = [], []
i = 0
while i < len(lines):
    ln = lines[i].strip()
    if ln == "AK":
        i += 1
        ak_pts = []
        pt = parse_xy(lines[i], first_line=True)
        if pt: ak_pts.append(pt)
        i += 1
        while i < len(lines) and lines[i].strip() not in ("IK", "EN", "AK"):
            pt = parse_xy(lines[i])
            if pt: ak_pts.append(pt)
            i += 1
    elif ln == "IK":
        i += 1
        pts = []
        pt = parse_xy(lines[i], first_line=True)
        if pt: pts.append(pt)
        i += 1
        while i < len(lines) and lines[i].strip() not in ("IK", "EN", "AK"):
            pt = parse_xy(lines[i])
            if pt: pts.append(pt)
            i += 1
        ik_list.append(pts)
    else:
        i += 1

# DXF oluştur
doc = ezdxf.new("R2010")
msp = doc.modelspace()
doc.layers.add("KONTUR", color=7)
doc.layers.add("DELIK", color=1)
doc.layers.add("BILGI", color=3)

# AK → LWPOLYLINE
msp.add_lwpolyline(ak_pts, dxfattribs={"layer": "KONTUR", "closed": True})

# IK → CIRCLE (LWPOLYLINE değil!)
for pts in ik_list:
    cx, cy, r = circle_center(pts)
    msp.add_circle((cx, cy), r, dxfattribs={"layer": "DELIK"})

# Bilgi metni
msp.add_text(f"{poz}  {profil}  {malzeme}",
             dxfattribs={"layer": "BILGI", "height": 8}).set_placement((5, -20))
msp.add_text(f"{boyut}  {n_delik}xD{cap}  {tarih}",
             dxfattribs={"layer": "BILGI", "height": 6}).set_placement((5, -32))

doc.saveas(dxf_path)
```

### Doğrulama

Üretilen DXF kontrol kriterleri:
- `KONTUR` katmanında 1 LWPOLYLINE — köşe sayısı NC1 AK satır sayısıyla eşleşmeli
- `DELIK` katmanında N adet CIRCLE (LWPOLYLINE olmamalı)
- Her daire yarıçapı nominal delik çapının yarısına eşit olmalı (ör. Ø18 → r≈9.0 mm)

---

## Kalite Barı
- Üretilen NC sayısı numaralandırma raporundaki pozisyon sayısıyla eşleşmeli
- Hatalı eleman sayısı > %5 ise insana bildir ve dur
- Tüm plakalar için DXF-NC, tüm profiller için DSTV-NC üretilmiş olmalı
- Görsel renklendirme yapılmış olmalı

## Araçlar (Tekla MCP)
- `tekla://connection_status` — bağlantı kontrolü
- `tekla://macros` — makro listesi (fallback için)
- `list_nc_settings` — geçerli NC ayar profilleri (export öncesi)
- `export_nc_files` — NC1 export (BİRİNCİL; api_failed → manual_steps insana)
- `get_nc_export_status` — export sonrası doğrulama, DXF'siz NC1'leri listeler
- `convert_nc1_to_dxf` — NC1→DXF, CIRCLE delikler, 3 katman otomatik (BİRİNCİL)
- `select_elements_by_filter` — plaka/profil seçimi (profil adı bazlı)
- `select_elements_by_guid` — hatalı eleman seçimi
- `get_elements_properties` — pozisyon ve profil doğrulama
- `color_selected` — durum renklendirmesi
- `draw_elements_labels` — pozisyon etiketleri
- `zoom_to_selection` — odaklanma
- `run_macro("DSTVtoDXFConverter.cs")` — DXF-NC üretimi (fallback; GUI açar)

## Notlar
- Plaka seçiminde `name` filtresi `PLATE` içerenleri, `profile` filtresi `PL` ile başlayanları yakalar — ikisini de dene
- Profil DSTV-NC için Tekla'nın kendi Export menüsü de kullanılabilir; makro çalışmazsa insana yönlendir
- NC klasörü sabit: `C:\Users\pc\Desktop\Tekla-Agent\outputs\nc\` — insana sorma, yoksa `os.makedirs` ile oluştur

## Entegrasyon
- NUMARALANDIRMA + insan onayı (`model onaylandı`) sonrası çalışır
- METRAJ_CIKART'tan SONRA çalışır — partlist.xlsx AÇIKLAMA sütununun kesinleşmesi için NC_EXPORT → ACIKLAMA_GUNCELLE zinciri tamamlanmalıdır
- CIZIM_URET'ten önce veya sonra çalışabilir
- **Bu skill tamamlandıktan sonra otomatik olarak `ACIKLAMA_GUNCELLE` çalıştır** — partlist AÇIKLAMA sütununu günceller

---

## Profil (HEB/IPE/UPN) NC1 — HENÜZ UYGULANMADI

`scripts/generate_nc1_from_model.py` yalnızca `plates[]` dizisini işliyor. Profil (HEB/IPE) desteği için gereken:

1. `main_beam` alanını da işle
2. DSTV profil tipi belirle: I/H kesitler → tip `"I"`, kutu → `"RH"`, L → `"L"` vb.
3. ST header'da genişlik/yükseklik flanş+web boyutlarından türet (kesit tablosu gerekebilir)
4. Web delikleri için IK koordinatlarını flanş yüzeyine dik eksen baz alarak hesapla
5. AK kontur: flanş+web profil kesiti poligonu (dikdörtgen değil, I-profil şekli)

**Yol haritası:** Retrospektif 000-000-522-607 Madde 9.  
**Öncelik:** Bir sonraki web delikli profil (cıvata grubu, stiffener gibi) içeren projede uygulanacak.
# 2026-04-30 MCP API kisiti notu

- `export_nc_files` sonucu `manual_required` ise bunu hata gibi tekrar deneme dongusune sokma. Kullaniciya `manual_steps` ile Tekla UI export adimlarini ver.
- HEB/IPE/UPN gibi profil NC1 icin v1 guvenli karar: once Tekla API, API false/sessiz basarisizsa Tekla UI. Dogrudan profil DSTV generator henuz kullanilmaz; ayri kesit tablosu ve canli dogrulama gerekir.
- Plaka NC1 fallback `scripts/generate_nc1_from_model.py` ile sinirlidir; bu script `plates[]` disindaki G1/HEB ana profili uretmez.
