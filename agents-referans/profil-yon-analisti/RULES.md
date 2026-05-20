# Kurallar: profil-yon-analisti

## Bu Agent Şunları YAPABİLİR:
- `../../data/imports/[proje]_gorsel_analiz.json` okuyabilir (cizim-gorsel-analisti çıktısı)
- `../../data/imports/[proje]_sections.json` okuyabilir
- `../../data/imports/[proje]_spatial.json` okuyabilir
- `../../data/imports/[proje].dxf` okuyabilir
- `../../scripts/rotation_analyzer.py` çalıştırabilir (DXF modu için)
- `../../data/imports/[proje]_rotation.json` yazabilir
- `../../outputs/[proje]_rotation_status.json` yazabilir
- `../../journal/` okuyabilir ve yazabilir
- `../../requirements/` okuyabilir (yazamaz)
- Kendi `MEMORY.md`'sini güncelleyebilir
- **`codex:rescue` skill'ini çağırabilir** — confidence_gate: FAILED durumunda, SORU-XXX üretmeden önce (maks. 1 deneme)

## Araç Önceliği Kuralı (KESİN — istisna yok)

**Belirsiz rotation için önce tüm sinyalleri dene, sonra insana sor.**

| Durum | Yapılacak |
|-------|-----------|
| Tüm sinyaller < 0.75 | `CODEX_DESTEGI` çalıştır (1 deneme) |
| Codex başarısız | SORU-XXX ile insana sun |
| L-profil delik bacağı belirsiz | Güven ne olursa SORU-XXX aç (MEMORY.md kuralı) |
| Script crash (rotation_analyzer.py) | `CODEX_DESTEGI` → başarısızsa SORU-XXX |

## Bu Agent Şunları YAPAMAZ:
- Vision Provider'ı çağıramaz — bu `cizim-gorsel-analisti`'nin sorumluluğu
- `../../data/imports/[proje]_gorsel_analiz.json` dosyasını YAZAMAZ — sadece okur
- `../../outputs/model_[proje].json` üretemez — bu `model-uretici`'nin işi
- `../../knowledge/` dosyalarını düzenleyemez
- Başka agentların status.json dosyalarına yazamaz
- `tekla_rotation_enum` alanını boş bırakamaz — her zaman bir değer (veya SORU-XXX)

## Waterfall Sırası (KESİN — değiştirilemez)
```
S1 Profil kuralı → S2 PDF etiket → S2b Spatial → S3b Vision Provider → S3 DXF geometri
→ S5 Çift simetri → Füzyon → Karar
```

Vision Provider ≥ 0.75 ise → KESİNLEŞ, diğerleri atla.

## L-Profil Zorunlu Kuralı
`güven(delik_bacağı) < 0.90` ise → **güven değeri ne olursa SORU-XXX aç.**
Bu kuralın istisnası yoktur. (Kaynak: MEMORY.md → L/Asimetrik Profiller)

## Devir Kuralları

### İNSANA devret (SORU-XXX formatı):
```
[SORU-XXX]
Eleman: [Profil ID]
Sorun: [Rotation belirlenemiyor / L-profil delik bacağı belirsiz]
Tahmin: [TOP / FRONT — ve nedeni]
Güven: [% Skor]
Lütfen onaylayın veya doğru yönü belirtin:
```

### ORCHESTRATOR'a devret:
- `_rotation_status.json` → status: "success", blocker: null → orchestrator cizim-butunleyici'yi tetikler
- `_rotation_status.json` → status: "blocked", blocker: "L_PROFIL_ROTATION_REVIEW" → orchestrator insan-onay'ı tetikler

## Paylaşılan Dosya Kuralları
- `../../journal/` her döngüde yaz
- `_rotation_status.json` içinde `next_agent` alanını mutlaka doldur
- Codex kurtarma raporu → `../../outputs/YYYY-MM-DD_tekla_modeller_codex_kurtarma_[proje]_rotation.md`
