# Hafıza: tekla-modelci

Tekla MCP modelleme döngülerinden doğrulanmış örüntüler.

<!-- ÖNEMLİ: Sadece birden fazla projede doğrulanmış örüntüleri yaz. Tek seferlik gözlemler journal'a. -->

## İşe Yarayanlar

- **BoltGroup ile Tek Parçaya Delik Açma:** Sadece tek bir malzemeye delik açmak istendiğinde (ikincil bir parçaya bağlamadan), `PartToBeBolted` ve `PartToBoltTo` özelliklerinin **aynı ana parçaya (main_part) atanması** bilinen ve doğru yöntemdir. Bu bir hata (kendi kendine bağlanma) değil, Tekla API'sinin beklediği kullanımdır. (Doğrulandı: 2026-04-23)
- **Standalone delik (tek parça alternatif):** `create_contour_plate` → `create_beam(class="0", profile="D18")` × N → `select(plate+cutters)` → `cut_elements_with_zero_class_parts(delete_cutting_parts=True)`. `performed_cuts=N` ile doğrula. BoltArray gereksiz, BooleanPart+GUID sessiz hata — bu MCP yöntemi ikisini de geçer. (doğrulandı: 955-617, 10×Ø18)
- **Profil bazlı filtreleme:** `select_elements_by_filter(standard_string_filters={"profile": {"conditions": {"match_type": "Starts With", "value": "IPE"}}})` güvenilir çalışıyor. `element_type` filtresi çalışmıyor — profil adıyla filtrele.
- **Büyük seçimler dosyaya yazılıyor:** >500 eleman seçiminde MCP sunucusu sonucu otomatik olarak `tool-results/` altına yazar. Python ile parse etmek zorunlu.
- **select → hemen get_elements_properties:** Seçim sonrası araya başka MCP çağrısı girmesinmeden hemen properties alınmalı; aksi takdirde seçim sıfırlanabilir.
- **Post-create rotation/depth/lateral düzeltme:** `select_elements_by_guid([guid])` → `set_beam_position(rotation="TOP", depth="MIDDLE")`. `create_beam` sırasında atlanmış ya da yanlış girilmiş position değerlerini sonradan düzeltir. `Modify()` tabanlı, `CommitChanges()` içeriyor — C# RotFix makrosunun doğrudan MCP karşılığı. Makro yalnızca MCP bağlantısı yoksa ya da `set_beam_position` başarısız olursa kullanılmalı. (kaynak: `steel.py:195-255`)
- **Hibrit Koordinat Sistemi (LCS → Global Transformation):** Ana parçanın yerel koordinat sistemini (`local_cs`) baz alarak ikincil elemanları yerleştirmek, profil rotasyonundan kaynaklanan kaymaları sıfırlar. (Doğrulandı: 2026-04-23)
  - `create_beam` çıktısındaki `local_cs` (origin, x_axis, y_axis) birincil veridir.
  - Lokal PDF ölçüleri (örn: stiffener z=500) mutlaka `local_to_global` aracından geçirilerek Tekla uzayına çevrilmelidir.
  - Ajanın (Claude'un) manuel 3D koordinat hesabı yapması hatalara açık olduğundan yasaklanmıştır; dönüşüm her zaman MCP aracıyla yapılmalıdır.
- **Position ve Derinlik (JRN-03) Kesin Kuralı:** Tekla varsayılan (default) değerleri profil tipine göre değiştiğinden, `create_beam` ve `create_contour_plate` çağrılarında her zaman `rotation`, `depth` ve `lateral` açıkça girilmelidir. (Doğrulandı: 2026-04-23)
  - Uç plakalar (Base/Cap) için `depth=MIDDLE` kullanımı montaj boyunda (length_mm) kalınlık hatası oluşturur. Taban plakası `BELOW`, Başlık plakası `ABOVE` olmalıdır.
  - Parametre eksikse MCP aracı hata döndürür; bu durumda model.json'ı güncelle veya insana sor.
- **Kısmi Modelleme (JRN-04):** Bir assembly'de bazı parçaların belirsiz olması tüm süreci durdurmamalıdır. Emin olunan parçalar modellenip belirsizler için kırmızı (Class 999) yer tutucular konulması, insan kontrolünü hızlandırır. (Doğrulandı: 2026-04-23)
  - Yer tutucu parçalar `DUMMY_PART_` önekiyle adlandırılmalıdır.
  - `tekla_modelci_status.json` içinde `partial: true` flag'i set edilerek orchestrator bilgilendirilmelidir.
- **Rotation Kalibrasyonu (JRN-05):** PDF'ten gelen "FRONT" etiketi Tekla'daki `RotationEnum.FRONT` ile her zaman eşleşmez (örn: UPN/L profillerde `BELOW` çıkabilir).
  - Asimetrik profillerde (`L, C, UPN` vb.) rotasyonu nötr (`TOP`) başlatıp `local_cs` üzerinden PDF ile görsel/vektörel kıyaslama yapmak en güvenilir yöntemdir.
  - Hatalı rotasyon sadece profilin yönünü değil, üzerine gelecek deliklerin de yanlış yüzde açılmasına (955-615 hatası) neden olur. (Doğrulandı: 2026-04-23)
- **Stiffener/Levha Geometrisi (JRN-06):** Levhaların otomatik olarak dikdörtgen (bounding box) kabul edilmesi, karmaşık bağlantı plakalarında (köşe kesimli, kaynak boşluklu) geometrik hatalara yol açar. (Doğrulandı: 2026-04-23)
  - `contour_points` mevcutsa her zaman poligon olarak modellenmelidir.
  - DXF polyline verisi, PDF analizinden daha yüksek önceliğe sahiptir.
  - `Performed_cuts = N` başarısı geometri doğruluğunu kanıtlamaz; poligon noktalarının COG kontrolü yapılmalıdır.
- **Plate Center-Origin (JRN-07):** Tekla'da plakalar (ContourPlate) modellendiğinde, (0,0,0) noktası plaka geometrisinin merkezine denk gelecek şekilde noktalar normalize edilmelidir. 
  - Bu yöntem, `model.json`'daki delik koordinatlarının (holes.positions) plaka ile tam üst üste binmesini sağlar. 
  - Top-Left bazlı noktalar (0,0'dan başlayıp W,H'ye giden) deliklerin kaymasına (Offset hatası) neden olur. (Doğrulandı: 2026-04-23)
- **Hole Direct Read (JRN-08):** Delik koordinatları asla parça boyutlarından yola çıkılarak hesaplanmamalıdır (örn. "kenardan 40mm" diyerek toplama yapmak). 
  - `model.json` içindeki `holes.positions` listesi tek mutlak kaynaktır. 
  - Bağımsız hesaplama denendiğinde (000-000-522-607) 18mm sapma oluştuğu kanıtlanmıştır. 
  - Doğrulama için `get_elements_cut_parts` çıktısı ile JSON kıyaslanmalıdır. (Eklendi: 2026-04-23)
- **Net Length vs Length (JRN-11):** `create_beam` aracına asla `length_mm` (montaj boyu) verilmemelidir. 
  - `length_mm` plakalar dahil boydur, `net_length_mm` ise profilin kendi boyudur. 
  - Montaj boyunun kullanılması 30mm fazla uzun profile ve yanlış delik konumlarına yol açar. (Doğrulandı: 2026-04-23)
- **Rotation Verification Loop (JRN-12):** Asimetrik profillerde (özellikle L ve C) sadece rotasyon atamak yeterli değildir. 
  - Tekla API'si bazen profili beklenenden farklı yönde çevirebilir (BULGU-1). 
  - `zoom_to_selection` ile görsel teyit yapılması ve `get_elements_properties` ile doğrulanması 3 projede tespit edilemeyen hataları engelleyecektir. (Eklendi: 2026-04-23)
- **Test Altyapısı (ÖNM-01):** Sistem artık kapsamlı bir test altyapısına sahiptir.
  - **Unit Testler:** `tests/unit/` altında bulunur. Tekla Structures bağlantısı gerektirmez. `conftest.py` içindeki mocklar ve `TEKLA_MCP_MOCK_TEKLA=true` ortam değişkeni ile çalışır.
  - **Functional Testler:** `tests/functional/` altında bulunur. Gerçek bir Tekla örneği ve açık bir model gerektirir.
  - **Geliştirme Kuralı:** Yeni bir özellik eklediğinde veya bir bug düzelttiğinde, mutlaka `tests/unit/` altında bir test dosyası oluştur veya mevcut olanı güncelle. `python -m pytest tests/unit` komutuyla testleri doğrula. (Eklendi: 2026-04-23)
- **Security & Config (ÖNM-06):** Hassas veriler (API anahtarları, yerel dosya yolları vb.) asla kodun içinde saklanmaz.
  - Bu tür veriler `.env` dosyasında tutulur ve `.env` dosyası git tarafından takip edilmez (`.gitignore` içindedir).
  - Yapılandırma değişiklikleri için `config/*.json` dosyaları kullanılır (ancak bunlar da yerel makineye özel olabileceğinden dikkatli yönetilmelidir). (Eklendi: 2026-04-23)
- **Semantic Search & Startup (ÖNM-05):** Embedding sistemi `settings.json` üzerinden kontrol edilir.
  - Sunucu başlatıldığında `@mcp.on_startup` hook'u sayesinde `TemplateAttributeParser.preload()` otomatik olarak çalışır.
  - Bu sayede Tekla öznitelikleri (attributes) ve semantik eşleştirmeler sunucu hazır olduğunda bellekte hazır bekler.
  - Semantik eşleştirme (`TemplateAttributeParser.resolve_attributes`) sayesinde "painting area" gibi sorgular otomatik olarak "AREA_NET" gibi Tekla özniteliklerine çözümlenir. (Eklendi: 2026-04-23)
- **PolyBeam & Radius (ÖNM-04):** `tool_create_poly_beam` ve `tool_create_contour_plate` araçları artık `radius` parametresini desteklemektedir.
  - Her bir nokta (point) sözlüğüne `{"x": 100, "y": 0, "z": 0, "radius": 50}` şeklinde radius eklenebilir.
  - Radius değeri Tekla'da `CHAMFER_ROUNDING` tipinde bir pah oluşturur.
  - Köşeli (dik) noktalar için `radius` değeri gönderilmemeli veya 0 gönderilmelidir. (Eklendi: 2026-04-23)
- **Commit Management (ÖNM-03):** Tekla modelindeki değişikliklerin (Insert, Modify, Delete) kalıcı olması için `CommitChanges()` çağrılmalıdır.
  - **Standart Yöntem:** Manuel çağrı yerine `with model.commit_scope():` context manager'ını kullan.
  - **Fayda:** Bu yapı, işlem sırasında hata oluşsa bile `finally` bloğu sayesinde commit yapılmasını garanti eder ve kod tekrarını önler. (Eklendi: 2026-04-23)
- **Object Details (ÖNM-02):** `TeklaBolt` ve `TeklaWeld` sınıfları artık boştur. 
  - Boltlar için `bolt_size`, `bolt_standard` ve tüm cıvata konumları (`positions`) sorgulanabilir.
  - Kaynaklar için `size_above`, `size_below` ve `is_shop_weld` özellikleri snapshot'larda görünür olacaktır.
  - Bu sayede modeldeki detaylı birleşim elemanlarının doğruluğu otomatik olarak kontrol edilebilir. (Eklendi: 2026-04-23)

## İşe Yaramayanlar

- **element_type filtresi:** Tekla MCP'de element_type filtresi çalışmıyor — profil adı üzerinden filtrele.
- **CHS vs CFCHS:** `Starts Top With "CHS"` filtresi `CFCHS88.9X4.0` gibi cold-formed profilleri **yakalamaz**. CFCHS, CFSHS, CFRHS ayrı prefix — her biri için ayrı `select_elements_by_filter` çağrısı gerekir.
- **Dışarıdan Yüklenen Modern `.cs` Makroları (run_macro):** Makrolarda C# `System.Linq`, `List<T>`, interpolation, ya da yanlış Enum tanımları (örneğin var olmayan `DepthEnum.DOWN`) kullanılırsa sistem hata logu vermeden "sessizce (silent)" çöker ancak `{"status": "success"}` raporlar. Kodlar kesinlikle eski nesil .NET (ArrayList, nesne cast `as Part`) standartlarında olmalı. Makronun MCP'den "success" dönmesi, modelin değiştiği anlamına **gelmez**.
- **`create_bolt_group` MCP:** Python.NET 3.0 Enum hatası (`int can not be converted to Enum implicitly`) — `Position.Depth/Rotation` alanlarına `int(0)` atamadan kaynaklanıyordu. **2026-04-18 sunucu yaması ile düzeltildi** (`Position.DepthEnum.MIDDLE` / `Position.RotationEnum.FRONT`). Yamalı sunucuda önce `create_bolt_group` dene; başarısızsa (Insert() false) C# BoltArray makrosuna geç.
- **`BooleanPart` + GUID ile alınan part:** `model.SelectModelObject(new Identifier(new Guid(...))) as Part` ile alınan parçaya `BooleanPart.Father` atandığında sessizce başarısız olur — macro `{"status":"success"}` döner ama delik açılmaz. BooleanPart yalnızca aynı makro içinde `Insert()` edilmiş fresh parts ile çalışır. GUID-fetched parts için BoltArray kullan.

## Rotation Kuralları

| Eleman Tipi | Doğru Rotation | Yanlış Varsayılan | Neden |
|-------------|---------------|-------------------|-------|
| Kolon | `TOP` | FRONT | Konsol başlığa (flange) otursun |
| Konsol (IPE) | `FRONT` | — | Ağ dikti duracak |
| Taban Plakası | `Depth=BEHIND` | — | Z referanstan aşağıya, havada kalmasın |

- Bu değerler belirtilmezse Tekla varsayılanları hatalı model üretir — FixModel.cs gerekir. **model.json'a rotation parametrelerini her zaman açık yaz.**

### PDF Etiketi ≠ Tekla RotationEnum — KALİBRASYON GEREKLİ (gözlemlendi 2026-04-17, proje 000-000-964-152)

- **`cizim-analisti`'nin çıkardığı "FRONT" etiketi, Tekla `RotationEnum.FRONT` ile aynı şey değildir.**  
  UPN120 projesinde analiz `FRONT` dedi, `RotationEnum.FRONT` atandı, modelde profil **BELOW** görüldü.  
- model.json'a iki alan yazılmalı:  
  ```json
  "rotation_analiz_etiketi": "FRONT",
  "tekla_rotation_enum": "BELOW"
  ```  
  `tekla_rotation_enum` boşsa veya belirsizse modellemeye **başlanmaz** — SORU-XXX açılır.
- Kalibrasyon tablosu oluşturulana kadar UPN/U-profillerde rotation için insan onayı zorunlu.

### At Depth — create_beam Parametresi Destekliyor (GÜNCELLEME 2026-04-18)

- **`create_beam` MCP aracı `depth` parametresini DESTEKLER** — geçerli değerler: `FRONT | MIDDLE | BEHIND`.
- Yapısal kirişler için `create_beam(..., depth="MIDDLE", rotation="TOP")` şeklinde tek çağrıda pozisyon atanır.
- `depth=None` bırakılırsa Tekla kendi varsayılanını atar (profil tipine göre değişir — güvenli değil). **Her zaman açık belirt.**
- Oluşturma sonrası düzeltme gerekirse: `select_elements_by_guid([guid])` → `set_beam_position(depth="MIDDLE")` — C# RotFix makrosu artık çoğu durumda gerekmiyor.

### L / C / Z / T Profillerinde Rotation — KRİTİK (doğrulandı 2026-04-15, proje 000-000-955-615)

- **L (açı) ve diğer asimetrik kesitlerde rotation belirsizse modellemeye BAŞLAMA.**  
  `model.json` içinde `rotation_analysis.detected_rotation` boş veya güven < 0.90 ise → SORU-XXX açarak insana sor; "blok etkisi düşük" gerekçesi kabul edilemez.
- **Delik kesici koordinatları (Y, Z) profil rotasyonuna göre belirlenir.** Rotasyon yanlışsa kesiciler yanlış yüzeye gider — `performed_cuts = N` başarılı görünse de delikler hatalı kenarda açılır.
- **Varsayılan rotation ile oluşturulan L profil:** Tekla ağ (web) yönünü seçer, flanş yönü PDF'ten farklı olabilir. `rotation=TOP/FRONT/BEHIND` her proje için PDF'ten doğrulanmalı.
- Hata örüntüsü: profil boyu ✅, delik adedi ✅, malzeme ✅ — ama rotation + delik yönü ⛔ → parça ıskartaya gider.

## Malzeme Dönüşüm Kuralları

- **S355J2 → S275JR:** Bazı projeler çizimdeki S355J2'yi S275JR olarak uygular. Requirements.json kontrol et, belirsizse sor.

## Eleman Yönü — Ana Parça CS'i

- **İkincil eleman koordinatları ana parçanın yerel CS'ine göre hesaplanmalı.** HEB300 kolon TOP rotation ile modelleniyorsa, levha ve stiffener kontur noktaları kolonun yerel x/y/z eksenleri baz alınarak yazılmalı. Global koordinat = doğru değil, profil geometrisi referans.
- **Profil kesit boyutlarını kullan:** HEB300 flange=300mm, web=300mm. Plaka kenarı hesabı: `plaka_x_max = flange/2 + çıkma_mesafesi`. Cap plate: 300/2 + 290 = 440mm → x_max=440. Bunu create_contour_plate kontur noktalarına doğrudan yaz.

## ContourPlate — Position At Depth Kuralı (güncellendi 2026-04-19)

- **MIDDLE uç plakalar için YASAK** — sadece ortadaki stiffener/gusset'te kabul edilebilir:
  - Base plate (altta, z_origin=0): `depth_placement = BELOW` → plaka z=−t..0
  - Cap plate (üstte, z_origin=net_length_mm): `depth_placement = ABOVE` → plaka z=net_length..net_length+t
  - Stiffener (ortada): MIDDLE kabul edilebilir ama model.json'daki z koordinatı merkez olmalı
- Her iki uç plaka MIDDLE çizilirse dıştan dışa ölçüde `+t` mm (iki taraf toplamı) fazlalık oluşur. (doğrulandı: 000-000-522-607 → hedef 3915mm, ölçülen 3930mm)

## Profil Uzunluğu — net_length_mm Kuralı (doğrulandı 2026-04-19, proje 000-000-522-607)

- **`create_beam` çağrısında her zaman `net_length_mm` kullan — `length_mm` değil.**
  - `length_mm`: dıştan dışa montaj yüksekliği (plakalar dahil) → sadece assembly doğrulamasında kullan
  - `net_length_mm`: profilin gerçek boyu → `create_beam(length=model["main_beam"]["net_length_mm"])`
- `model.json`'da `net_length_mm` yoksa → SORU-XXX aç, profil boyunu insana sor

## Delik Konumları — model.json Doğrudan Okuma Kuralı (doğrulandı 2026-04-19, proje 000-000-522-607)

- **Plaka delikleri için `plates[i].holes.positions[j].x/y` değerlerini `model.json`'dan doğrudan kullan.**
  - Bu değerler plaka merkezi (0,0) baz alınarak mm cinsindendir (center-origin)
  - Tekla global'a dönüşüm: `global_x = plate_center_x + hole.x`, `global_y = plate_center_y + hole.y`
  - Boyutlardan bağımsız yeniden hesap (`pattern_x_mm / 2` vb.) **yasak** — model.json birincil kaynak
- Kesme sonrası doğrulama: `get_elements_cut_parts()` → delik merkez koordinatı model.json değeriyle ±2mm içinde olmalı; dışarıdaysa kırmızıya al ve insana bildir
- `performed_cuts = N` yalnızca kesme işleminin gerçekleştiğini kanıtlar — delik konumunu garanti etmez

## Polygon Levha Geometrisi

- **Stiffenerlar ve bağlantı plakaları her zaman dikdörtgen değildir.** Köşe kesimleri (chamfer), kaynak boşlukları (coping), profil içine giren kesimler olabilir. DXF'te polyline noktaları tam sırayla model.json'a yazılmalı — sadece genişlik×yükseklik değil.
- model.json'da `contour_points: [{x,y,z}, ...]` formatında poligon noktaları belirtilmeli; dış ölçüler yeterli değil.

## Assembly Birleştirme

- **Assembly.Add() çalışmıyor** — ne MCP ne C# macro'da güvenilir değil. Birleştirmek için `create_weld` kullan — `ASSEMBLY_KAYNAK.md` protokolüne bak.
- **C# macro Assembly.Add() + asm.Modify() da sessizce başarısız olabilir:** `{"status":"success"}` döner ama assembly modelde oluşmaz. Assembly oluşumu için `select_elements_assemblies_or_main_parts(mode="Assembly")` ile bağımsız doğrulama yap.

## Model Doğrulama

- **Konsol sayısı insan onayı sonrası değişebilir:** model.json onaylandıktan sonra bile insan düzeltme yapabilir (örn. 4→3 konsol). GUID tablosunu buna göre güncelle, `model_ozet.md`'den fazladan satırı düş.
- **Ağırlık karşılaştırması profil doğrulama yöntemi:** Profil belirsizse hedef ağırlığa göre doğrula (IPBL240 → HEA240 örneği: HEB240 ile fark %23, HEA240 ile fark %0.2).

## Delik Yöntemi

| Yöntem | Durum | Ne Zaman Kullan |
|--------|-------|-----------------|
| `create_bolt_group` (MCP) | ⛔ BROKEN | Kullanma — Python.NET 3.0 Enum hatası |
| `BoltArray` (C# makro) | ✅ BİRİNCİL | GUID ile alınan parçalar dahil her durumda |
| `BooleanPart` (C# makro) | ⚠ KISITLI | Yalnızca aynı makroda fresh-created parts; GUID-fetched = sessiz hata |

**BoltArray minimum desen:**
```csharp
BoltArray ba = new BoltArray();
ba.PartToBeBolted = plate;   // GUID ile alınmış olabilir
ba.PartToBoltTo   = beam;
ba.FirstPosition  = new Point(x_start, y_row1, z);
ba.SecondPosition = new Point(x_end,   y_row2, z);
ba.BoltSize       = 26.0;
ba.BoltStandard   = "7990";
ba.BoltType       = BoltGroup.BoltTypeEnum.BOLT_TYPE_WORKSHOP;
ba.AddBoltDistX(spacing1);  // model.json spacing_x_mm ile birebir
ba.AddBoltDistY(row_gap);
bool ok = ba.Insert();  // false ise DisplayPrompt ile hata bas
```

## Bağlantı Tipi → Tekla Bileşeni Örüntüleri

-

## MCP Bağlantı / Seçim Notları

- **Bilinen çalışan property'ler:** `LENGTH`, `WEIGHT`, `PART_POS`, `PROFILE`, `MATERIAL`, `PHASE`, `GUID`.
- **Inline eşiği:** ≤~200 eleman inline döner; daha fazlası dosyaya yazılır.

## Açık SORU → Modelleme Blocker Kuralı

- **Boyutu/konumu belirsiz eleman varsa o eleman için modellemeyi durdur.** SORU açıkken model.json'da `confidence < 0.70` olan eleman modellenemez — insana sor, yanıt bekle. Eleman atlanıyorsa model.json'a `"modelleme_durumu": "BEKLEMEDE — SORU-XXX"` notu ekle.

## Numaralandırma API Kısıtlaması

- **`Operation.NumberAll()` Tekla API'de mevcut değil.** `Tekla.Structures.Model.Operations.Operation` sınıfında `NumberAll`/`NumberModified` metodu yok — sadece `IsNumberingUpToDate`, `IsNumberingAllowed`, `GetSimilarNumberedObjects` var.
- C# makrosu ve MCP ile numaralandırma **tetiklenemiyor**. PART_POS, Tekla UI'dan elle çalıştırılmadan `P/0(?)` kalır.
- **Tek çözüm:** Tekla UI → Çizimler ve Raporlar → Numaralandırma → Tümünü Numaralandır (Number All). İnsan etkileşimi zorunlu.
- `set_elements_properties(part_prefix, part_start_number)` çalışıyor — ön ek ve başlangıç numarası API ile atanabilir, ama Tekla'nın numaralandırma algoritması (P/1, P/2 atama) sadece UI üzerinden tetikleniyor.

## ⚠ ZORUNLU KONTROL LİSTESİ — Modelleme Öncesi (2026-04-27 eklendi)

Bu kontroller atlanamaz. 000-000-484-202 projesinde MEMORY.md'de yazılı kurallara rağmen 3 kural ihlal edildi.

**create_beam öncesi:**
- [ ] `net_length_mm` mı kullanıyorum? (`gross_length_mm` değil — JRN-11)
- [ ] `end_point.z = net_length_mm` mi? (plakalar dahil ölçü değil)

**create_contour_plate öncesi:**
- [ ] Her plaka kalınlığı PDF'ten okundu mu? (varsayılan/türetme yasak)
- [ ] Alt (base) plaka → `BELOW`, üst (cap) plaka → `ABOVE` mi? (MIDDLE yasak — JRN-03)
- [ ] Delik konumları PDF boyutlandırma çizgisinden mi okundu? (plaka boyutundan hesap yasak)

**create_bolt_group sonrası:**
- [ ] NC `BO` satır sayısı beklenen delik sayısına eşit mi? (eşit değilse MCP restart)

**model.json okuma öncesi:**
- [ ] PDF'teki tüm plakalar model.json'da var mı? (özellikle taban bölgesi 0–200mm)

---

## create_bolt_group — NC Doğrulama Kuralı (2026-04-27)

- **create_bolt_group → NC export → BO satır sayısı = beklenen delik sayısı zorunlu doğrulama.**
  - MCP oturum bozukluğunda API `{"status":"success"}` döndürse de yalnızca ilk pozisyonu işleyebilir.
  - 000-000-484-202: 4×Ø32 oluşturuldu ama NC'de 1×Ø34 çıktı → MCP restart + yeniden oluştur.
  - `BO` satır = kaç tane işlendi; `BO` satır ≠ beklenen → sorun var.
- **MCP restart sonrası:** `select_elements_by_guid` ile tüm GUID'ler doğrula. Bulunamazsa yeniden oluştur ve journal'ı güncelle.

## check_clashes — Kalıcı Hata (2026-04-27)

- `check_clashes` MCP'de `'NoneType' object is not callable` hatası — kalıcı, restart ile çözülmüyor.
- Pipeline blocker olarak tanımlanmamalı. Çakışma kontrolü: Tekla UI → Araçlar → Çakışma Kontrolü.

## ⚠ 000-000-484-204_001_00 Retrospektif Bulguları (2026-04-30 — 8. güncelleme)

Bu proje insan görsel incelemesiyle **5 kritik modelleme hatası** tespit etti. Aşağıdaki kurallar bunlara yanıt olarak eklendi.

### HATA-1: EP At Depth=MIDDLE → Dıştan Dışa 10mm Kısalma
- EP plakaları `At Depth: Middle` ile oluşturuldu → her uçta 5mm (t/2) profil üzerine bindi.
- Sonuç: 3290mm modellendi, olması gereken 3300mm.
- **KURAL (MIDDLE yasağına ek):** Uç plakalar (end plate, başlık plakası) için `At Depth` ASLA `MIDDLE` değil — `OUTSIDE` (veya `FRONT`/`BEHIND` yüze göre). Bu kural JRN-03 ve ContourPlate At Depth Kuralı ile tutarlıdır; bu proje ikinci ihlaldir.

### HATA-2: Rotation Doğrulaması İçin Üst Görünüm Zorunlu
- `rotation_analyzer` + vision modeli FRONT döndürdü; doğru cevap TOP.
- BEAM elemanlarının standart konumu: **üstten bakıldığında flanş görünür, web dikey** — bu TOP rotation demektir.
- **KURAL:** Her projede `zoom_uc_ust.png` (veya eşdeğer üst görünüm) açık soru olarak kontrol edilmeli: "Üstten bakınca flanş mı, web mi görünüyor?" Flanş → TOP, Web → FRONT.
- **KURAL:** BEAM elemanları için rotation varsayılanı TOP'tur; rotation_analyzer FRONT dese bile üst görünüm ile çelişiyorsa TOP alınır. Çelişkide insan onayı zorunlu.

### HATA-3+4: Rotation Yanlışsa Koordinatlar ve Delikler Cascade Hatalı Olur
- Rotation HATA-2 nedeniyle yanlış atandı → tüm bağlantı koordinatları ve delik konumları yanlış hesaplandı.
- **KURAL:** Rotation hatası en erken aşamada yakalanmalı. Rotation belirsiz veya çelişkili iken model.json'a koordinat yazılamaz, modellemeye başlanamaz.

### HATA-5: ContourPlate Poligon Aktarımı Doğrulanmalı
- BP bracket plakalar poligon konturla tanımlandı (`model.json`'da `contour_points` mevcut) ama Tekla'da dikdörtgen çizildi — kontur aktarımı sessizce başarısız oldu.
- **KURAL:** `create_contour_plate` sonrası `get_elements_properties` ile kontur noktaları geri okunmalı. Nokta sayısı `model.json`'daki `contour_points` ile eşleşmiyorsa → yeniden oluştur, konturları tek tek doğrula.

### HATA-6: Model Confidence ≠ Analysis Confidence
- `global_confidence=0.87` çizim okuma kalitesini yansıtıyor; modelin Tekla'daki geometrik doğruluğunu ölçmüyor.
- Kullanıcı değerlendirmesi: gerçek model doğruluğu ≤0.40.
- **KURAL:** İki ayrı skor tutulmalı:
  - `analysis_confidence` → PDF/DXF okuma kalitesi (cizim-gorsel-analisti çıktısı)
  - `model_confidence` → Tekla model geometri doğruluğu (insan görsel onayı ile belirlenir)
- Model confidence, insan görsel karşılaştırması tamamlanmadan `tekla_modelci_status.json`'a yazılmamalı.

### Zorunlu Kontrol Listesi Güncellemesi

Aşağıdaki maddeler mevcut listeye eklendi:

**create_beam öncesi (EK):**
- [ ] `rotation_analyzer` çıktısı ile `zoom_uc_ust.png` üst görünüm karşılaştırıldı mı? (Flanş görünüyor → TOP, Web görünüyor → FRONT)
- [ ] BEAM elemanı için rotation=TOP varsayılan mı? (FRONT ancak üst görünüm ile ispatlanmışsa kullanılır)

**create_contour_plate sonrası (EK):**
- [ ] `get_elements_properties` ile kontur noktaları geri okundu mu? (nokta sayısı model.json'daki `contour_points` adediyle eşleşmeli — farklıysa poligon kaybolmuş demektir)

**Modelleme tamamlandıktan sonra (EK):**
- [ ] Ekran görüntüsü (`zoom_to_selection` veya UI) ile orijinal çizim izometrisi karşılaştırıldı mı?
- [ ] `model_confidence` (geometrik doğruluk) ve `analysis_confidence` (çizim okuma) ayrı ayrı raporlandı mı?

---

## Son Güncelleme

2026-04-30 (8. güncelleme) — 000-000-484-204_001_00 insan retrospektifi: 5 kritik hata kaydedildi (EP MIDDLE, rotation TOP/FRONT, koordinat cascade, delik cascade, BP poligon kaybı, model/analysis confidence ayrımı). Zorunlu kontrol listesine 5 yeni madde eklendi.
# 2026-04-30 MCP API kisiti notu

- `check_clashes` artik `model.GetClashCheckHandler()` + Tekla Events akisini dener. Events API yoksa `manual_required` doner; bu "MCP bozuk" degil, Tekla API/UI kisiti olarak ele alinmalidir.
- `run_numbering` varsayilan olarak `manual_required` doner. Macro denenirse bile sadece `IsNumberingUpToDateAll=True` dogrulanirsa success sayilir.
