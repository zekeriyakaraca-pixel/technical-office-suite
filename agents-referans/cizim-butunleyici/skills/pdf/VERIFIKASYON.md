# PDF Analiz Doğrulama Protokolü (Sanity Checks)

Her analiz döngüsünde zorunlu çalıştırılır.

---

## 1. Geometrik Tutarlılık
```
(Ara Ölçü 1) + (Ara Ölçü 2) + ... = Toplam Ölçü?
Hata payı: ±2 mm
```
Eşit değilse → "Sorular" bölümünde hangi ölçü hatalı olabileceğini belirt.

## 2. Plan — Kesit Çapraz Kontrolü
- Plan görünüşteki kolon adedi = Kesitlerdeki kolon adedi?
- Aynı poz numarasının profili/malzemesi tüm görünüşlerde aynı mı?
- Farklılık → "Çelişki" işaretle, detay kesitini doğru kabul et, insana bildir

## 3. Cıvata / Kaynak Sayımı
- Toplam cıvata = (bağlantı başına cıvata) × (bağlantı sayısı)
- Kaynak boğazı a > 0.7 × t_min → Over-welding uyarısı

## 4. Malzeme / Standart Eşleştirmesi
- "S355" / "IPE" → EUROCODE / DIN
- "A36" / "W12x26" → AISC / ASTM
- Tekla'da karşılığı var mı? (S235JR → S235, S355J2+N → S355)

## 5. Doğrulama Durum Raporu (analiz_[proje].md sonuna ekle)

| Kontrol | Durum | Notlar |
|---------|-------|--------|
| Ölçü Toplamları | [OK / Hatalı] | ... |
| Plan-Kesit Uyumu | [OK / Eksik] | ... |
| Malzeme Teyidi | [OK / Belirsiz] | ... |
| Bağlantı Mantığı | [OK / Şüpheli] | ... |
