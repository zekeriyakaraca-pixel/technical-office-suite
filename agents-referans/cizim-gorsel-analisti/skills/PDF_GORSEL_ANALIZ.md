# Skill: PDF_GORSEL_ANALIZ (v2 — cizim-gorsel-analisti için)

## Amaç
PDF sayfalarını Codex CLI Vision Provider ile analiz ederek ve zorunlu microzoom protokolüyle görsel S3b sinyallerini üretmek.

Bu skill yalnızca `cizim-gorsel-analisti` agent tarafından kullanılır. Rotation füzyonu bu skill'in sorumluluğu değildir — çıktılar `profil-yon-analisti`'ne aktarılır.

## Tetikleme Koşulları
Her çalışmada otomatik — blocker yoksa çalışır.

## Önkoşullar
- Varsayılan provider `codex_cli` olmalı; Anthropic yalnız açık `--provider anthropic_api` seçilirse kullanılır.
- Codex CLI auth/config yoksa `provider_available: false` ve legacy `api_available: false` ile devam edilir.
- `data/imports/[proje]_page_N.png` mevcut olmalı (cizim-on-islemci üretmiş olmalı)
- `data/imports/[proje].pdf` mevcut olmalı (microzoom için)

---

## Adım 1: ZORUNLU Microzoom PNG Üretimi

**Bu adım ATLANAMAZ. microzoom_png_count = 0 → final analiz yazılmaz; status error/MICROZOOM_FAILED yazılır.**

`rotation_analyzer.py --pdf` komutu `_build_smart_regions()` ile bölgeleri otomatik belirler ve PNG'leri üretir. Bölge adları ne gösterildiğini tanımlar:

| PNG Adı | İçerik |
|---------|--------|
| `_zoom_kesit_AA.png` | A-A kesit görünüşü |
| `_zoom_kesit_AA_kaynak.png` | A-A kesit alma çizgisi (nereden alındığı) |
| `_zoom_kesit_BB.png` | B-B kesit görünüşü |
| `_zoom_izometrik.png` | 3D izometrik görünüm |
| `_zoom_ust_gorunusu.png` | TOP VIEW / PLAN görünüşü |
| `_zoom_on_gorunusu.png` | FRONT VIEW / ön görünüş |
| `_zoom_yukselti.png` | ELEVATION görünüşü |
| `_zoom_plaka_detay.png` | Plaka/levha detayı (DETAIL, PL...) |
| `_zoom_uc_ust.png` | Profilin üst ucundaki boyut bölgesi |
| `_zoom_uc_alt.png` | Profilin alt ucundaki boyut bölgesi |
| `_zoom_genel_kesit_ust.png` | Fallback — kesit etiketi bulunamadı |
| `_zoom_genel_izometrik.png` | Fallback — isometrik etiket bulunamadı |

```python
# Manuel üretim gerektiğinde (rotation_analyzer çıktısındaki bölgeleri kullan):
import fitz
doc = fitz.open("data/imports/[proje].pdf")
mat = fitz.Matrix(4, 4)  # 4× büyütme zorunlu

# rotation_analyzer _rotation.json → microzoom_files listesini oku
# veya _spatial.json'dan kendim belirle
for page_no, rect_coords, label in zoom_regions:
    pix = doc[page_no].get_pixmap(matrix=mat, clip=fitz.Rect(rect_coords))
    pix.save(f"data/imports/[proje]_zoom_{label}.png")
```

Üretilen PNG'leri Read tool ile oku — PNG adına bakarak ne göreceğini bil:
- `kesit_*`: Profil kesit şekli — I-profil yatay → TOP, dikey → FRONT
- `uc_ust` / `uc_alt`: Kalınlık ve plaka boyutları — net_length_mm ayrımı için kritik
- `izometrik`: 3D yön doğrulaması — rotation teyidi değil, bağlam okuma
- `plaka_detay`: Levha contour_points ve pah ölçüleri için
→ `s3b_visual_rotation` ve `s3b_visual_confidence` doldur

## Adım 2: Vision Provider Analizi

**Varsayılan komut:**
```
python scripts/pdf_visual_analyzer.py data/imports/[proje].pdf \
  --project [proje] \
  --provider codex_cli
```

`scripts/pdf_claude_analyzer.py` yalnız legacy wrapper'dır; yeni ana yol değildir.

**Provider hata yönetimi:**
```
Codex CLI auth/config eksik veya provider hatası:
  → provider_available: false
  → api_available: false (legacy alias)
  → confidence_penalty kayıtlı
  → vision_model UNKNOWN
  → claude_vision = vision_model (legacy alias)
  → S3b_visual sinyaliyle devam (pipeline çökmez)
```

## Adım 3: Sonuçları Birleştir

```json
{
  "project": "000-000-XXX",
  "agent": "cizim-gorsel-analisti",
  "timestamp": "YYYY-MM-DD",
  "provider": "codex_cli",
  "provider_available": true,
  "api_available": true,
  "confidence_penalty": 0.0,
  "microzoom_png_count": 3,
  "vision_model": {
    "rotation": "TOP",
    "rotation_confidence": 0.88,
    "bom": [],
    "section_labels": ["TOP VIEW", "FRONT VIEW"]
  },
  "claude_vision": "legacy alias of vision_model",
  "s3b_visual_rotation": "TOP",
  "s3b_visual_confidence": 0.82,
  "s3b_reasoning": "Kesit görünüşünde I-profil yatay konumda görünüyor",
  "consensus": {
    "rotation": "TOP",
    "confidence": 0.93,
    "source": "vision_model_dominant"
  }
}
```

**Birleştirme kuralı:**
- `vision_model.rotation_confidence ≥ 0.75` → `source: "vision_model_dominant"`
- `< 0.75` → S3b_visual + Vision Provider ağırlıklı ortalama
- `provider_available: false` → `source: "s3b_visual_only"`, confidence penalty

## Adım 4: `_gorsel_status.json` Yaz

```json
{
  "project": "000-000-XXX",
  "agent": "cizim-gorsel-analisti",
  "status": "success",
  "timestamp": "YYYY-MM-DD",
  "provider": "codex_cli",
  "provider_available": true,
  "api_available": true,
  "confidence_penalty": 0.0,
  "microzoom_png_count": 3,
  "vision_confidence": 0.88,
  "claude_vision_confidence": 0.88,
  "s3b_visual_confidence": 0.82,
  "next_agent": "profil-yon-analisti"
}
```

**microzoom_png_count = 0 ise success status yazılmaz; canonical status `error`, `blocker: "MICROZOOM_FAILED"` olur.**

---

## Kurallar
- Microzoom önce gelir — provider çağrısından önce PNG üretilmeli
- Provider kullanılamazsa pipeline durmaz (provider_available false ve confidence_penalty ile devam)
- MEMORY.md → "Vision Provider Doğrulama Örüntüleri" tablosunu her projede güncelle
- `_gorsel_analiz.json` içindeki `vision_model` alanı dolu olmalı; `claude_vision` legacy alias olarak yazılır
 
## v3 Microzoom Manifest Sozlesmesi
- Gorsel analiz success status yalniz `microzoom_valid: true` ise yazilir.
- Ortak uretim yolu `core/agents/pdf_agent/microzoom.py` moduludur; `rotation_analyzer.py` sadece ayni modulu kullanan uyumluluk yoludur.
- `source_trace.microzoom_manifest` ve `microzoom_manifest_path` analiz payload'unda tutulur.
- `MICROZOOM_FAILED` hic render yok, `MICROZOOM_INSUFFICIENT` ise PNG var ama zorunlu paket eksik anlamina gelir.
