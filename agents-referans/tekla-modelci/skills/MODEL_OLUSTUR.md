# Skill: MODEL_OLUSTUR

## Amaç
CONFIDENCE_GATE çıktısındaki model.json'u okuyarak `create_beam` ve `create_contour_plate` MCP araçlarıyla Tekla'da 3D yapısal elemanlar oluşturmak; ardından delik/bolt ve assembly/kaynak tanımlarını eklemek ve doğrulamak.

## Hangi Hedefe Hizmet Eder
- Doğru modelleme — PDF'teki elemanların Tekla'da karşılığını oluşturur

## Önkoşullar
- `outputs/` içinde bu projeye ait **onaylanmış model.json** mevcut olmalı (CONFIDENCE_GATE çıktısı)
- Global Confidence ≥ %75 doğrulanmış olmalı (model.json içindeki `global_confidence` alanı)
- Model contract validator PASS olmali: `python scripts/validate_model_contract.py outputs/model_[proje].json`
- `tekla://connection_status` BAĞLI döndürmeli
- Tüm SORU'lar yanıtlanmış ve analiz dosyası güncellenmiş olmalı

## Girdiler
- `outputs/YYYY-MM-DD_tekla_modeller_model_[proje-adi].json` — **CONFIDENCE_GATE onaylı model.json** (zorunlu)
- `outputs/YYYY-MM-DD_tekla_modeller_analiz_v2_[proje-adi].md` — güncellenmiş analiz (referans)
- `new/config/requirements/` veya `project://requirements` — isimlendirme kuralları
- `tekla://phases` — modeldeki mevcut fazlar

## Kritik Kısıt: Görev Bölümü

| Görev | Araç | Neden |
|-------|------|-------|
| Yapısal eleman oluştur (Beam) | MCP `create_beam` | Direkt API — makro gereksiz |
| Levha oluştur (ContourPlate) | MCP `create_contour_plate` | Direkt API — makro gereksiz |
| Faz atama | MCP `set_elements_properties` | `create_beam`'de phase parametresi yok |
| Delik / bolt açma | MCP `create_bolt_group` → fallback C# `BoltArray` | 2026-04-18 yaması; Insert() false dönerse BoltArray makrosuna geç |
| Rotation/depth düzeltme | MCP `set_beam_position` | RotFix makrosu yerine; makro yalnızca MCP başarısız olursa |
| Assembly birleştirme | MCP `create_weld` | Assembly.Add() güvenilmez |
| Kaynak tanımı | MCP `create_weld` | — |
| Özellik düzeltme | MCP `set_elements_properties` | — |

---

## Süreç

### Aşama 1: model.json Doğrula ve Bağlantı Kontrol Et

```
outputs/ içinde model.json dosyasını bul
    → Yok → DUR: CONFIDENCE_GATE skill'ini çalıştır
    → global_confidence < 0.75 → DUR: CONFIDENCE_GATE tamamlanmamış, insana bildir
    → contract validator FAIL → DUR: model-uretici'ye CONTRACT_VALIDATION_FAILED bildir

tekla://connection_status → BAĞLI olmalı
tekla://model_info → model adı ve mevcut eleman sayısını al (referans baseline)
tekla://phases → model.json'daki fazlar modelde var mı?
    → Eksik faz varsa insana bildir, dur
project://requirements → İsimlendirme kurallarını al
```

### Aşama 2: Alt Dosyaları Oku

```
skills/model/DELIK_BOLT.md        — delik/bolt varsa (zorunlu)
skills/model/ASSEMBLY_KAYNAK.md   — assembly tanımı gerekiyorsa (zorunlu)
```

### Aşama 3: Elemanları MCP ile Oluştur

**model.json'daki sırayla ilerle: önce columns, sonra beams, sonra plates.**

Her eleman için:

#### 3a. Kiriş ve Kolon → `create_beam`
```
create_beam(
  start_point = eleman.start_point,    // {x, y, z} — mm
  end_point   = eleman.end_point,      // {x, y, z} — mm
  profile     = eleman.profile,        // "HEA200", "IPE300" vb.
  material    = eleman.material,       // "S235JR", "S355J2" vb.
  tekla_class = "3",
  name        = eleman.id,             // "K1", "B1" vb.
  rotation    = eleman.tekla_rotation_enum,  // ZORUNLU — model.json'daki tekla_rotation_enum alanı
  depth       = "MIDDLE"                     // ZORUNLU — açık belirt, varsayılana bırakma
)
→ Başarı: {"status": "success", "guid": "..."}  → GUID'i hemen GUID tablosuna yaz
→ rotation eksik veya null: GUID tablosuna "rotation=PENDING_FIX" işaretle, devam et
→ Hata:   {"status": "error", "message": "..."}  → Tabloya ⛔ HATA yaz, devam et
```

