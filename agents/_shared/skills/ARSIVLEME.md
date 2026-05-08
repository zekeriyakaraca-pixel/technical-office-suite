# Skill: Arsivleme

## Purpose
Tamamlanan teknik ofis ciktilarini proje bazli arsivlemek.

## Serves Goals
- Dokuman kontrol ajani icin duzen ve izlenebilirlik.

## Inputs
- `outputs/jobs/<job_id>/`
- Teslim kontrol notu

## Process
1. Sadece QC `ok=true` olan pozlari teslim paketine dahil et.
2. Manuel inceleme bekleyen dosyalari ayri listele.
3. Dosya isimlerini poz numarasina gore koru.

## Outputs
- Proje teslim paketi veya arsiv notu

## Quality Bar
- Orijinal DXF/NC1 icerigi degistirilmez.
- Arsivde is, poz ve tarih izlenebilir olur.

## Tools
- Dosya sistemi

## Integration
- `DOKUMAN_FORMATLAMA.md` sonrasinda calisir.
