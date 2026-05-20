# Kurallar: insan-onay

## Bu Agent Şunları YAPABİLİR:
- `../../journal/` okuyabilir ve onay notunu yazabilir
- `../../requirements/[proje].json` güncelleyebilir (prefix cevabı Onay Kapısı 6)
- Orchestrator'a bir sonraki sub-ajanı başlatma sinyali verebilir

## Bu Agent Şunları YAPAMAZ:
- PDF okuyamaz
- Tekla MCP çağrısı yapamaz
- `../../outputs/` dosya üretemez
- Onay verilmemiş bir kapıyı geçilemez — her kapının kendi onay sinyali vardır
- `../../knowledge/` dosyalarını düzenleyemez

## Kapı Geçiş Kuralları

1. Her kapı, önceki sub-agentin çıktı dosyasının varlığını gerektirir
2. İnsandan anlamsal olarak (semantic) onay sinyali (örn: "ok", "tamam", "geç") alınmadan bir sonraki adıma geçilmez. Sinyalin birebir eşleşmesi gerekmez, niyetin onay olması yeterlidir.
3. Soru yanıtlanmadan model.json üretilemez (pdf-analisti kuralı — hatırlatmak için)
4. Prefix sorusu (Kapı 6) cevabı doğrudan `../../requirements/[proje].json`'a yazılır; agent prefix'i insan adına seçemez

## requirements.json Güncelleme Formatı (Kapı 6)

```json
{
  "part_prefixes": {
    "column": "p",
    "beam": "p",
    "plate": "p"
  },
  "assembly_prefixes": {
    "column": "K",
    "beam": "G",
    "plate": "D"
  },
  "numbering_start": {
    "parts": 1,
    "assemblies": 1
  },
  "phase_separation": true
}
```

## KPI Güncelleme Kuralı (Kapı 10 — Döngü Tamamlandı)

Kapı 10 onaylandığında (`outputs/kpi_tracker.md` güncelle):

1. Proje Geçmişi tablosuna yeni satır ekle: proje kodu, başlangıç/bitiş tarihi, döngü süresi, eleman uyumu (%), tonaj farkı (%)
2. Özet tablosundaki metrikleri güncelle
3. Öğrenmeler bölümüne dikkat çekici bulgular ekle (opsiyonel)

> insan-onay `../../outputs/` dosyasına YAZAMAZ kuralına bu tek istisnai durumdur.
> Yalnızca `kpi_tracker.md` güncellenir, başka dosya değil.

---

## Journal Onay Notu Formatı

```markdown
## İnsan Onayı — Kapı [N]
- Tarih: YYYY-MM-DD HH:MM
- Sinyal: "[model onaylandı / metraj onaylandı / vb.]"
- Sonraki adım: [tekla-modelci / tekla-raporlama / tamamlandı]
```
