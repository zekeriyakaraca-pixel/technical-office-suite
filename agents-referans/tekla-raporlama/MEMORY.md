# Hafıza: tekla-raporlama

Metraj ve NC döngülerinden doğrulanmış örüntüler.

<!-- ÖNEMLİ: Sadece birden fazla projede doğrulanmış örüntüleri yaz. Tek seferlik gözlemler journal'a. -->

## Profil Tarama Örüntüleri

- **PAINTING_AREA / NET_SURFACE_AREA:** Tekla'nın gerçek property adı `AREA_NET`. `PAINTING_AREA` ve `NET_SURFACE_AREA` sorguları çalışmıyor. **B.ALAN: `AREA_NET / 1,000,000` m²** — değer mm² cinsinden gelir.
- **CHS vs CFCHS:** `Starts With "CHS"` filtresi `CFCHS88.9X4.0` gibi cold-formed profilleri **yakalamaz**. CFCHS, CFSHS, CFRHS ayrı prefix — her biri için ayrı sorgu gerekir.

## Eleman Tipleri ve Profil Örüntüleri

- **YEMLIK_2 profil seti:** IPE240 + CC170-2-25-50 + L50×5 + PL serisi (S235JR).
- **CC170-2-25-50:** Özel soğuk şekil profil, standart kütüphane dışı.
- **PL profil adı formatı:** `PL{kalınlık}*{genişlik}` — genişlik bu formatla parse edilebilir.
- **B.ALAN geometrik fallback sabitler** (AREA_NET alınamazsa): IPE240→0.875, L50×5→0.190, CC170→0.680 m²/m

## Anomali Tespiti Örüntüleri

- **P/82 (L50×5):** Live sorguda 13 eleman, eski metrajda 12 gösteriyordu — GUID bazlı doğrulama yapılmalı, sayım değişebilir.

## NC Export: MCP Araç Akışı (güncelleme 2026-04-18)

- **`export_nc_files` TRY FIRST:** Agent önce bu aracı çağırır. Araç kendi başarısızlık tespitini yapar (baseline `.nc1` sayısı karşılaştırması). `status="success"` → ilerle. `status="api_failed"` → `manual_steps` alanını insana ilet.
- **`list_nc_settings`:** `export_nc_files` öncesi geçerli `nc_settings_name` değerlerini listeler.
- **`convert_nc1_to_dxf`:** NC1 klasöründen DXF otomatik üretir — KONTUR (LWPOLYLINE), DELIK (CIRCLE), BILGI katmanları. `NC_EXPORT.md`'deki manuel Python kodu artık kullanılmaz.
- **`get_nc_export_status`:** Export sonrası doğrulama; `unmatched_nc1` DXF'siz kalan dosyaları gösterir.
- **Eski not (2026-04-10):** `Operation.CreateNCFilesFromAll/Selected` sessiz başarısızlık doğrulandı. `export_nc_files` bunu otomatik tespit eder — agent ayrıca tespit yapmamalı.
- `DSTVtoDXFConverter.cs` makrosu GUI açtığından doğrudan çalıştırılamaz — `convert_nc1_to_dxf` kullan.

## Danieli Kaynak Standartları

- Kaynak boğazı = **0.7 × t_min** (Danieli STD 2.8.006) — metraj veya kaynak raporunda kullan.

## NC1 → DXF Dönüşümü: Delikler CIRCLE Olmalı (doğrulandı 2026-04-10)

> **2026-04-18 güncellemesi:** `convert_nc1_to_dxf` MCP aracı bu kuralı otomatik uygular. Manuel Python aşağıda yalnızca MCP sunucu erişilemez olduğunda son çare olarak korunur.

- **IK bloklarını LWPOLYLINE olarak çizme.** NC1 formatı delikleri dairesel poligon (genellikle 8 nokta) olarak saklar. Bu poligondan CIRCLE üretilmeli:
  - Merkez = poligon noktalarının koordinat ortalaması (kapanış noktası hariç)
  - Yarıçap = merkeze ortalama mesafe (nominal çapın yarısına ≈ eşit çıkar, örn. Ø18 → r≈9.000 mm)
- **AK bloğu** (dış kontur) → LWPOLYLINE(closed=True) olarak kalır.
- **NC1 ilk satır formatı:** `v  {x}u  {y} ...` — `x` değeri `u` harfiyle bitişik gelir; `re.sub(r'[a-zA-Z]', '', s)` ile temizle.
- **Katmanlar:** KONTUR (color=7), DELIK (color=1), BILGI (color=3)
- **Kanıtlanan kod deseni:** `NC_EXPORT.md → NC1 → DXF Dönüşümü` bölümüne bakın.

## CIZIM_URET: smart_create_fabrication_drawing Kullanımı (doğrulandı 2026-04-10)

- **Part GUID değil, assembly GUID gerekir.** MCP parametresi `assembly_guids` (liste).
- Assembly GUID alma akışı:
  1. `select_elements_by_guid([part_guid])` — parçayı seç
  2. `select_elements_assemblies_or_main_parts(mode="Assembly")` — assembly'ye geç
  3. `get_elements_properties(["GUID"])` — assembly GUID'ini oku
  4. `smart_create_fabrication_drawing(assembly_guids=[assembly_guid])` — çizim üret
- **draw_elements_labels:** `"Part Position"` geçersiz; `"Position"` kullan.
- **Renklendirme:** Çizim üretilen eleman → mavi (R=0, G=120, B=255).

## Raporlama Araçları (MCP) — 2026-04-18

- **`get_material_takeoff`:** Seçim sonrası çağır. `group_by="profile"` ile METRAJ_CIKART'taki 17 profil döngüsünün eşdeğeri tek çağrıda gelir. `phase_filter` ile faz bazlı metraj yapılır.
- **`get_weight_summary`:** `breakdown="type"` ile kolon/kiriş/levha bazlı ağırlık dağılımı. Metraj Özet tablosunu doğrudan doldurur.
- **`get_part_list`:** `sort_by="position"` ile PART_POS sıralı liste. ERT partlist için ham veri kaynağı.
- **`run_tekla_report`:** `status="not_supported"` veya `"api_failed"` dönerse `fallback_instructions` insana iletilir — `export_nc_files` ile aynı patern.
- **Seçim önkoşulu:** Tüm raporlama araçları seçili elemanlara göre çalışır. Çağırmadan önce `select_elements_by_filter` veya `select_elements_assemblies_or_main_parts` zorunlu.

## Son Güncelleme

2026-04-18 — NC Export try-first MCP akışı; `convert_nc1_to_dxf` otomatik dönüşüm; Raporlama Araçları bölümü (`get_material_takeoff`, `get_weight_summary`, `get_part_list`, `run_tekla_report`) eklendi
# 2026-04-30 MCP API kisiti notu

- `export_nc_files` artik preflight yapar: secim, bos `nc_settings_name`, `IsNumberingAllowed` ve `IsNumberingUpToDateAll` durumlarini ayri raporlar. Numaralandirma eksikse API denenmeden `manual_required` doner.
- `.nc1` basarisi sadece dosya sayisi ile degil, dosya adi + boyut + mtime degisimiyle dogrulanir; mevcut dosyanin overwrite edilmesi de success sayilir.
- `run_tekla_report` 5 argumanli `CreateReportFromSelected(template, full_report_path, title1, title2, title3)` imzasini kullanir. Dosya olusmazsa `api_failed` + `fallback_instructions` doner.
- `manual_required` sonucu "MCP bozuk" degil, Tekla API/UI kisiti olarak ele alinmalidir.
