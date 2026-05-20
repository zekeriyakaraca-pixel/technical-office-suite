# Model: Delik ve Bolt Oluşturma

## Yöntem Karşılaştırması

| Yöntem | Durum | Ne Zaman |
|--------|-------|----------|
| `cut_elements_with_zero_class_parts` (MCP) | ✅ BİRİNCİL — Standalone delik | Tek parçaya standalone delik (bağlantı değil). create_beam(class=0) + bu MCP araç. |
| `BoltArray` (C# makro) | ✅ BİRİNCİL — İki parça bağlantısı | İki parçayı bağlayan bolt deliği. GUID ile alınan parçalarla çalışır. |
| `create_bolt_group` (MCP) | ⛔ BROKEN | Kullanma — Python.NET 3.0 Enum hatası. |
| `BooleanPart` (C# makro) | ⚠ KISITLI | Yalnızca aynı makroda fresh-created parts. GUID-fetch = sessiz hata. |

---

## BİRİNCİL PROTOKOL 1: MCP Standalone Delik (cut_elements_with_zero_class_parts)

**Ne zaman:** Tek bir parçaya (levha, kiriş) bağlantı amacı olmayan delikler.

```
1. create_contour_plate veya create_beam → parça GUID
2. create_beam(profile="D18", tekla_class="0") × N → kesici GUIDs (Z ekseni boyunca parçayı geçecek şekilde)
3. select_elements_by_guid([parça_guid, kesici1, kesici2, ...])
4. cut_elements_with_zero_class_parts(delete_cutting_parts=True)
   → performed_cuts=N ile doğrula ✅
```

**Doğrulanmış örnek (955-617, 10×Ø18):**
```python
# Adım 2 — kesici beam örneği:
create_beam(start_point={"x": 263, "y": 40, "z": -15},
            end_point={"x": 263, "y": 40, "z": 15},
            profile="D18", tekla_class="0")
# Adım 4 — sonuç:
# {"performed_cuts": 10} ✅
```

---

## BİRİNCİL PROTOKOL 2: C# BoltArray Makrosu

---

## BİRİNCİL PROTOKOL: C# BoltArray Makrosu

### Ön Koşul
- MODEL_OLUSTUR Aşama 3 tamamlandı — GUID tablosu hazır
- Her elemanın GUID'i `create_beam` / `create_contour_plate` dönüş değerinden kaydedildi

### Şablon

```csharp
// FindGuid helper — makroya ekle
static Part FindGuid(Model model, string g)
{
    return model.SelectModelObject(new Identifier(new Guid(g))) as Part;
}

// Delik oluştur
static bool BoltDelik(Model model, Part plate, Part beam,
                      double xStart, double xEnd,
                      double yRow1, double yRow2, double z,
                      double boltSize, double[] spacingX, double spacingY)
{
    BoltArray ba = new BoltArray();
    ba.PartToBeBolted  = plate;
    ba.PartToBoltTo    = beam;
    ba.FirstPosition   = new Point(xStart, yRow1, z);  // 1. satır başı
    ba.SecondPosition  = new Point(xEnd,   yRow2, z);  // 2. satır sonu
    ba.BoltSize        = boltSize;
    ba.BoltStandard    = "7990";
    ba.BoltType        = BoltGroup.BoltTypeEnum.BOLT_TYPE_WORKSHOP;

    // X spacing (model.json spacing_x_mm dizisiyle birebir)
    foreach (double d in spacingX)
        ba.AddBoltDistX(d);

    // Y spacing (tek satır ise eklemeyebilirsin)
    ba.AddBoltDistY(spacingY);

    bool ok = ba.Insert();
    if (!ok)
        Tekla.Technology.Akit.Operations.Operation.DisplayPrompt(
            "HATA: BoltArray.Insert() başarısız — GUID ve koordinatları kontrol et.");
    return ok;
}
```

### Kullanım Örneği (Danieli — Base Plate Ø26, NR.12)

```csharp
Part basePlate = FindGuid(model, "0f757dc9-b6d5-42a9-9818-b22ae8dc7f6c");
Part column    = FindGuid(model, "8d701d13-ae5f-494e-b65d-b2480ef38603");

// Spacing: 45-120-120-150-120-120-45 → X konumları -315 .. +315
// FirstPosition = sol-alt, SecondPosition = sağ-üst
BoltArray ba = new BoltArray();
ba.PartToBeBolted  = basePlate;
ba.PartToBoltTo    = column;
ba.FirstPosition   = new Point(-315.0, -150.0, 0.0);
ba.SecondPosition  = new Point( 315.0,  150.0, 0.0);
ba.BoltSize        = 26.0;
ba.BoltStandard    = "7990";
ba.BoltType        = BoltGroup.BoltTypeEnum.BOLT_TYPE_WORKSHOP;
ba.AddBoltDistX(120.0);
ba.AddBoltDistX(120.0);
ba.AddBoltDistX(150.0);
ba.AddBoltDistX(120.0);
ba.AddBoltDistX(120.0);
ba.AddBoltDistY(300.0);  // 2 satır, 300mm aralık
bool ok = ba.Insert();
model.CommitChanges();
```

### Doğrulama

```
// Makro bittikten sonra MCP ile doğrula:
select_elements_by_guid(guids=["levha-guid"])
get_elements_cut_parts()  → beklenen delik sayısıyla karşılaştır
```

---

## KISITLI DURUM: BooleanPart (Sadece Fresh Parts)

BooleanPart yalnızca aynı makro içinde `Insert()` edilmiş parçalarda çalışır:

```csharp
// Bu çalışır — cutter ve father aynı makroda oluşturuldu
Beam cutter = new Beam();
cutter.StartPoint = new Point(x, y, z1);
cutter.EndPoint   = new Point(x, y, z2);
cutter.Profile.ProfileString = "D26";
cutter.Class = "BlOpCl";  // zorunlu
cutter.Insert();           // Insert önce

BooleanPart hole = new BooleanPart();
hole.Father        = freshCreatedPart;  // aynı makroda oluşturulmuş parça
hole.Type          = BooleanPart.BooleanTypeEnum.BOOLEAN_CUT;
hole.OperativePart = cutter;
hole.Insert();

// ⛔ ÇALIŞMAZ — GUID ile alınan parça:
// Part plate = model.SelectModelObject(new Identifier(new Guid("..."))) as Part;
// hole.Father = plate;  ← sessizce başarısız
```

---

## 000-000-979-831 Referans Değerleri

### SAG-UC-LEVHA — 6 delik Ø18
```
Pozisyonlar (global koordinatlar):
Y=-30, Z=-25.5   → {"x": 1435, "y": -30.0, "z": -25.5}
Y=+30, Z=-25.5   → {"x": 1435, "y":  30.0, "z": -25.5}
Y=-30, Z=-90.5   → {"x": 1435, "y": -30.0, "z": -90.5}
Y=+30, Z=-90.5   → {"x": 1435, "y":  30.0, "z": -90.5}
Y=-30, Z=-155.5  → {"x": 1435, "y": -30.0, "z": -155.5}
Y=+30, Z=-155.5  → {"x": 1435, "y":  30.0, "z": -155.5}
```

### ALT-UC-LEVHA — 4 delik Ø18
```
Pozisyonlar (global koordinatlar):
X=-30, Y=-32.5  → {"x": -30.0, "y": -32.5, "z": -635}
X=-30, Y=+32.5  → {"x": -30.0, "y":  32.5, "z": -635}
X=+30, Y=-32.5  → {"x":  30.0, "y": -32.5, "z": -635}
X=+30, Y=+32.5  → {"x":  30.0, "y":  32.5, "z": -635}
```

---

## Sık Hatalar

| Hata | Neden | Çözüm |
|------|-------|-------|
| `create_bolt_group` hata veriyor | Python.NET 3.0 Enum bug | C# BoltArray makrosuna geç |
| BooleanPart delik açmıyor, macro "success" diyor | GUID ile alınan part'a Father atandı | BoltArray kullan |
| BoltArray.Insert() false döndü | Koordinatlar parçanın dışında kalmış | FirstPosition/SecondPosition spacing'ini kontrol et |
| Delik yanlış yerde | Koordinatlar levha yerel sistemiyle karıştırıldı | Global model koordinatları kullan |