> **rotation kaynağı:** `data/imports/[proje]_rotation.json` → `detected_rotation` alanı.
> Dosya yoksa: `outputs/model_[proje].json` içindeki `rotation_analysis.detected_rotation`.
> İkisi de yoksa: PENDING_FIX olarak işaretle.

#### 3a.1. PENDING_FIX Düzeltme (BİRİNCİL: set_beam_position)

**Tüm elemanlar oluşturulduktan SONRA çalıştır:**

```
GUID tablosunda "rotation=PENDING_FIX" var mı?
  → HAYIR: bu adımı tamamen atla

  → EVET — BİRİNCİL YOL (MCP):
    Her PENDING_FIX GUID için:
      1. select_elements_by_guid(guids=[pending_guid])
      2. set_beam_position(
           rotation = hedef_rotation,   // model.json'daki tekla_rotation_enum
           depth    = "MIDDLE"          // açık belirt
         )
      3. get_elements_properties(props=["ROTATION", "DEPTH"]) ile doğrula
         → Doğruysa: GUID tablosunu "✅ ROT_FIX" olarak güncelle
         → Yanlışsa: ⛔ aşağıdaki GERİ DÖNÜŞ yoluna geç

  → GERİ DÖNÜŞ (set_beam_position başarısızsa): C# RotFix.cs makrosu
    1. model.json rotation tablosundan profil→rotation gruplaması çıkar
    2. RotFix_{proje}.cs üret (aşağıdaki şablon):
       KRİTİK: ArrayList kullan — List<T> KULLANMA; LINQ yok
    3. run_macro(macro_path="RotFix_{proje}.cs")
       → {"status": "success"} ≠ model değişti — get_elements_properties ile doğrula
    4. Hâlâ yanlışsa: ⛔ ROT_FIX_FAILED — insana bildir
```

**Geri dönüş makro şablonu (yalnızca MCP başarısızsa):**
```csharp
// RotFix_{proje}.cs — ArrayList pattern (doğrulanmış)
using System.Collections;
using Tekla.Structures.Model;

Model model = new Model();
ModelObjectEnumerator objs = model.GetModelObjectSelector()
    .GetAllObjectsWithType(ModelObject.ModelObjectEnum.BEAM);
ArrayList beamList = new ArrayList();
while (objs.MoveNext()) beamList.Add(objs.Current);

foreach (object obj in beamList) {
    Beam b = obj as Beam;
    if (b == null) continue;
    if (b.Profile.ProfileString.StartsWith("HEB")) {
        b.Position.Rotation = Position.RotationEnum.TOP;
        b.Modify();
    }
    // ... diğer profiller
}
model.CommitChanges();
```

#### 3b. Levha ve Stiffener → `create_contour_plate`
```
create_contour_plate(
  points      = eleman.contour_points,  // [{x,y,z}, ...] — min 3 nokta
  profile     = eleman.profile,         // "PL15", "PL10" vb.
  material    = eleman.material,
  tekla_class = "1"
)
→ Başarı: guid → GUID tablosuna yaz
→ Ardından: set_elements_properties(guids=[guid], name=eleman.id)
  (create_contour_plate'de name parametresi yok — sonradan atanmalı)
```

> ⚠ **ZORUNLU — ContourPlate Poligon Doğrulaması (her plaka için):**
> `create_contour_plate` başarıyla dönse bile Tekla poligonu silip dikdörtgen çizmiş olabilir.
> ```
> get_elements_properties(guids=[guid], props=["CONTOUR_POINTS"])
> → Dönen nokta sayısı = model.json contour_points adedi mi?
>   EVET → ✅ Poligon korunmuş
>   HAYIR → ⛔ Poligon kaybolmuş — delete_elements + create_contour_plate yeniden çalıştır
>           → İkinci denemede de başarısız → ⛔ POLYGON_FAILED olarak işaretle, insana bildir
> ```
> Bu kontrol stiffener, EP, BP, gusset, cap, base — tüm contour_plate türleri için zorunludur.
> (Kaynak: 000-000-484-204_001_00 — BP plakalar dikdörtgen çizildi, API success döndü)

