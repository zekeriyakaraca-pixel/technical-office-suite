# cizim-on-islemci Heartbeat

## Zamanlama
Orchestrator tetikler — `data/imports/[proje].dxf` veya `data/imports/[proje].pdf` mevcut, `outputs/[proje]_parsed_status.json` YOK.

## Tetikleme Koşulları
```
data/imports/[proje].dxf VEYA data/imports/[proje].pdf mevcut   ✓
outputs/[proje]_parsed_status.json                               YOK
```

## Blocker Koşulları (başlamadan önce kontrol et)
```
Ne DXF ne PDF var → DUR, orchestrator'a bildir
İkisi de bozuk (okunmuyor) → DUR, insana bildir
```

## Her Döngü

### 1. Dosya Tespiti
```
data/imports/ klasörünü tara:
  [proje].dxf var mı?  → dxf_available: true
  [proje].pdf var mı?  → pdf_available: true
  
  İkisi de yoksa → DUR
```

### 2. DXF Parser (dxf_available: true ise)
```
python scripts/dxf_parser.py data/imports/[proje].dxf

Çıktılar:
  → data/imports/[proje]_geom.json (koordinatlar, entity sayıları)
  → dxf_text_count: TEXT/MTEXT entity sayısını kaydet

dxf_text_count = 0 ise:
  → mode: "DXF+PDF_FALLBACK"
  → PDF parser zorunlu (Adım 3'e devam)

dxf_text_count > 0 ise:
  → mode: "DXF" (PDF yoksa) veya "DXF" (PDF ek bilgi)
```

### 3. PDF Parser (pdf_available: true VEYA mode = "DXF+PDF_FALLBACK" ise)
```
python scripts/pdf_parser.py data/imports/[proje].pdf --dpi 150

Çıktılar:
  → data/imports/[proje]_spatial.json   (metin blokları + koordinatlar)
  → data/imports/[proje]_tables.json    (BOM tablosu)
  → data/imports/[proje]_sections.json  (section_labels)
  → data/imports/[proje]_page_1.png ... [proje]_page_N.png

pdf_page_count: kaç sayfa üretildiğini kaydet
```

### 4. Mode Belirleme
```
dxf_available: true, dxf_text_count > 0, pdf_available: false  → mode: "DXF"
dxf_available: false, pdf_available: true                       → mode: "PDF"
dxf_available: true, dxf_text_count > 0, pdf_available: true   → mode: "DXF" (PDF ek bilgi)
dxf_available: true, dxf_text_count = 0, pdf_available: true   → mode: "DXF+PDF_FALLBACK"
dxf_available: true, dxf_text_count = 0, pdf_available: false  → DUR, PDF zorunlu
```

### 5. Danieli / Fabrikatör Tespiti
```
Şunlardan herhangi birinde "danieli" / "SMS" / "Primetals" varsa:
  → [proje]_tables.json BOM içeriği
  → [proje]_spatial.json metin blokları
  → requirements/[proje].json proje_adi alanı
  
  → project_type: "danieli"
  
Aksi hâlde → project_type: "standard"
```

### 6. Versiyon Çakışması Kontrolü
```
[proje]_tables.json veya [proje]_spatial.json içinde "Ver001" + "Ver002" varsa:
  → SORU-001: "Proje [ID]'de birden fazla revizyon var (Ver001/Ver002). 
               Hangi revizyon modellenecek?"
  → DUR: Yanıt bekle
```

### 7. requirements/[proje].json Kontrolü
```
requirements/[proje].json var mı?
  Var → Oku, profil adı dolu mu kontrol et
  Yok → Taslak üret:
    {
      "proje_kodu": "[proje]",
      "profil": "[parser'dan okunan profil adı veya ?]",
      "malzeme": "S275",
      "tolerans": "ISO 2768-m"
    }
    → SORU-001: "requirements/[proje].json taslak üretildi — profil adını onaylayın: [taslak_profil]"
    → DUR: Yanıt bekle
```

### 8. `_parsed_status.json` Yaz
```json
{
  "project": "000-000-XXX",
  "agent": "cizim-on-islemci",
  "status": "success",
  "timestamp": "YYYY-MM-DD",
  "mode": "DXF | PDF | DXF+PDF_FALLBACK",
  "project_type": "standard | danieli",
  "dxf_available": true,
  "dxf_text_count": 47,
  "pdf_available": true,
  "pdf_page_count": 3,
  "skip_visual_agent": false,
  "soru_required": false,
  "soru_reason": null,
  "next_agent": "cizim-gorsel-analisti"
}
```

**skip_visual_agent: true durumu:**
```
PDF mevcut değil VE DXF TEXT/MTEXT > 50 (zengin metin):
  → skip_visual_agent: true
  → Orchestrator boş gorsel_status yazar, cizim-gorsel-analisti atlanır
  → next_agent: "cizim-gorsel-analisti" (orchestrator skip logic uygular)
```

### 9. Journal'a Logla
```
journal/YYYY-MM-DD_HHMM.md:
  - mode, project_type
  - dxf_text_count
  - pdf_page_count
  - SORU açıldıysa nedeni
```

## Tırmanma Kuralları
- `dxf_parser.py` crash → CODEX_DESTEGI çağır → başarısızsa insana bildir
- `pdf_parser.py` crash → CODEX_DESTEGI çağır → başarısızsa insana bildir
- Her ikisi de crash → DUR, insana bildir
