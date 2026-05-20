# cizim-butunleyici Heartbeat

## Zamanlama
Orchestrator tetikler — `outputs/[proje]_rotation_status.json` status: "success", blocker: null olduğunda çalışır.

## Tetikleme Koşulları
```
outputs/[proje]_rotation_status.json → status: "success", blocker: null   ✓
outputs/[proje]_analiz_status.json                                         YOK
```

## Blocker Koşulları (başlamadan önce kontrol et)
```
outputs/[proje]_rotation_status.json eksik veya blocker var  → DUR, orchestrator'a bildir
data/imports/[proje]_rotation.json eksik                    → DUR
data/imports/[proje]_gorsel_analiz.json eksik               → DUR
requirements/[proje].json eksik                             → DUR, cizim-on-islemci tamamlanmamış
```

## Her Döngü

### 1. Bağlam Oku
```
outputs/[proje]_parsed_status.json → mode, project_type
data/imports/[proje]_rotation.json → detected_rotation, confidence
requirements/[proje].json → prefix kuralları, malzeme
```

### 2. Danieli Özel Kurallar (project_type = "danieli" ise)
```
skills/pdf/DANIELI_KURALLAR.md yükle:
  - Kaynak boğazı = 0.7 × t_min (Danieli STD 2.8.006)
  - Tek assembly yaklaşımı
  - Tüm parçalar bir çatı altında
```

### 3. BOM Çapraz Kontrolü
```
data/imports/[proje]_tables.json (parser BOM)
data/imports/[proje]_gorsel_analiz.json → vision_bom (fallback: claude_bom)

İkisi eşleşiyor → güven yüksek
Çakışma var → SORU-XXX: "Parser BOM: [...], Vision BOM: [...] — hangisi doğru?"
```

### 4. Eleman Listesi Çıkar
```
skills/pdf/GENEL_KURALLAR.md uygula:
  - Her eleman için: tip, ad, profil, malzeme, başlangıç, bitiş
  - Stiffener adedi: yan görünüş × kesit yüz sayısı
  - TYP. notasyonu → tüm benzer noktalara kopyala
  - Revizyon bulutları → öncelikli bölge olarak işaretle
```

### 5. Bağlantı Detayları (varsa)
```
skills/pdf/BAGLANTI_DETAY.md uygula:
  - Kaynak sembolleri + boyutları
  - Cıvata grubu detayları (çap, adet, düzeni)
  - Profil-plaka bağlantı geometrisi
```

### 6. Kaynak Sembolleri
```
skills/pdf/KAYNAK_ANALIZI.md uygula:
  - AWS/ISO kaynak sembol oku
  - Boğaz kalınlığı, uzunluk, konum
```

### 7. Polygon Stiffener Tespiti (KRİTİK)
```
data/imports/[proje]_geom.json → polyline entity'leri incele:

  Her polyline için nokta sayısını say:
    4 nokta → dikdörtgen kabul et (width × height yeterli)
    ≥ 5 nokta → POLYGON — köşe kesimi veya coping var
      → contour_points: [{x:_, y:_, z:_}, ...] sıralı çıkar
      → Karmaşık geometri → CODEX_DESTEGI çağır (1 deneme)

HATA: sadece width/height yazmak — contour_points zorunlu (MEMORY.md kuralı)
```

### 8. Güven Skoru Ata (Her Eleman)
```
DXF koordinatından → confidence: 1.00
BOM doğrulamalı → confidence: 0.90
Görsel + metin → confidence: 0.85
Metin only → confidence: 0.70–0.80
Tahmini (profil kuralı) → confidence: 0.55–0.70
```

### 9. Delik Doğrulama — 4 Zorunlu Kontrol
```
Her eleman için:

1. SAYI KONTROLÜ:
   PDF/DXF delik adedi = model.json delik adedi?
   → Hayırsa SORU-XXX

2. SINIR KONTROLÜ:
   Tüm delik merkezleri parça sınırları içinde mi?
   Merkez + yarıçap, parça kenarına en az 1×çap uzakta mı?
   → Hayırsa SORU-XXX

3. ARALIK KONTROLÜ:
   Delikler arası mesafe ≥ 2 × nominal çap mı?
   → Hayırsa SORU-XXX

4. SİMETRİ KONTROLÜ:
   PDF simetrik düzen gösteriyorsa koordinatlar simetrik mi?
   → Hayırsa SORU-XXX

4 kontrol geçildiyse → delik_dogrulama: "PASSED"
Herhangi biri başarısızsa → SORU-XXX aç, modele yazmadan dur
```

### 10. Üretilebilirlik Kontrolü
```
skills/pdf/URETILEBILIRLIK.md uygula:
  - Minimum levha kalınlığı standartlara uygun mu?
  - Delik kenar mesafeleri standart içinde mi?
  - Kaynak erişimi var mı?
```

### 11. Geometrik Doğrulama
```
skills/pdf/VERIFIKASYON.md uygula:
  - Ölçü toplamları tutarlı mı?
  - Plan-kesit çapraz kontrol
  - Toplam ağırlık makul mi?
```

### 12. `analiz_[proje].md` Üret
```
skills/pdf/CIKTI_FORMAT.md şablonunu kullan:
  - Aks sistemi + kat kotları
  - Eleman listesi (her eleman: tip, ad, profil, malzeme, koordinatlar, güven)
  - Çelik bağlantılar
  - Çelişki logu
  - Doğrulama tablosu
  - Açık SORU-XXX listesi

global_confidence = tüm eleman confidence değerleri ortalaması
```

### 13. `_analiz_status.json` Yaz
```json
{
  "project": "000-000-XXX",
  "agent": "cizim-butunleyici",
  "status": "success | blocked",
  "timestamp": "YYYY-MM-DD",
  "global_confidence": 0.88,
  "element_count": 12,
  "soru_count": 0,
  "soru_required": false,
  "soru_reason": null,
  "blocker": null,
  "next_agent": "model-uretici"
}
```

**SORU-XXX varsa:**
```json
{
  "status": "blocked",
  "soru_count": 2,
  "soru_required": true,
  "soru_reason": "Delik koordinatı sınır dışı (K1), BOM çakışması (P2)",
  "blocker": "SORU_REQUIRED",
  "next_agent": "insan-onay"
}
```

### 14. Journal'a Logla
```
journal/YYYY-MM-DD_HHMM.md:
  - Eleman sayısı, global_confidence
  - Polygon stiffener tespiti var mı
  - Delik doğrulama sonucu
  - Açılan SORU-XXX sayısı
```

## Tırmanma Kuralları
- Polygon hesabı başarısız → CODEX_DESTEGI çağır (1 deneme) → başarısızsa SORU-XXX
- Tüm sinyaller çakışıyorsa → SORU-XXX aç
- CODEX_DESTEGI denenmeden insana soru sormak yasak

---

## 2026-04-27 Payload Ciktisi

Status `success` olmadan once su ek dosya uretilir:

`outputs/YYYY-MM-DD_tekla_modeller_analiz_payload_[proje].json`

Minimum alanlar:

```json
{
  "project": "[proje]",
  "required_elements": [],
  "columns": [],
  "beams": [],
  "plates": [],
  "holes": [],
  "welds": [],
  "field_sources": {}
}
```

`field_sources` anahtarlari model kontratindaki hedef alanlarla ayni isimlendirilir: `plates.BP-1.thickness_mm`, `plates.BP-1.holes.positions`, `columns.COL-1.net_length_mm`. Bu sayede model-uretici `source_trace.fields` alanini birebir doldurur.
