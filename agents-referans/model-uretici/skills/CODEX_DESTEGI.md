# Skill: CODEX_DESTEGI

## Amaç

Script hatası, parse başarısızlığı veya düşük güven durumlarında `codex:rescue` plugin'ini devreye alarak insana soru sormadan önce otomatik kurtarma denemek.

## Ne Zaman Devreye Girer

**Şu koşullardan birinde** — SORU-XXX üretilmeden ÖNCE:

| Tetikleyici | Koşul |
|------------|-------|
| Script crash | `dxf_parser.py` veya `pdf_parser.py` exception döndürdü |
| Parse başarısız | `_geom.json`, `_spatial.json` veya `_tables.json` boş çıktı |
| Güven < 0.60 | Kritik veri noktası (profil, uzunluk, delik) %60 altında |
| Polygon karmaşıklığı | DXF polyline >4 nokta, `contour_points` çıkarılamadı |
| Delik doğrulama hatası | 4-kontrol doğrulama geçilemedi |

**Devreye GİRMEZ:**
- Güven 0.60–0.79 arası, script başarılı → doğrudan SORU-XXX yeterli
- İnsan SORU-XXX yanıtladıysa → Codex'e gerek yok
- model.json zaten üretilmişse → Codex çağrısı yapma

---

## Aşama 1: Bağlam Hazırla

```
[CODEX KURTARMA GÖREVİ]
Proje: [proje-kodu]
Hata türü: [script crash / parse hatası / düşük güven / polygon / delik]
Çalışma dizini: [PROJECT_ROOT]

Sorun:
[Hatanın tam açıklaması — exception mesajı, boş çıktı nerede, hangi eleman etkilendi]

Beklenen çıktı:
[Ne üretilmesi gerekiyor: _geom.json, contour_points, delik koordinatları vb.]

İlgili dosyalar:
- data/imports/[dosya-adi]
- scripts/[ilgili-script.py]
- requirements/[proje].json (varsa)

Kısıtlamalar:
- Koordinatlar milimetre cinsinden
- model.json formatı: agents/model-uretici/skills/CONFIDENCE_GATE.md Aşama 6
- Yazılacak dosyalar: outputs/ veya data/imports/ klasörüne

Görev:
[Tek cümle]
```

---

## Aşama 2: Codex'i Çalıştır

`codex:rescue` skill'ini yukarıdaki prompt ile çağır.

**Maksimum 1 deneme.** Başarısız olursa tekrar çağırma — SORU-XXX'e geç.

---

## Aşama 3: Sonucu Değerlendir

**Başarı kriterleri (tümü sağlanmalı):**
1. Beklenen çıktı dosyası oluştu
2. Üretilen JSON beklenen format ve alan adlarına uyuyor
3. Güven skoru 0.60'ın üstüne çıktı (veya script hatası düzeldi)

**Başarısızlık (birisi yeterliyse):**
- Codex exception döndürdü
- Çıktı dosyası oluşmadı
- Çıktıdaki güven hâlâ < 0.60
- Format/permission hatası

---

## Aşama 4: Kurtarma Raporunu Kaydet

`outputs/YYYY-MM-DD_tekla_modeller_codex_kurtarma_[proje].md`:

```
Tetikleyici: [ne tetikledi]
Codex prompt özeti: [gönderilen görev 1 cümle]
Sonuç: BAŞARILI / BAŞARISIZ
Üretilen dosya: [dosya yolu veya "üretilmedi"]
Güven değişimi: %XX → %YY (veya "değişmedi")
```

---

## Başarılı → Devam

```
Codex başarılı
  → Üretilen dosyayı analiz girdisi olarak kullan
  → CONFIDENCE_GATE Aşama 1'e dön (yeniden hesapla)
  → Güven ≥ 0.75 → model.json üret
  → SORU-XXX üretme
```

## Başarısız → Fallback

```
Codex başarısız
  → Kurtarma raporuna "BAŞARISIZ" yaz
  → CONFIDENCE_GATE Aşama 3.1'e geç (SORU-XXX akışı)
  → Journal'a logla: "Codex denendi, başarısız, insana soruldu"
```

---

## Örnek Senaryolar

**Senaryo 1 — dxf_parser.py crash:**
```
Hata: ezdxf.DXFStructureError: POLYLINE with invalid bulge value
Codex görevi: "ezdxf bulge değerini handle edecek şekilde dxf_parser.py düzelt ve [proje].dxf yeniden parse et"
```

**Senaryo 2 — Polygon Stiffener:**
```
Hata: polyline 8 nokta, contour_points üretilmedi
Codex görevi: "DXF'teki 8-noktalı polyline'dan contour_points listesini {x,y,z} formatında çıkar"
```

**Senaryo 3 — Delik Doğrulama:**
```
Hata: Delik merkezi parça sınırı dışında
Codex görevi: "PDF delik grid'ini yeniden parse et, parça sınırlarına göre doğrula, outputs/delik_duzeltme_[proje].json yaz"
```
