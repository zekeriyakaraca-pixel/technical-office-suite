# Skill: Ogrenme ve Hafiza Yonetimi

## Purpose
Agentlerin yeni gordugu durumlari kontrollu sekilde kaydetmesini, dogrulanmis oruntuleri yerel hafizaya almasini ve kalici skill degisikliklerini mudur onayina baglamasini saglamak.

## Serves Goals
- Teknik ofis muduru icin kalite kapisi ve surec izleme.
- Tum aktif agentlar icin tekrar eden hatalardan ogrenme.

## Inputs
- `journal/entries/`
- Agentin kendi `MEMORY.md` dosyasi
- `outputs/jobs/<job_id>/job_summary.json`
- `outputs/jobs/<job_id>/<poz_no>/<poz_no>_qc.json`
- Haftalik veya is kapanis mudur degerlendirmesi

## Process
1. Yeni veya beklenmeyen bir durum goruldugunde once `journal/entries/` altina olay kaydi yaz.
2. Kayitta is no, poz no, kaynak dosya, karar, sonuc ve ilgili cikti yollarini belirt.
3. Ayni oruntu en az iki dogrulanmis iste tekrar ederse agent kendi `MEMORY.md` dosyasina kisa bir ders ekler.
4. Paylasimli skill dosyalarina dogrudan yazma; skill degisikligi onerisi mudure journal kaydi olarak verilir.
5. Mudur haftalik review veya is kapanisinda QC sonuclarini kontrol eder ve uygun gorurse paylasimli skill'i gunceller.
6. Yanlis veya supheli ogrenme tespit edilirse journal'da iptal karari yazilir ve MEMORY girdisi duzeltilir.
7. PDF/OCR/vision adaylari icin mudur onayi ve QC `ok=true` olmadan kalici ogrenme yazma.
8. Ayni PDF/layout oruntusu en az iki onayli iste tekrar ederse `journal/skill_proposals/` altinda skill guncelleme onerisi ac.

## Outputs
- `journal/entries/YYYY-MM-DD_HHMM.md` olay veya ders kaydi
- Agentin kendi `MEMORY.md` dosyasinda dogrulanmis yerel ogrenme
- Mudur onayindan sonra guncellenmis `agents/_shared/skills/*.md`
- `journal/skill_proposals/*.md` tekrar eden, QC kanitli PDF/layout oruntuleri icin oneriler

## Quality Bar
- Tekil basarisiz deneme kalici skill'e donusmez.
- Dusuk guvenli PDF davranisi "tahmin" olarak ogrenilmez; manuel inceleme kuralina bagli kalir.
- Her kalici ogrenmenin en az bir QC veya teslim kaniti vardir.

## Integration
- `SUREC_IZLEME.md` is kapanis verisini saglar.
- `CIZIM_NC_KALITE_KONTROLU.md` dogrulanmis teknik sonucu saglar.
- `DOKUMAN_FORMATLAMA.md` teslim ve eksik dokuman sinyallerini saglar.
