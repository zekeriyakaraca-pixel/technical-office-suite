# model-uretici MEMORY

---

## Position Varsayılanları (KRİTİK)

Tekla `create_beam` varsayılan değerleri: `depth: BEHIND`, `lateral: RIGHT`.
Bu değerler gerçek yapıda ciddi konum hatası yaratır.

**Her model.json elemanında zorunlu:**
```json
"position": {
  "depth": "MIDDLE",
  "lateral": "MIDDLE",
  "tekla_rotation_enum": <rotation.json'dan al>
}
```

Kaynak: `profil-yon-analisti` → `_rotation.json` → `tekla_rotation_enum` alanı.

---

## tekla_rotation_enum Asla Boş Bırakılamaz

`tekla_rotation_enum` boş veya null → model.json YAZILMAZ → SORU-XXX aç.

Boş gelme nedenleri ve aksiyonlar:
- `_rotation.json` YOK → `profil-yon-analisti` tamamlanmamış → orchestrator'a bildir
- `tekla_rotation_enum` null → `profil-yon-analisti`'ni yeniden tetikle
- Belirsizlik var → SORU-XXX: "K1 profili için Tekla rotation değeri nedir?"

---

## Confidence Skoru Örüntüleri

| Veri Kaynağı | Tipik Confidence | Açıklama |
|-------------|-----------------|----------|
| DXF geometri (koordinat) | 0.95–1.00 | En güvenilir kaynak |
| BOM doğrulamalı | 0.90 | Tablo + görsel çapraz teyit |
| Vision Provider (net görünüş) | 0.85 | Microzoom uygulanmış |
| Metin OCR | 0.70–0.80 | Yazı tipi ve DPI'ye bağlı |
| Tahmin / interpolasyon | 0.50–0.65 | CODEX_DESTEGI veya SORU-XXX |
| İnsan onayı sonrası | 1.00 | Sabitlenir |

Global threshold: ≥ 0.75 → PASSED. Altında → insan müdahalesi.

---

## Codex Kurtarma Örüntüleri

| Hata Türü | Codex Görevi |
|-----------|-------------|
| `dxf_parser.py` DXFStructureError | ezdxf bulge hatasını handle et, yeniden parse et |
| `_geom.json` boş array | Alternatif entity katmanında geometriyi ara |
| Polygon stiffener contour_points eksik | 8-noktalı polyline'dan {x,y,z} listesi çıkar |
| ContourPlate poligon kaybolmuş (EP/BP dikdörtgen çizilmiş) | get_elements_properties ile kontur nokta sayısını kontrol et; model.json contour_points adediyle eşleşmiyorsa yeniden oluştur |
| Delik merkezi parça sınırı dışında | Delik grid'ini yeniden parse et, sınır içi koordinatları yaz |

Maksimum 1 Codex denemesi. Başarısız → SORU-XXX. Tekrar Codex çağırma.

---

## Skill Sırası — Araç Önce, İnsan Sonra

```
confidence < 0.60 veya script crash
    → CODEX_DESTEGI (1 deneme)
    → hâlâ < 0.75 → SORU-XXX
    → SORU yanıtlandı → Adım 1'e dön

confidence 0.60–0.74 (script başarılı)
    → SORU-XXX (Codex atla)
    → yanıt gelince → Adım 1'e dön
```

Codex denenmeden insana soru sormak yasaktır (güven < 0.60 veya script crash durumunda).

---

## Backward Compatibility — model.json Çift Yazma

`tekla-modelci` `outputs/model_[proje].json` okur — bu dosya adı kontrat.

Her zaman ikisini birden yaz:
1. `outputs/model_[proje].json` ← tekla-modelci kontratlı dosya
2. `outputs/YYYY-MM-DD_tekla_modeller_model_[proje].json` ← tarih damgalı arşiv

Sadece arşiv dosyası yazılırsa tekla-modelci bulamamaz.
