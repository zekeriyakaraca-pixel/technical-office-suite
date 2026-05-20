# tekla-raporlama Heartbeat

## Zamanlama
Orchestrator (`Tekla_modeller`) yönlendirir — tekla-modelci tüm adımlarını tamamlayıp insan "model onaylandı" sinyali verdikten sonra çalışır.

## Her Döngü

### 1. Bağlam Oku
- `../../journal/` son 3 girişi — bekleyen onay veya yarım kalmış adım var mı?
- Kendi `MEMORY.md` — profil tarama örüntüleri, NC makro bilinen sorunları
- `../../knowledge/ERT_PARTLIST_FORMAT.md` — Excel partlist kuralları
- `../../requirements/[proje].json` — NC ayarları

### 2. BLOCKER Kontrol Et

```
tekla://connection_status → BAĞLI DEĞİL → dur, insana bildir
model_ozet_[proje].md yok → dur, tekla-modelci tamamlamamış
numaralandirma.md yok     → dur, PART_POS atanmamış
"model onaylandı" yok     → dur, insan-onay tamamlanmamış
→ Tüm BLOCKER'lar geçildiyse devam et
```

### 3. Durum Değerlendir — Hangi Skill

```
metraj.md yok             → METRAJ_CIKART çalıştır
metraj onayı bekliyor     → DUR: insan-onay kapısı
nc.md yok                 → NC_EXPORT çalıştır
                             NC_EXPORT bittikten sonra → ACIKLAMA_GUNCELLE otomatik çalışır
nc onayı bekliyor         → DUR: insan-onay kapısı
cizim.md yok              → CIZIM_URET çalıştır
tüm çıktılar var          → Döngü tamamlandı, orchestrator'a bildir
```

### 4. Skill Çalıştır

**METRAJ_CIKART sırası:**
1. MCP bağlantı + faz listesi kontrol
2. Her profil tipi için: select_elements_by_filter (Starts With "IPE", "HEA" vb.)
   - ⚠️ CFCHS/CFSHS/CFRHS ayrı prefix — her biri için ayrı sorgu
3. get_elements_properties: NAME, PROFILE, MATERIAL, LENGTH, WEIGHT, PART_POS, ASSEMBLY_POS, PHASE, AREA_NET, GUID
   - AREA_NET birimi: mm² → m² için ÷1,000,000
4. compare_elements ile anomali tespiti
5. Renk: doğru → yeşil, anomali → kırmızı
6. Profil/malzeme/faz bazlı özet tablo
7. PDF analiz sonuçlarıyla uyum kontrol: Fark > `requirements/[proje].json` içindeki `tolerances.tonnage` (varsayılan %5) ise → insana bildir
8. ERT_PARTLIST_FORMAT.md'ye göre Excel üret → `outputs/[proje]_partlist.xlsx`
   ⛔ `run_tekla_report` / `part_list.rpt` KULLANMA — API çalışmıyor, gereği de yok.
      ERT Excel zaten tüm parça listesi çıktısını karşılıyor.
   ⚠️ AÇIKLAMA sütunu bu adımda **"Deliksiz" başlangıç değeriyle** üretilir.
      NC_EXPORT + ACIKLAMA_GUNCELLE tamamlanmadan partlist **TESLİM EDİLMEZ**.

**NC_EXPORT sırası:**
> ⚡ **NC çıktı klasörü SABİT: `[USER_HOME]\Desktop\Tekla-Agent\outputs\nc\`**  
> İnsana klasör sorma — doğrudan başlat. Klasör yoksa `os.makedirs` ile oluştur.

1. `list_nc_settings()` → geçerli `nc_settings_name` listesi al
2. Plakaları seç: `Starts With "PL"` → `export_nc_files(output_folder=NC_FOLDER, nc_settings_name=...)` MCP dene
   - `status="success"` → 3. adıma geç
   - `status="api_failed"` →  
     ⛔ **BLOCKER: manual_steps içeriğini insana ilet ve BEKLE**  
     Journal'a yaz: `"⏳ BEKLENIYOR: İnsan Tekla UI'dan NC export yapacak. 'nc export yapıldı' sinyali bekleniyor."`  
     İnsan "nc export yapıldı" demeden 3. adıma GEÇİLMEZ — döngüyü durdur.
3. Çıktı klasörünü tara → .nc1 dosyalarını listele (sayı + isim)
4. **[OTOMATİK — insan onayı gerekmez]** `convert_nc1_to_dxf` MCP çağır:
   - `convert_nc1_to_dxf(input_folder=NC_FOLDER, output_folder=NC_FOLDER, overwrite=True)`
   - `status="success"` → converted_count logla, devam et
   - `status="partial"/"error"` → conversions listesinde hata detayına bak; %5 üzeri hata → dur, insana bildir
   - Fallback (MCP erişilemezse): `NC_EXPORT.md → NC1 → DXF Dönüşümü` Python kodu
5. `get_nc_export_status(output_folder=NC_FOLDER)` → unmatched_nc1 listesini kontrol et
6. Renk: üretilen DXF → mavi (0,120,255); hata → kırmızı (255,0,0)
7. HEB/IPE gibi profiller için DSTV-NC: aynı akış (profil tipi seçimi → export → validate)

**ACIKLAMA_GUNCELLE (otomatik, NC_EXPORT sonrası):**
- Delikli parçaları tespit et → partlist AÇIKLAMA sütununu güncelle

**CIZIM_URET sırası:**
1. apply_view_filter + redraw_view ile görünüm hazırla
2. Eleman hazırlığı: select → show_only_selected → zoom_to_selection → draw_elements_labels
3. Faz varsa her faz için ayrı makro çalıştır
4. Makro çalıştır (insan onayıyla)
5. Çizim sayısı doğrula

### 5. Journal'a Logla
Her döngü sonunda `../../journal/YYYY-MM-DD_HHMM.md`:
- Hangi skill çalıştı
- Tonaj özeti veya NC durumu
- Anomali veya hata
- Sıradaki adım veya bekleyen onay

### 6. MEMORY.md Güncelle
Aşağıdakilerden **biri** gerçekleştiyse `MEMORY.md`'ye ilgili bölüme 1 satır ekle:
- Tonaj farkı >%2 (PDF analiz beklentisi vs Tekla çıktısı — neden saptı?)
- Yeni profil prefix keşfedildi veya CFCHS/CFSHS gibi kaçırıldı
- NC makrosu başarısız oldu veya hiç dosya üretmedi
- B.ALAN için geometrik fallback kullanıldı (AREA_NET neden alınamadı?)

→ Hiçbiri gerçekleşmediyse bu adımı geç.

### Pipeline Kapatma (Her Proje Sonu)

Tüm skill'ler (METRAJ + NC_EXPORT + CIZIM) başarıyla tamamlandıktan sonra:
1. `python scripts/archive_project.py <proje-kodu>` çalıştır
2. Kullanıcıya bildir:
   ```
   ✅ Proje [proje-kodu] tamamlandı ve arşivlendi.
   Dosyalar: [USER_HOME]\Desktop\Tekla-Agent\archive\[proje-kodu]\
   İçerik: model.json, partlist.xlsx, NC1+DXF dosyaları, retrospektif
   ```

## Tırmanma Kuralları
- MCP bağlantısı kopuksa → dur, insana bildir
- NC hata oranı > %5 → dur, insana bildir
- Tonaj farkı > %5 → insana bildir, tahmin etme
