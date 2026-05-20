# Model: Assembly ve Kaynak Oluşturma

## Assembly.Add() Neden Çalışmıyor?

Tekla Open API'de `Assembly.Add(part)` çağrısı, commit öncesinde de sonrasında da güvenilmez davranır:
- 8 parça oluşturulsa da her biri kendi assembly'sinde kalır
- Hata vermez ama birleştirme gerçekleşmez

**Çözüm: Kaynak (Weld) oluştur → Tekla assembly'yi otomatik birleştirir.**

---

## Kaynak ile Assembly Birleştirme

Tekla'da iki part arasına weld eklendiğinde, Tekla her iki parçayı **aynı assembly'ye** taşır.
Bu yan etki, assembly birleştirmenin standart MCP yöntemidir.

### Protokol

1. Ana elemanın GUID'ini MODEL_OLUSTUR Aşama 3 GUID tablosundan al (assembly'nin main part'ı)
2. Her secondary eleman için `create_weld` çağır:

```
create_weld(
  main_part_guid      = "ana-eleman-guid",
  secondary_part_guid = "secondary-eleman-guid",
  size_above          = 6.0,   // a-ölçüsü mm (Danieli STD: 0.7 × t_min)
  size_below          = 0.0    // tek taraflı kaynak için 0
)
```

3. Tüm secondary elemanlar eklendikten sonra assembly'yi doğrula:

```
select_elements_assemblies_or_main_parts(mode="Assembly")
→ 1 assembly görünmeli (önceki 8 değil)
```

### Danieli Kaynak Standardı (STD 2.8.006)
- Sınıf: X (çevre kaynak)
- a-ölçüsü: 0.7 × t_min (en ince birleşen parçanın kalınlığının 0.7 katı)

| Birleşim | t_min | a (mm) |
|----------|-------|--------|
| HEA140 flanş (12mm) + PL15 | 12 | 8.4 → 8 |
| HEA140 gövde (7mm) + PL10 | 7 | 4.9 → 5 |
| PL15 + HEA140 gövde | 7 | 4.9 → 5 |

---

## 000-000-979-831 Kaynak Planı

Ana eleman: **SUPPORT-YATAY** (HEA140, GUID yakalanacak)

| Secondary | Kaynak a (mm) | Açıklama |
|-----------|---------------|----------|
| SUPPORT-DUSEY | 8 | köşe birleşimi |
| SAG-UC-LEVHA | 8 | flanş uç levha |
| ALT-UC-LEVHA | 8 | flanş uç levha |
| S2-STIFFENER ×4 | 5 | gövde stiffener |

```
create_weld(main="YATAY-GUID", secondary="DUSEY-GUID", size_above=8.0)
create_weld(main="YATAY-GUID", secondary="SAG-LEVHA-GUID", size_above=8.0)
create_weld(main="YATAY-GUID", secondary="ALT-LEVHA-GUID", size_above=8.0)
create_weld(main="YATAY-GUID", secondary="S2A-GUID", size_above=5.0)
create_weld(main="YATAY-GUID", secondary="S2B-GUID", size_above=5.0)
create_weld(main="YATAY-GUID", secondary="S2C-GUID", size_above=5.0)
create_weld(main="YATAY-GUID", secondary="S2D-GUID", size_above=5.0)
```

---

## Doğrulama

```
select_elements_assemblies_or_main_parts(mode="Assembly")
→ Beklenen: 1 assembly

get_elements_properties(props=["ASSEMBLY_PREFIX", "ASSEMBLY_ID", "NAME"])
→ Tüm elemanlar aynı ASSEMBLY_ID'ye sahip olmalı
```

Hâlâ 8 assembly görünüyorsa → `create_weld` çağrıları başarısız olmuştur:
- GUID'leri tekrar doğrula
- Her weld çağrısından sonra sonucu kontrol et
