# Kurallar: cizim-on-islemci

## Bu Agent Şunları YAPABİLİR:
- `../../data/imports/[proje].dxf` okuyabilir
- `../../data/imports/[proje].pdf` okuyabilir
- `../../requirements/[proje].json` okuyabilir ve yazabilir (taslak üretebilir)
- `scripts/dxf_parser.py` çalıştırabilir
- `scripts/pdf_parser.py` çalıştırabilir
- `../../data/imports/[proje]_geom.json` yazabilir
- `../../data/imports/[proje]_spatial.json` yazabilir
- `../../data/imports/[proje]_tables.json` yazabilir
- `../../data/imports/[proje]_sections.json` yazabilir
- `../../data/imports/[proje]_page_N.png` yazabilir
- `../../outputs/[proje]_parsed_status.json` yazabilir
- `../../journal/` okuyabilir ve yazabilir
- **`codex:rescue` skill'ini çağırabilir** — script crash durumunda (maks. 1 deneme)
- Kendi `MEMORY.md`'sini güncelleyebilir

## Bu Agent Şunları YAPAMAZ:
- Vision Provider'ı çağıramaz — bu `cizim-gorsel-analisti`'nin sorumluluğu
- `../../data/imports/[proje]_gorsel_analiz.json` yazamaz
- `../../data/imports/[proje]_rotation.json` yazamaz
- `../../outputs/model_[proje].json` üretemez
- `../../knowledge/` dosyalarını düzenleyemez
- Başka agentların status.json dosyalarına yazamaz

## Araç Önceliği Kuralı

| Durum | Yapılacak |
|-------|-----------|
| Script crash (dxf_parser.py / pdf_parser.py) | CODEX_DESTEGI çalıştır (1 deneme) |
| Codex başarısız | SORU-XXX ile insana bildir |
| Versiyon çakışması (Ver001/Ver002) | SORU-001 aç, DUR |
| requirements eksik | Taslak üret, SORU-001 aç |

## Zorunlu Kontroller

`_parsed_status.json` yazılmadan önce:
```
mode alanı dolu      ✓ (DXF / PDF / DXF+PDF_FALLBACK)
project_type dolu    ✓ (standard / danieli)
dxf_text_count dolu  ✓ (DXF yoksa 0)
pdf_page_count dolu  ✓ (PDF yoksa 0)
```

## Paylaşılan Dosya Kuralları
- `../../journal/` her döngüde yaz
- `_parsed_status.json` içinde `next_agent` alanını mutlaka doldur
- Script crash raporu → `../../outputs/YYYY-MM-DD_tekla_modeller_codex_kurtarma_[proje]_parser.md`
