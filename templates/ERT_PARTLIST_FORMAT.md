# ERT Partlist Format Standardı

## Excel Dosya Yapısı

- **Dosya adı:** `<safe_project_name>_partlist.xlsx`
- **Çıktı yolu:** `outputs/jobs/<job_id>/<safe_project_name>_partlist.xlsx`
- **Sheet adı:** `Part_List_holes`
- **Başlık satırı:** Satır 1 — kolon adları (aşağıya bakın)
- **Veri başlangıcı:** Satır 2

## Kolon Sırası ve Eşleme

| Kolon | Başlık | Kaynak | Tip |
|-------|--------|--------|-----|
| A | POZ NO | `plate_spec.poz_no` | metin |
| B | CİNSİ | `PL<thickness_mm>` | metin |
| C | GENİŞLİK | `plate_spec.height` (kısa ölçü) | sayı (mm) |
| D | UZUNLUK | `plate_spec.width` (uzun ölçü) | sayı (mm) |
| E | ADET | `plate_spec.quantity` | tam sayı |
| F | KALİTE | `plate_spec.material` | metin |
| G | B.ALAN | `plate_spec.unit_surface_area_m2` | sayı (m², 4 ondalık) |
| H | B.AĞIRLIK | `plate_spec.unit_weight_kg` | sayı (kg, 3 ondalık) |
| I | T.ALAN | Excel formülü: `=+G{row}*E{row}` | formül |
| J | T.AĞIRLIK | Excel formülü: `=+H{row}*E{row}` | formül |
| K | AÇIKLAMA | Delik varsa `Delikli`, yoksa `Deliksiz` | metin |

## Kurallar

- QC `ok=true` olmayan poz Excel'e dahil edilmez.
- `unit_surface_area_m2` veya `unit_weight_kg` eksikse satır eklenmez; `partlist_manual_review_required.json` üretilir.
- `safe_project_name` şu karakterler temizlendikten sonra üretilir: `/ \ : * ? " < > |`
- Kolon genişlikleri: A=10, B=10, C=12, D=12, E=8, F=10, G=12, H=12, I=12, J=12, K=14

## Hata Durumu Çıktısı

`partlist_manual_review_required.json` formatı:
```json
{
  "job_id": "<job_id>",
  "reason": "missing_weight_or_area",
  "affected_poz": ["<poz_no>", ...]
}
```
