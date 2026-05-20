# Kurallar: tekla-raporlama

## Bu Agent Şunları YAPABİLİR:
- `../../outputs/` içindeki model_ozet ve numaralandirma dosyalarını okuyabilir
- `../../outputs/` klasörüne metraj, nc, cizim raporları ve partlist.xlsx yazabilir
- `../../journal/` okuyabilir ve yazabilir
- `../../requirements/` okuyabilir (yazamaz)
- `../../knowledge/ERT_PARTLIST_FORMAT.md` okuyabilir (yazamaz)
- Kendi `MEMORY.md`'sini güncelleyebilir
- **Seçim araçları:** `select_elements_by_filter`, `select_elements_by_filter_name`, `select_elements_by_guid`, `select_elements_assemblies_or_main_parts`
- **Özellik araçları:** `get_elements_properties`, `get_elements_cut_parts`, `compare_elements`
- **Operasyon araçları:** `run_macro`
- **Raporlama araçları:** `get_material_takeoff`, `get_weight_summary`, `get_part_list`, `run_tekla_report`
- **NC export araçları:** `export_nc_files`, `list_nc_settings`, `convert_nc1_to_dxf`, `get_nc_export_status`
- **Çizim araçları:** `smart_create_fabrication_drawing`
- **Görünüm araçları:** `draw_elements_labels`, `zoom_to_selection`, `redraw_view`, `apply_view_filter`, `show_only_selected`, `hide_selected`, `color_selected`
- **MCP kaynakları:** `tekla://connection_status`, `tekla://phases`, `tekla://macros`, `tekla://filters/view`, `tekla://model_info`

## Bu Agent Şunları YAPAMAZ:
- GUID tablosu olmadan METRAJ_CIKART başlatamaz — **ZORUNLU BLOCKER**
- NUMARALANDIRMA tamamlanmadan METRAJ/CIZIM başlatamaz — **ZORUNLU BLOCKER**
- Tekla'da eleman oluşturamaz veya düzenleyemez (`create_beam`, `set_elements_properties` vb.)
- `put_components` veya `remove_components` çağıramaz
- PDF okuyamaz
- `../../knowledge/` dosyalarını düzenleyemez
- Başka agentların dosyalarını değiştiremez

## Devir Kuralları

### İNSANA devret (insan-onay aracılığıyla):
- METRAJ_CIKART sonrası tonaj onayı
- NC_EXPORT sırasında `export_nc_files` API başarısız olursa manuel export adımları
- NC_EXPORT sonrası dosya onayı
- CIZIM_URET sonrası çizim teslimi

### ORCHESTRATOR'a devret:
- Tüm çıktılar tamamlandı → `tekla_raporlama_status.json` ("status": "success") üret, döngü tamamlandı sinyali ver
- Kritik hata durumunda → `tekla_raporlama_status.json` ("status": "error") üret ve işlemi durdur

### JOURNAL'a devret:
- Profil bazlı metraj anomali örüntüleri (yeni projede de görüldüyse)
- NC makro başarısızlıkları ve koşulları

## Profil Filtreleme Özel Kuralları

⚠️ **CFCHS/CFSHS/CFRHS soğuk şekil profilleri:**
- `Starts With "CHS"` bu profilleri yakalamaz
- Her soğuk şekil ön eki için ayrı sorgu: CFCHS, CFSHS, CFRHS

**Profil tarama sırası:**
IPE → HEA/HEB/HEM → INP → RHS → CHS → CFCHS/CFSHS/CFRHS → SHS → UNP/UPE → L → CC → PL

## B.ALAN (Yüzey Alanı) Kuralı

- Tekla property: `AREA_NET` (mm² cinsinden)
- m² için: `AREA_NET / 1,000,000`
- `PAINTING_AREA` ve `NET_SURFACE_AREA` **çalışmıyor** — `AREA_NET` kullan
- Fallback (AREA_NET alınamazsa): IPE240→0.875, L50×5→0.190, CC170→0.680 m²/m

## Paylaşılan Dosya Kuralları
- `../../journal/` her döngüde yaz
- `../../MEMORY.md` (pipeline) okunur ama yazılmaz
- Kendi `MEMORY.md`'si yalnızca metraj/NC örüntüleri için
- Çıktı dosyaları tarih önekli: `YYYY-MM-DD_tekla_modeller_[adım]_[proje].md`

## NC Klasörü (Sabit Kural)

**NC çıktı klasörü her zaman:** `[USER_HOME]\Desktop\Tekla-Agent\outputs\nc\`

- Proje bazlı alt klasör **açma** — tüm projeler aynı `nc\` altında
- Klasör yoksa `os.makedirs(..., exist_ok=True)` ile oluştur, insana sorma
- `export_nc_files` çağrısında `output_folder` bu değeri alır
- `api_failed` durumunda insana gösterilen manuel adımda da aynı klasör belirtilir

## Proje Arşivleme (JRN-09)
- **Tetikleyici:** Tüm raporlama (Metraj, NC, Çizim) başarıyla tamamlandığında.
- **Eylem:** `python scripts/archive_project.py <proje-kodu>` scriptini çalıştır.
- **Kapsam:** `outputs/` altındaki model, partlist, nc1/dxf dosyaları ve `journal/` altındaki retrospektif dosyası `archive/<proje>/` altına taşınır.
- **Raporlama:** Arşivleme bittikten sonra `tekla_raporlama_status.json` içine `"archive_path"` bilgisini ekle.
