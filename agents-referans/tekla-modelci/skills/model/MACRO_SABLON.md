# Model: Makro Şablonu (Güvenli C# Deseni)

> ⚠ ARŞİV: Bu şablon artık birincil yöntem değildir.
> MODEL_OLUSTUR artık `create_beam` / `create_contour_plate` MCP araçlarını kullanır.
> Bu dosya yalnızca referans veya özel durumlar (toplu yeniden oluşturma, makro gerektiren özel senaryolar) için saklanmaktadır.

## Kapsam
Bu şablon yalnızca **yapısal elemanları** oluşturur:
- `Beam` (kiriş, kolon, çapraz)
- `ContourPlate` (levha, stiffener)

**Bu şablona GİRMEYECEK:**
- `BooleanPart` (delik) → GUID ile alınan part'a Father atandığında sessizce başarısız olur; `BoltArray` kullan (bkz. DELIK_BOLT.md)
- `BoltGroup` doğrudan / `create_bolt_group` (MCP) → Python.NET 3.0 Enum bug, kullanma; `BoltArray` kullan
- `Assembly.Add()` çağrısı → güvenilmez, MCP `create_weld` birleştirir

## Bilinen Hatalar (Kaçın)

| Yanlış | Doğru | Neden |
|--------|-------|-------|
| `new Phase(int)` | Kullanma | Sessiz compile hatası |
| `Phase atama` | Atama, varsayılan faz kullanılır | Sorunsuz çalışır |
| `Assembly.Add(part)` | Kullanma | Çalışmıyor, weld kullan |
| `BooleanPart.Father = GUIDdenAlinanPart` | `BoltArray` kullan | GUID ile alınan part'a Father atandığında sessizce başarısız |
| `create_bolt_group` (MCP) | `BoltArray` C# makrosu | Python.NET 3.0 Enum hatası — MCP kırık |

## Temiz Şablon

```csharp
// [PROJE ADI] - [TARİH]
// Otomatik üretildi: MODEL_OLUSTUR skill
// Kapsam: yapısal elemanlar (delik/bolt/kaynak = MCP adımı)
using System.Collections.Generic;
using Tekla.Structures.Model;
using Tekla.Structures.Geometry3d;

namespace Tekla.Technology.Akit.UserScript
{
    public class Script
    {
        public static void Run(Tekla.Technology.Akit.IScript akit)
        {
            Model model = new Model();
            if (!model.GetConnectionStatus()) return;

            // ======================================================
            // 1. TEMİZLİK — önceki çalıştırmadan kalan elemanları sil
            // ======================================================
            // NOT: Tüm Beam ve ContourPlate sil (yeniden çalıştırma güvenli)
            ModelObjectEnumerator moe = model.GetModelObjectSelector().GetAllObjects();
            List<Part> toDelete = new List<Part>();
            while (moe.MoveNext())
            {
                if (moe.Current is Beam || moe.Current is ContourPlate)
                    toDelete.Add((Part)moe.Current);
            }
            for (int i = 0; i < toDelete.Count; i++) toDelete[i].Delete();
            model.CommitChanges();

            // ======================================================
            // 2. ELEMANLAR
            // ======================================================
            string mat = "S355JR";  // veya proje standardı

            // --- Kiriş Örneği ---
            Beam b1 = new Beam();
            b1.StartPoint = new Point(0, 0, 0);
            b1.EndPoint   = new Point(1435, 0, 0);
            b1.Profile.ProfileString   = "HEA140";
            b1.Material.MaterialString = mat;
            b1.Name  = "ELEMAN-ADI";
            b1.Class = "1";          // görsel sınıf (rakam, "BlOpCl" değil)
            b1.Insert();

            // --- Levha Örneği ---
            ContourPlate p1 = new ContourPlate();
            p1.Profile.ProfileString   = "PL15";
            p1.Material.MaterialString = mat;
            p1.Name  = "LEVHA-ADI";
            p1.Class = "99";
            p1.Contour.AddContourPoint(new ContourPoint(new Point(X1, Y1, Z1), null));
            p1.Contour.AddContourPoint(new ContourPoint(new Point(X2, Y2, Z2), null));
            p1.Contour.AddContourPoint(new ContourPoint(new Point(X3, Y3, Z3), null));
            p1.Contour.AddContourPoint(new ContourPoint(new Point(X4, Y4, Z4), null));
            p1.Insert();

            // ======================================================
            // 3. CommitChanges — TEK SEFER, SONDA
            // ======================================================
            model.CommitChanges();
        }
    }
}
```

## Sık Yapısal Tipler

| Tip | C# Sınıfı | Profil Örneği |
|-----|-----------|---------------|
| Yatay kiriş | `Beam()` | IPE300, HEA140 |
| Kolon | `Beam()` — StartPoint alta | HEB240, CHS219 |
| Levha | `ContourPlate()` | PL15, PL10 |
| Taban levhası | `ContourPlate()` | PL20 |
| Stiffener | `ContourPlate()` | PL10 |

---

## BoltArray Delik Örneği (GUID ile alınan parçalar — birincil yöntem)

Bu bölümü kopyalayarak makroya ekle. `create_bolt_group` MCP kırık olduğu için delikler için bu desen kullanılır.

```csharp
using Tekla.Structures.Model;
using Tekla.Structures.Geometry3d;

// --- FindGuid Helper ---
static Part FindGuid(Model model, string g)
{
    return model.SelectModelObject(new Identifier(new Guid(g))) as Part;
}

// --- BoltArray Örneği ---
// model.json'dan: plate GUID, beam GUID, spacing_x_mm, bolt_size
Part plate = FindGuid(model, "plate-guid-buraya");
Part beam  = FindGuid(model, "beam-guid-buraya");

if (plate == null || beam == null)
{
    Tekla.Technology.Akit.Operations.Operation.DisplayPrompt("HATA: Parça bulunamadı.");
    return;
}

BoltArray ba = new BoltArray();
ba.PartToBeBolted  = plate;
ba.PartToBoltTo    = beam;
// FirstPosition = ilk satırın başı, SecondPosition = son satırın sonu
ba.FirstPosition   = new Point(x_start, y_row1, z);
ba.SecondPosition  = new Point(x_end,   y_row2, z);
ba.BoltSize        = 26.0;          // model.json bolt_diameter
ba.BoltStandard    = "7990";
ba.BoltType        = BoltGroup.BoltTypeEnum.BOLT_TYPE_WORKSHOP;

// model.json spacing_x_mm dizisiyle birebir (arası mesafeler, konum değil)
ba.AddBoltDistX(120.0);
ba.AddBoltDistX(150.0);
ba.AddBoltDistX(120.0);
// 2 satır için Y spacing:
ba.AddBoltDistY(300.0);

bool ok = ba.Insert();
if (!ok)
    Tekla.Technology.Akit.Operations.Operation.DisplayPrompt("HATA: BoltArray.Insert() başarısız.");

model.CommitChanges();
```

## Weld / Assembly Örneği (Assembly.Add() yerine)

```csharp
// create_weld (MCP) yetersiz kalırsa C# Weld kullan
Weld w = new Weld();
w.MainObject      = column;      // ana parça (main part)
w.SecondaryObject = basePlate;   // ikincil parça
w.SizeAbove       = 8.0;         // kaynak boğaz a=8mm
w.ShopWeld        = true;        // atölye kaynağı
bool wOk = w.Insert();
if (!wOk)
    Tekla.Technology.Akit.Operations.Operation.DisplayPrompt("HATA: Weld.Insert() başarısız.");
```

