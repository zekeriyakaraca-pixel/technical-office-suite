# Kurallar: cizim-gorsel-analisti

## Bu Agent Şunları YAPABİLİR:
- `../../data/imports/[proje]_spatial.json` okuyabilir
- `../../data/imports/[proje]_sections.json` okuyabilir
- `../../data/imports/[proje]_page_N.png` okuyabilir (Read tool ile görsel)
- `../../data/imports/[proje].pdf` okuyabilir (microzoom için)
- `../../outputs/[proje]_parsed_status.json` okuyabilir
- `../../requirements/[proje].json` okuyabilir (profil adı için)
- `scripts/pdf_visual_analyzer.py` çalıştırabilir
- `scripts/pdf_claude_analyzer.py` çalıştırabilir (legacy wrapper)
- `scripts/rotation_analyzer.py` çalıştırabilir (sadece `--pdf` modu, S3b PNG üretimi için)
- `../../data/imports/[proje]_zoom_*.png` yazabilir (microzoom çıktıları)
- Provider geçici çıktılarını doğrulamadan final dosya olarak yazamaz
- `../../data/imports/[proje]_gorsel_analiz.json` yazabilir
- `../../outputs/[proje]_gorsel_status.json` yazabilir
- `../../journal/` okuyabilir ve yazabilir
- `../../requirements/` okuyabilir (yazamaz)
- Kendi `MEMORY.md`'sini güncelleyebilir

## Araç Önceliği Kuralı (KESİN — istisna yok)

**Microzoom zorunludur — atlanamaz.**

| Durum | Yapılacak |
|-------|-----------|
| PDF belirsiz semboller (P18, P35 vb.) | microzoom uygula (fitz.Matrix(4,4)) |
| microzoom_png_count = 0 | final analiz yazma; `_gorsel_status.json` status:error, blocker:MICROZOOM_FAILED yaz |
| Provider kullanılamıyor | provider_available: false ve confidence_penalty kaydet, S3b_visual ile devam |
| Provider JSON doğrulanamadı | status error yaz, final analiz dosyasını yazma |

## Bu Agent Şunları YAPAMAZ:
- Vision Provider çıktısını schema doğrulamadan `_gorsel_analiz.json`'a yazamaz; rotation füzyonu `profil-yon-analisti`'nin işi
- `../../data/imports/[proje]_rotation.json` üretemez
- `../../outputs/[proje]_rotation_status.json` üretemez
- `../../outputs/model_[proje].json` üretemez
- `../../knowledge/` dosyalarını düzenleyemez
- Başka agentların status.json dosyalarına yazamaz
- `microzoom_png_count = 0` iken `_gorsel_analiz.json` veya success `_gorsel_status.json` yazamaz

## Zorunlu Çıktı Kontrolü

`_gorsel_status.json` yazılmadan önce kontrol:
```
microzoom_png_count ≥ 1         ✓ (ZORUNLU)
_gorsel_analiz.json mevcut      ✓ (ZORUNLU)
vision_model alanı dolu         ✓ (`claude_vision` legacy alias olarak yazılır)
s3b_visual_rotation dolu        ✓ (ZORUNLU — microzoom görsel okumadan gelir)
```

## Devir Kuralları

### ORCHESTRATOR'a devret:
- `_gorsel_status.json` → status: "success" → orchestrator profil-yon-analisti'yi tetikler
- `_gorsel_status.json` → status: "error", blocker: "MICROZOOM_FAILED" → orchestrator insana bildirir

## Paylaşılan Dosya Kuralları
- `../../journal/` her döngüde yaz
- `_gorsel_status.json` içinde `next_agent` alanını mutlaka doldur

## Geometri Analiz Kuralları (JRN-06)
- **Levha ve Stiffener Geometrisi (JRN-06):** Levhalar sadece dikdörtgen değildir. Görseldeki poligon (çokgen) noktalarını, köşe koordinatlarını ve varsa pah (chamfer) ölçülerini `contour_points` olarak raporla.
- **Profil Uzunluk Ayrımı (JRN-11):** Profil uzunluklarını analiz ederken iki farklı değer raporlamalısın:
  1. `length_mm`: Parçanın montaj boyu (dıştan dışa, varsa uçtaki plakalar dahil).
  2. `net_length_mm`: Sadece ana profilin (kiriş/kolon) kesim boyu.
  - Eğer resimde sadece tek ölçü varsa ve parça uçlarında levha (flanş vb.) görünüyorsa, bunu `length_mm` olarak işaretle ve `net_length_mm` için "plaka kalınlığı düşülmüş" tahmini bir değer üret veya not düş.
- **Belirsizlik Durumu:** Eğer plaka şekli karmaşıksa ve noktalar net değilse, `confidence < 0.70` ata ve `SORU-XXX` açarak kullanıcıdan DXF veya detaylı ölçü iste.
 
## Microzoom Valid Paket Kuralı v3
- `_gorsel_status.json status: success` yazmadan once `microzoom_valid: true` olmalidir.
- `MICROZOOM_FAILED`: hic PNG uretilemedi.
- `MICROZOOM_INSUFFICIENT`: PNG var ama minimum valid paket veya kalite kosulu eksik.
- Eski/stale `_zoom_*.png` dosyalari manifest valid degilse kanit kabul edilmez.
