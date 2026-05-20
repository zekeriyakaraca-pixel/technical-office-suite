# Skill: MIKRO_ZOOM_PROTOKOLU (v2 — PyMuPDF Otonom Kırpma)

## Amaç
PDF sayfalarından yüksek çözünürlüklü bölge kırpmaları üretmek. Belirsiz semboller, karmaşık kesit detayları ve rotation için kritik görünüşleri netleştirmek.

**Bu protokol `cizim-gorsel-analisti` tarafından ZORUNLU çalıştırılır — SORU-XXX'ten önce ve API çağrısından önce.**

Eski protokol (insandan ekran görüntüsü isteme) artık geçersizdir. Otonom kırpma zorunludur.

---

## Uygulama — Otomatik (Tercih Edilen)

`rotation_analyzer.py --pdf` komutu `_build_smart_regions()` ile `_spatial.json`'dan bölgeleri otomatik belirler. Manuel müdahale gerekmez.

```bash
python scripts/rotation_analyzer.py --project [proje_id]
# → data/imports/[proje]_zoom_kesit_AA.png
# → data/imports/[proje]_zoom_kesit_BB.png
# → data/imports/[proje]_zoom_izometrik.png
# → data/imports/[proje]_zoom_uc_ust.png
# → data/imports/[proje]_zoom_uc_alt.png
# → ... (en fazla 8 PNG)
```

## Uygulama — Manuel (Otomatik başarısız olursa)

```python
import fitz  # PyMuPDF — pip install pymupdf

doc = fitz.open("data/imports/[proje].pdf")
mat = fitz.Matrix(4, 4)  # 4× zorunlu (daha az → yetersiz çözünürlük)

# _spatial.json'dan koordinatları al veya _page_1.png'yi okuyarak belirle
# PNG isimlendirme: ne gösterdiğini tanımlamalı
zoom_regions = [
    (0, [100, 200, 540, 650], "kesit_AA"),        # A-A kesit görünüşü
    (0, [100, 650, 540, 900], "kesit_AA_kaynak"), # A-A kesit alma çizgisi
    (0, [540, 200, 900, 700], "izometrik"),        # İzometrik görünüm
    (0, [0,   0,   900, 120], "uc_ust"),           # Üst uç boyut notları
    (0, [0,   760, 900, 842], "uc_alt"),           # Alt uç boyut notları
]

for page_no, rect_coords, label in zoom_regions:
    pix = doc[page_no].get_pixmap(matrix=mat, clip=fitz.Rect(rect_coords))
    out_path = f"data/imports/[proje]_zoom_{label}.png"
    pix.save(out_path)
    print(f"PNG üretildi: {out_path}")

doc.close()
```

---

## Bölge Belirleme Rehberi

```
rotation_analyzer çıktısı _rotation.json → microzoom_files listesini incele.
Otomatik bölgeler yeterli değilse _page_1.png'yi Read tool ile oku:

  Öncelik sırası:
    P1 — Kesit etiketleri (A-A, B-B): kesit görünüşü + kesit alma noktası
         → iki ayrı PNG: kesit_AA + kesit_AA_kaynak
    P2 — Görünüş etiketleri: izometrik, ust_gorunusu, on_gorunusu, yukselti
    P3 — Plaka detayları: plaka_detay (DETAIL, DETAY, PL250x12 gibi)
    P4 — Uç bölgeler: uc_ust, uc_alt (kalınlık boyutları burada)
```

**Minimum zoom bölgesi sayısı:** 2 (1 kesit + 1 uç bölge)  
**Önerilen:** 5–8 (kesitler + izometrik + uç bölgeler + plaka detayı)

---

## PNG Okuma ve S3b_visual Doldurma

```
Read tool ile her PNG'yi oku:

S3b_visual_rotation belirleme kuralları:
  - "TOP VIEW" / "PLAN" / "ÜSTTEN GÖRÜNÜŞ" metni → TOP (confidence: 0.82)
  - "FRONT VIEW" / "ELEVATION" / "ÖN GÖRÜNÜŞ" metni → FRONT (confidence: 0.82)
  - I-profil kesitinde gövde YATAY → TOP (confidence: 0.75)
  - I-profil kesitinde gövde DİKEY → FRONT (confidence: 0.75)
  - L-profil: hangi bacak görünüyor? → asymmetric_flag tetikle

Belirsizse:
  → s3b_visual_confidence: 0.55 (düşük güven, füzyon diğer sinyalleri kullanır)
  → s3b_reasoning: "Görünüş belirsiz — kesit veya detay görünüşü yok"
```

---

## Bilinen Sembol Çözümleri (MEMORY'den)

| PDF Parser Çıktısı | Gerçek Anlam | Çözüm Yöntemi |
|--------------------|-------------|---------------|
| `P18` | `Ø18` (delik çapı 18mm) | 4× zoom → sembol netleşir |
| `P35` | `Ø35` (ankraj deliği 35mm) | 4× zoom |
| `12.5▽` | Ra 12.5 μm (yüzey pürüzlülüğü) | zoom + bağlam okuma |
| `(3885)` | Referans boyut (net gövde) | parantez → referans kural |
| `15 (3885) 15` | Plaka kalınlıkları + gövde boyu | Danieli boyut zinciri |

---

## Hata Durumları

| Durum | Yapılacak |
|-------|-----------|
| `fitz` import hatası | `pip install pymupdf` çalıştır |
| PDF şifreli / kilitli | DUR, orchestrator'a bildir |
| Koordinatlar hatalı (boş PNG) | Sayfa boyutunu kontrol et: `page.rect` |
| microzoom_png_count = 0 | `_gorsel_analiz.json` yazma; `_gorsel_status.json` error/MICROZOOM_FAILED yaz |

---

## Kurallar
- Eski protokol (insandan görüntü isteme) geçersizdir — otonom kırpma zorunlu
- `fitz.Matrix(4, 4)` sabittir — daha düşük büyütme kullanılamaz
- Her kesit görünüşü ayrı PNG olmalı (tek genel kırpma yetersiz)
- microzoom_png_count = 0 → pipeline blocker
 
## v3 Valid Paket Kurali
- Ana uretim yolu `core/agents/pdf_agent/microzoom.py` ortak moduludur; `pdf_visual_analyzer.py`, `vision_analyzer.py` ve `rotation_analyzer.py` ayni manifest sozlesmesini kullanir.
- `data/imports/[proje]_microzoom_manifest.json` zorunludur. `microzoom_valid: true` ancak en az 2 gecerli PNG, 1 `section`/`fallback_section` ve 1 `end_detail`/`plate_detail` varsa yazilir.
- Stale/eski `_zoom_*.png` dosyalari manifest valid degilse kullanilmaz; once yeniden render denenir.
- PNG var ama zorunlu kategori veya kalite eksikse `MICROZOOM_INSUFFICIENT`; hic PNG uretilemezse `MICROZOOM_FAILED` yazilir.