#### 3c. Faz Atama — Her Elemandan Hemen Sonra
```
model.json'da eleman.phase tanımlıysa:
  set_elements_properties(
    guids = [guid],
    phase = eleman.phase
  )
```

#### 3d. Konum Hatası Düzeltme ve Simetrik Kopyalama (Gerekirse)

Yanlış konumda oluşturulan eleman varsa:
```
select_elements_by_guid(guids=["hatalı-guid"])
move_elements(dx=Δx, dy=Δy, dz=Δz)   // model.json rel_start - mevcut konum farkı
get_elements_properties(...)           // yeni konumu doğrula
```

Simetrik eleman grubu varsa (kopyala, yeniden çizme):
```
select_elements_by_guid(guids=["kaynak-guids"])
copy_elements(dx=Δx, dy=Δy, dz=Δz)
// Döner: {"new_guids": [...]} → GUID tablosuna ekle
set_elements_properties(guids=new_guids, name=..., phase=...)  // isim ve faz ata
```

#### GUID Takip Tablosu
Tüm elemanlar işlendikten sonra bu tablo oluşmuş olmalı:
```
| ID   | Tip          | GUID     | Faz | Durum  |
|------|--------------|----------|-----|--------|
| K1   | column/beam  | {guid}   | 1   | ✅ OK  |
| B1   | beam         | {guid}   | 1   | ✅ OK  |
| L1   | plate        | {guid}   | 1   | ✅ OK  |
| K3   | column/beam  | —        | —   | ⛔ HATA|
```

**Hatalı eleman varsa:** Tüm elemanlar işlendikten sonra hataları toplu raporla, insana sor.
Hatalı eleman sayısı > %5 ise dur, insana bildir.

### Aşama 4: Delik / Bolt Oluştur

→ **`skills/model/DELIK_BOLT.md`** protokolünü uygula.

> ⚠ `create_bolt_group` — 2026-04-18 yaması ile kullanılabilir. Insert() false dönerse `DELIK_BOLT.md` C# BoltArray bölümüne geç.

Özet adımlar:
```
// C# BoltArray makrosu ile:
// 1. GUID tablosundan plate ve beam GUID'lerini al
// 2. BoltArray oluştur: PartToBeBolted=plate, PartToBoltTo=beam
// 3. model.json spacing_x_mm dizisiyle AddBoltDistX() çağrıları
// 4. bool ok = ba.Insert() → ok=false ise hata mesajı bas
// 5. model.CommitChanges()
→ get_elements_cut_parts() ile delik sayısını doğrula
```

### Aşama 5: Assembly ve Kaynak Tanımla

→ **`skills/model/ASSEMBLY_KAYNAK.md`** protokolünü uygula.

Özet adımlar:
```
create_weld(main_part_guid="ana-guid", secondary_part_guid="ikincil-guid", size_above=a_mm)
→ Her secondary eleman için tekrar et
→ select_elements_assemblies_or_main_parts(mode="Assembly") → 1 assembly olmalı
```

### Aşama 6: Doğrulama

**6a. Eleman sayısı:**
```
select_elements_by_filter(profile="IPE*")   → kirişler
select_elements_by_filter(profile="HE*")    → kolonlar
select_elements_by_filter(profile="PL*")    → levhalar
```

**6b. Özellik kontrolü:**
```
get_elements_properties(props=["NAME", "PROFILE", "MATERIAL", "LENGTH"])
→ model.json ile karşılaştır
```

**6c. Uyumsuz eleman düzelt:**
```
select_elements_by_guid(guids=["sorunlu-guid"])
set_elements_properties(profile=..., material=...)
get_elements_properties(...)  → tekrar doğrula
```

**6d. Görsel kontrol:**
```
color_selected(red=0, green=200, blue=0)   // doğrulanmış → yeşil
color_selected(red=255, green=0, blue=0)   // sorunlu → kırmızı
zoom_to_selection()
```

### Aşama 6e: Ekran Görüntüsü + İzometrik Karşılaştırma — ZORUNLU SON KALITE KAPISI

> Bu adım atlanamaz. 000-000-484-204_001_00'da atlandı; 5 kritik hata ancak insan incelemesiyle bulundu.

