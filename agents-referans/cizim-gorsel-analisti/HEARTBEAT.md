# cizim-gorsel-analisti Heartbeat

## Zamanlama
Orchestrator tetikler — `outputs/[proje]_parsed_status.json` status: "success" olduğunda çalışır.

## Tetikleme Koşulları
```
outputs/[proje]_parsed_status.json → status: "success"   ✓
outputs/[proje]_gorsel_status.json                        YOK
```

## Blocker Koşulları (başlamadan önce kontrol et)
```
data/imports/[proje]_parsed_status.json eksik      → DUR, orchestrator'a bildir
data/imports/[proje]_page_1.png eksik              → DUR, cizim-on-islemci çalıştırılmamış
data/imports/[proje]_spatial.json eksik            → DUR, pdf_parser çıktısı yok
data/imports/[proje].pdf eksik                     → DUR, microzoom yapılamaz
```

## Her Döngü

### 1. Bağlam Oku
```
outputs/[proje]_parsed_status.json oku:
  mode (DXF / PDF / DXF+PDF_FALLBACK)
  project_type (standard / danieli)
  pdf_available (bool)
  skip_visual_agent (bool — true ise bu agent atlanır, orchestrator boş status yazar)
```

`skip_visual_agent: true` ise → bu agent çalışmaz, orchestrator minimal gorsel_status yazar.

### 2. ZORUNLU: Microzoom PNG Üret

**Bu adım atlanamaz. microzoom_png_count = 0 ise final analiz yazılmaz; status `error/MICROZOOM_FAILED` olur.**

`rotation_analyzer.py` bu adımı otomatik yapar. Çıktı bölge adı ne gösterdiğini tanımlar:

```
_zoom_kesit_AA.png        → A-A kesit görünüşü
_zoom_kesit_AA_kaynak.png → A-A kesit alma çizgisi (nereden alındığı)
_zoom_kesit_BB.png        → B-B kesit görünüşü
_zoom_izometrik.png       → 3D izometrik görünüm
_zoom_ust_gorunusu.png    → TOP VIEW / PLAN
_zoom_on_gorunusu.png     → FRONT VIEW / ön görünüş
_zoom_yukselti.png        → ELEVATION
_zoom_plaka_detay.png     → Levha/plaka detayı
_zoom_uc_ust.png          → Üst uç — kalınlık boyut notları
_zoom_uc_alt.png          → Alt uç — kalınlık boyut notları
_zoom_genel_kesit_ust.png → Fallback kesit (etiket bulunamadı)
_zoom_genel_izometrik.png → Fallback izometrik (etiket bulunamadı)
```

Manuel üretim (sadece otomatik başarısız olursa):
```python
import fitz
doc = fitz.open("data/imports/[proje].pdf")
mat = fitz.Matrix(4, 4)
for page_no, rect_coords, label in zoom_regions:
    pix = doc[page_no].get_pixmap(matrix=mat, clip=fitz.Rect(rect_coords))
    pix.save(f"data/imports/[proje]_zoom_{label}.png")
microzoom_png_count = len(zoom_regions)
```

Üretilen PNG'leri Read tool ile oku — PNG adından ne göreceğini bil:
- `kesit_*`: Profil kesit şekli — I-profil yatay → TOP, dikey → FRONT
- `uc_ust` / `uc_alt`: Kalınlık boyutları — `net_length_mm` ayrımı için kritik
- `izometrik`: 3D yön doğrulaması — bağlam okuma, rotation teyidi değil
- `plaka_detay`: Levha `contour_points` ve pah ölçüleri

### 3. Vision Provider Analizi

**Varsayılan komut:**
```
python scripts/pdf_visual_analyzer.py data/imports/[proje].pdf \
  --project [proje] \
  --provider codex_cli
```

`scripts/pdf_claude_analyzer.py` legacy wrapper olarak kalır; ana yol `pdf_visual_analyzer.py`.

**Provider hata yönetimi:**
```
Codex CLI auth/config eksik veya provider hatası → provider_available: false
  → api_available: false legacy alias kaydet
  → confidence_penalty kaydet
  → vision_model UNKNOWN
  → Microzoom görsel değerlendirmesiyle devam
  → Status: "success" (pipeline çökmez)
```

### 4. Sonuçları Birleştir → `_gorsel_analiz.json`

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
    "bom": [...],
    "section_labels": [...]
  },
  "claude_vision": "legacy alias of vision_model",
  "s3b_visual_rotation": "TOP",
  "s3b_visual_confidence": 0.82,
  "s3b_reasoning": "Kesit görünüşünde I-profil yatay konumda — TOP yönü",
  "consensus": {
    "rotation": "TOP",
    "confidence": 0.93,
    "source": "vision_model_dominant"
  }
}
```

**Birleştirme kuralı:**
- `vision_model.rotation_confidence ≥ 0.75` → consensus = vision_model (dominant)
- `< 0.75` → S3b_visual + Vision Provider ortalaması
- `provider_available: false` → consensus = S3b_visual alone (+ ceza)

### 5. `_gorsel_status.json` Yaz

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

**KRİTİK:** `microzoom_png_count = 0` ise success status yazılmaz; canonical status `error`, `blocker: "MICROZOOM_FAILED"` olur.  
Orchestrator bunu insan müdahalesi bekleyen hata olarak ele alır.

### 6. Journal'a Logla
Her döngü sonunda `../../journal/YYYY-MM-DD_HHMM.md`:
- Provider kullanıldı mı, confidence penalty var mı
- microzoom_png_count
- Vision Provider rotation + confidence
- S3b_visual sonucu

## Tırmanma Kuralları
- `[proje].pdf` bulunamazsa → DUR, orchestrator'a bildir (microzoom yapılamaz)
- Provider timeout veya malformed JSON → status error yaz, final analiz dosyasını yazma
- `microzoom_png_count = 0` → status error/MICROZOOM_FAILED yaz, final analiz dosyasını yazma
 
## v3 Microzoom Routing Notu
- Ana microzoom uretimi `core/agents/pdf_agent/microzoom.py` ile yapilir.
- `data/imports/[proje]_microzoom_manifest.json valid=true` olmadan success yazilmaz.
- `MICROZOOM_INSUFFICIENT` ve `MICROZOOM_FAILED` error status olarak insana/retry akisine gorunur.
