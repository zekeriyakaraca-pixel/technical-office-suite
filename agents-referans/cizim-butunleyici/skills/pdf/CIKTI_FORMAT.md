# PDF Analiz Çıktı Formatı

---

## Çıktı Şablonu: `outputs/YYYY-MM-DD_tekla_modeller_analiz_[proje].md`

```markdown
# PDF Analiz: [Proje Adı]
Tarih: YYYY-MM-DD
PDF: [dosya adı]
PDF Tipi: [Bina / Çelik Yapı / Fabrikasyon Detayı / Endüstriyel]

## Ön İşleme Referansları
| Dosya | Durum | Kullanıldı mı |
|-------|-------|---------------|
| `[proje]_page_1.png` | ✅ / ❌ | Görsel okuma |
| `[proje]_geom.json` | ✅ / ❌ | Boyut doğrulama |
| `[proje]_tables.json` | ✅ / ❌ | BOM çıkarma |
| `[proje]_spatial.json` | ✅ / ❌ | Aks grid / etiket konumları |
| `[proje]_gorsel_analiz.json` | ✅ / ❌ | Vision Provider BOM / rotation |
| `[proje]_rotation.json` | ✅ / ❌ | Rotation verisi |

## Aks Sistemi
| Aks | Koordinat (mm) |
|-----|----------------|
| A   | 0              |
| B   | 6000           |

## Kat Kotları
| Kat | Kot (mm) |
|-----|----------|
| Z01 | 0        |
| Z02 | 3500     |

## Eleman Listesi
| # | Tip | Ad | Profil | Malzeme | Başlangıç | Bitiş | Güven |
|---|-----|----|--------|---------|-----------|-------|-------|
| 1 | Kolon | K1 | HEA200 | S355 | A/Z01 | A/Z02 | 0.92 |
| 2 | Kiriş | G1 | IPE300 | S355 | A-B/Z02 | — | 0.88 |

## Polygon Stiffenerlar (varsa)
| Eleman | Nokta Sayısı | contour_points |
|--------|-------------|----------------|
| ST1 | 6 | [{x:0,y:0,z:0}, ...] |

## Delik Doğrulama
| Eleman | Sayı | Sınır | Aralık | Simetri | Sonuç |
|--------|------|-------|--------|---------|-------|
| K1 | ✅ | ✅ | ✅ | ✅ | PASSED |

## Çelik Bağlantılar
(skills/pdf/BAGLANTI_DETAY.md tablosu)

## Çelişki Logu (Geometri ve Tutarlılık)
1. [Çelişki varsa buraya]

## Doğrulama Tablosu
| Kontrol | Durum | Notlar |
|---------|-------|--------|
| Ölçü Toplamları | OK | ... |
| Plan-Kesit Uyumu | OK | ... |
| Malzeme Teyidi | OK | ... |
| Bağlantı Mantığı | OK | ... |

## Global Confidence
global_confidence: [0.00–1.00]

## Sorular — İnsan Yanıtı Gerekli
[SORU-001]
Eleman: [Eleman Adı]
Sorun: [Sorun tanımı]
Tahmin: [Tahmin veya "Belirsiz"]
Güven: [%]
Onaylıyor musunuz? (E/H) Veya doğru değeri girin:
```

---

## `_analiz_status.json` İçin global_confidence Hesabı

```python
confidences = [eleman.confidence for eleman in eleman_listesi]
global_confidence = sum(confidences) / len(confidences)
```