```
1. zoom_to_selection() ile tüm modeli göster
2. Tekla'da ekran görüntüsü al → outputs/Screen_Shot_[proje].png olarak kaydet
3. Orijinal çizim izometriği: data/imports/[proje]_zoom_izometrik.png
4. İki görseli yan yana karşılaştır:
   - Profil kesiti doğru mu? (Üstten flanş mı görünüyor?)
   - Bağlantı plakaları doğru yüzde mi?
   - Bracket/gusset plakalar poligon mu, dikdörtgen mi?
   - Delik dizileri görsel olarak doğru konumda mı?
5. Fark varsa → ⛔ DUR, insana raporla — pipeline'ı tamamlanmış ilan etme
6. Fark yoksa → model_confidence = "verified_visual" journal'a yaz
```

### Aşama 7: Özet Yaz ve Journal'a Logla

---

## Çıktılar
- `outputs/YYYY-MM-DD_tekla_modeller_model_ozet_[proje-adi].md` — modelleme özeti

Modelleme özeti formatı:
```markdown
# Modelleme Özeti: [Proje Adı]
Tarih: YYYY-MM-DD
Yöntem: MCP API (create_beam / create_contour_plate)

## Oluşturulan Elemanlar
| ID  | Tip    | Profil  | GUID     | Faz | Durum |
|-----|--------|---------|----------|-----|-------|
| K1  | kolon  | HEA200  | {guid}   | 1   | ✅ OK |
...

## Delikler
| Eleman | Delik Sayısı | Çap | Durum |
|--------|-------------|-----|-------|
...

## Assembly
| Durum | Yöntem |
|-------|--------|
| Birleşik (1 assembly) | create_weld |

## Doğrulama Sorunları
| Eleman | GUID | Sorun | Durum |
...

## model.json Uyumu
- Beklenen: XX eleman
- Oluşturulan: XX eleman
- Uyum: %XX

## Sonraki Adım
İnsan Tekla'da görsel kontrolü yapar → METRAJ_CIKART
```

---

## Kalite Barı
- GUID tablosu tamamlanmadan Aşama 4/5'e geçilmemeli
- Her eleman `get_elements_properties` ile doğrulanmış olmalı
- Eleman sayısı model.json ile en az %95 uyumlu olmalı
- Assembly tek birleşik assembly olarak görünmeli
- Hatalı eleman oranı > %5 ise dur ve insana raporla

---

## Araçlar (Tekla MCP)
- `tekla://connection_status` — bağlantı kontrolü
- `tekla://model_info` — model adı ve mevcut eleman sayısı (başlangıç referansı)
- `tekla://phases` — faz listesi
- `create_beam` — kiriş ve kolon oluşturma (GUID döner)
- `create_contour_plate` — levha ve stiffener oluşturma (GUID döner)
- `set_elements_properties` — faz atama, isim düzeltme, özellik güncelleme
- `select_elements_by_filter` — filtre ile seçim
- `select_elements_by_guid` — GUID ile seçim
- `get_elements_properties` — doğrulama
- `get_elements_cut_parts` — delik doğrulama
- `create_bolt_group` — ⚠ 2026-04-18 yaması; Insert() false dönerse C# BoltArray makrosuna geç
- `set_beam_position` — rotation/depth/lateral post-create düzeltme (RotFix makrosu yerine)
- `create_weld` — kaynak + assembly birleştirme
- `move_elements` — yanlış konumdaki elemanı taşıma (vektör: mm)
- `copy_elements` — simetrik eleman oluşturma, yeni GUID listesi döner
- `select_elements_assemblies_or_main_parts` — assembly kontrolü
- `draw_elements_labels` — görsel etiket
- `zoom_to_selection` — odaklanma
- `color_selected` — durum renklendirmesi

## Entegrasyon
- **CONFIDENCE_GATE çıktısı (model.json) zorunlu girdi** — doğrudan PDF_ANALIZ'dan başlamaz
- Makro dosyası üretilmez, insan makro çalıştırmaz — tamamen MCP ile yürütülür
- Doğrulama tamamlandıktan sonra insan Tekla'da görsel kontrol yapar
- Onay sonrası METRAJ_CIKART devreye girer
- ELEMAN_DOGRULA ek kalite kontrolü için çalıştırılabilir
