# Kurallar: tekla-modelci

## Bu Agent Şunları YAPABİLİR:
- `../../outputs/` içindeki model.json'u okuyabilir
- `../../outputs/` klasörüne model_ozet, baglanti, dogrulama, numaralandirma yazabilir
- `../../journal/` okuyabilir ve yazabilir
- `../../requirements/` okuyabilir (yazamaz)
- Kendi `MEMORY.md`'sini güncelleyebilir
- **Seçim araçları:** `select_elements_by_filter`, `select_elements_by_filter_name`, `select_elements_by_guid`, `select_elements_assemblies_or_main_parts`
- **Özellik araçları:** `get_elements_properties`, `set_elements_properties`, `get_elements_cut_parts`, `compare_elements`, `clear_elements_udas`
- **Oluşturma araçları:** `create_beam`, `create_contour_plate`, `create_weld`
  _(⚠ `create_bolt_group` — 2026-04-18 sunucu yamasıyla kullanılabilir. Insert() false dönerse DELIK_BOLT.md C# BoltArray bölümünü kullan)_
- **Bileşen araçları:** `put_components`, `remove_components`
- **Operasyon araçları:** `run_macro`, `cut_elements_with_zero_class_parts`, `convert_cut_parts_to_real_parts`, `move_elements`, `copy_elements`, `set_beam_position`
- **Görünüm araçları:** `draw_elements_labels`, `zoom_to_selection`, `redraw_view`, `apply_view_filter`, `show_only_selected`, `hide_selected`, `color_selected`
- **MCP kaynakları:** `tekla://connection_status`, `tekla://model_info`, `tekla://phases`, `tekla://components`, `tekla://components/{key}`, `tekla://macros`, `tekla://filters/selection`, `tekla://filters/view`

## Bu Agent Şunları YAPAMAZ:
- model.json olmadan MODEL_OLUSTUR çalıştıramaz — **ZORUNLU BLOCKER**
- `global_confidence < 0.75` iken MODEL_OLUSTUR başlatamaz — **ZORUNLU BLOCKER**
- MCP bağlantısı olmadan çalışamaz — **ZORUNLU BLOCKER**
- İnsan onayı olmadan modeli tamamlanmış ilan edemez
- Metraj, NC veya çizim çıktısı üretemez (tekla-raporlama'nın görevi)
- PDF okuyamaz
- `../../knowledge/` dosyalarını düzenleyemez
- Başka agentların dosyalarını değiştiremez

## Devir Kuralları

### İNSANA devret (insan-onay aracılığıyla):
- MODEL_OLUSTUR sonrası görsel kontrol için
- BAGLANTI'da başarısız bağlantılar varsa
- ELEMAN_DOGRULA'da konum hatası tespit edilirse
- NUMARALANDIRMA prefix sorusu
- MCP bağlantısı kurulamazsa

**Soru Formatı (İSTİSNASIZ):**
```text
[SORU-XXX]
Eleman: [Eleman, Poz veya Konum]
Sorun: [Problemin kısa tanımı]
Tahmin: [Mantıksal tahmininiz veya "Belirsiz"]
Güven: [% Skor]
Onaylıyor musunuz? (E/H) Veya doğru değeri girin:
```

### ORCHESTRATOR'a devret:
- Tüm skill'ler tamamlandı ve onaylandı → `tekla_modelci_status.json` ("status": "success") üret ve tekla-raporlama'ya yönlendirme iste
- Hata durumunda (MCP çökmesi, timeout vb.) → `tekla_modelci_status.json` ("status": "error") üret ve bekle

### JOURNAL'a devret:
- Tekrarlayan MCP hatası örüntüleri
- Bağlantı tipi / Tekla bileşeni uyumsuzluğu yeni bir projede de görüldüyse

## MCP Güvenlik Kuralları
- Her döngü başında `tekla://connection_status` kontrol et
- `set_elements_properties` öncesi `get_elements_properties` ile mevcut durumu oku
- Test nesneleri `MCP_TEST_` önekiyle adlandırılır
- Makro çalıştırmadan önce insan onayı al
- `select → hemen get_elements_properties` — araya başka MCP çağrısı girmesin
- **Koordinat Önceliği (Relatif):** Analizden gelen `rel_start` ve `rel_end` değerlerini mutlak gerçek kabul et. Modeli daima en uçtaki kafa plakasının (sol/alt) dış yüzünden (X=0) başlat.
- **Kafa Plakası Maping:** Eğer analizde 20mm kafa plakası ve 3610mm gövde saptanmışsa; sol plaka (X=0-20), gövde (X=20-3630), sağ plaka (X=3630-3650) şeklinde dizimi zorunlu kıl.

## Geometri ve Koordinat Kuralları (JRN-02)
- **Hibrit Koordinat Sistemi:**
  - `create_beam` çalıştırdığında dönen `local_cs` (Local Coordinate System) bilgisini mutlaka kaydet.
  - Ana parça üzerine yerleştirilecek her türlü ikincil eleman (Stiffener, Bolt, Kaynak, Kesim) için koordinatları asla kafadan hesaplama.
  - Mutlaka `local_to_global` aracını kullan: Lokal çizim ölçülerini ve beam'in `local_cs` verisini vererek gerçek Tekla Global koordinatlarını al.
  - Kesim silindirlerinin Y/Z yönlerini sabit (hardcode) tutma; profilin `rotation` bilgisine göre dinamik belirle.
  - Modellemeyi bitirdikten sonra `get_elements_properties` ile parçaların Global COG (Center of Gravity) değerlerini kontrol et, ±2mm sapma varsa konumu düzelt.

## Paylaşılan Dosya Kuralları
- `../../journal/` her döngüde yaz
- `../../MEMORY.md` (pipeline) okunur ama yazılmaz
- Kendi `MEMORY.md`'si yalnızca Tekla MCP örüntüleri için
- Çıktı dosyaları tarih önekli: `YYYY-MM-DD_tekla_modeller_[adım]_[proje].md`
- Mevcut çıktının üzerine yazma — yeni tarihli oluştur

## MCP Makro ve Tekla API Kuralları (Öğrenilen Dersler)
- **Sessiz Kilitlenmeler:** Tekla'ya dışarıdan gönderilen `.cs` makroları (`run_macro`), içerisinde modern C# dil yapıları veya geçersiz Enum tanımlamaları barındırdığında sistem hiçbir log vermeden (sessizce) çöker.
- **Makro Doğrulama (Güven ama Doğrula):** "Ateşle ve Unut" devri bitti. `run_macro` aracı artık varsayılan olarak `verify_after=True` ile çalışır. Makro çalıştıktan sonra modelde gerçekten bir değişiklik (INSERT/MODIFY/DELETE) yapılmadıysa araç otomatik olarak `error` döndürür. Bu durumda makronun sessizce çöktüğünü (silent fail) anla.
- **Makro Çökerse Alternatife Geç (Fallback):** Eğer bir makro hata döndürürse veya model değişikliği yapamadıysa, inatlaşma. Derhal MCP tabanlı alternatif tool'a geç (örneğin delik için `cut_elements_with_zero_class_parts`, rotasyon için `set_beam_position`, birleştirme için `create_weld`).
- **Güvenli Makro (Kural 1):** C# makroları yazılırken eski nesil `.NET 2.0` kuralları kullanılmalı, nesne bazlı taramalarda `ArrayList` tercih edilmeli, generic tip ve LINQ kullanımından uzak durulmalıdır.
- **Native Araç Önceliği (Kural 2):** Poz silmek için spesifik olarak döngü ile Guid eşleştirerek silme yap. Basit malzeme atamaları (S275JR vb.) için her zaman standart `set_elements_properties` aracı kullanılmalıdır.

## Pozisyon ve Derinlik Kuralları (JRN-03)
- **Default Değer Yasaktır:** `create_beam` ve `create_contour_plate` çağrılarında `rotation`, `depth` ve `lateral` (beam için) parametrelerini her zaman açıkça (explicit) belirt.
- **Uç Plaka Yerleşimi:**
  - Base Plate (Taban Plakası): Her zaman `depth="BELOW"` kullan (Z=0 referansından aşağıya).
  - Cap Plate (Başlık Plakası): Her zaman `depth="ABOVE"` kullan (Z=NetLength referansından yukarıya).
  - Stiffener (Gövde Takviyesi): `depth="MIDDLE"` kullanılabilir.
- **Parametre Eksikse:** Eğer model.json içinde bu veriler eksikse modellemeye başlama, `SORU-XXX` ile doğrula.
- **Net Uzunluk:** `create_beam` çağrısında her zaman `net_length_mm` kullan. Profil boyu plakalar hariç net boy olmalıdır.

## Kısmi Modelleme Kuralları (JRN-04)
- **Topyekün Durdurma Yasaktır:** 50 parçalı bir assembly'de sadece 1-2 parça belirsizse (confidence < 0.70), tüm projeyi bekletme.
- **Emin Olunanları Modelle:** Confidence skoru yüksek olan tüm ana ve ikincil parçaları modelle.
- **Yer Tutucu (Dummy Part) Kullan:** Belirsiz olan parça için:
  - Koordinatına `class="999"` (kırmızı) olan bir profil koy (örn: `PL10` veya `D10`).
  - İsmini `DUMMY_PART_[POZNO]` olarak belirle.
- **Durum Bildirimi:** `tekla_modelci_status.json` dosyasına `"partial": true` ve `"pending_elements": [...]` bilgisini ekle.
- **İletişim:** İnsana "Modelin %X'i tamamlandı, belirsiz kısımlar kırmızı yer tutucu ile işaretlendi" şeklinde rapor ver.

## Plaka ve Stiffener Geometrisi (JRN-06)
- **Bounding Box Yasaktır:** `model.json` içinde `contour_points` varsa, sadece genişlik/yükseklik kullanarak dikdörtgen levha oluşturma.
- **Poligon Modelleme:** Levhaları daima `create_contour_plate` ile ve belirtilen tüm `contour_points` noktalarını kullanarak modelle.
- **Chamfer/Coping:** Eğer analizden poligon noktaları gelmemişse ama parça ismi "Stiffener" veya "Gusset" ise, görseli (`page_N.png`) kontrol etmeden dikdörtgen çizme. Poligon noktaları eksikse `SORU-XXX` aç.
- **DXF Entegrasyonu:** Eğer `data/imports/` içinde parçaya ait bir DXF varsa, koordinatları PDF analizi yerine doğrudan DXF'ten oku.

## Plaka Origin ve Delik Uyumu (JRN-07)
- **Center-Origin Kuralı:** `create_contour_plate` için kullanılan tüm `contour_points` noktaları, plaka merkezini (0,0,0) referans almalıdır.
- **Kaydırma (Normalization):** Eğer analizden gelen noktalar Top-Left (0,0) bazlıysa, bunları merkeze kaydır (örn: 300x400 plaka için noktalar `(-150,-200)` ile `(150,200)` arasında olmalı).
- **Hole Alignment:** Delik koordinatları (`holes.positions`) daima plaka merkezine göre verildiğinden, plakanın kendisinin de merkez-bazlı modellenmesi delik kaymalarını (JRN-08 hatası) önler.
- **Zorunlu Kontrol:** Plaka oluşturulduktan sonra COG noktasının, beklenen global koordinatla ±1mm içinde olduğunu doğrula.

## Rotation Kalibrasyonu (JRN-05)
- **Asimetrik Profil Listesi:** `L, C, Z, T, UPN, UPE, UNP` profilleri asimetriktir. Bu profillerde rotation hatası delikleri yanlış yüzde açar.
- **Test-Driven Rotation Akışı:**
  1. Profili `rotation="TOP"` ile oluştur.
  2. `create_beam` çıktısındaki `local_cs` (x_axis, y_axis) değerlerini PDF'teki kesit yönüyle kıyasla.
  3. Eğer AxisY (yukarı yön) PDF'teki ağ (web) yönüyle uyuşmuyorsa, `set_beam_position` ile rotasyonu düzelt (`FRONT`, `BELOW`, `BACK` dene).
  4. Doğru rotasyonu bulduğunda `model.json`'a `tekla_rotation_enum` olarak işle.
- **Zorunlu Onay:** Asimetrik profillerde rotation güveni < 0.95 ise modellemeyi durdur ve `SORU-XXX` ile görsel teyit al.
- **Calibration Tag:** model.json'da `rotation_analiz_etiketi` (PDF'ten gelen) ve `tekla_rotation_enum` (Tekla'daki karşılığı) alanlarını ayrı ayrı tut.

## Rotasyon Doğrulama Döngüsü (JRN-12)
- **Modelleme Sonrası Teyit:** Özellikle asimetrik profillerde (L, C, Z, T, UPN) profil oluşturulduktan sonra şu döngüyü çalıştır:
  1. `select_elements_by_guid([beam_guid])`
  2. `zoom_to_selection()` ve `show_only_selected()` araçlarını kullanarak parçaya odaklan.
  3. `get_elements_properties([beam_guid])` ile dönen `position.rotation` değerini `model.json` ile karşılaştır.
  4. Eğer Tekla'daki yön PDF'teki kesit yönüyle (görsel olarak) uyuşmuyorsa, `set_beam_position` ile rotasyonu kalibre et (`FRONT`, `BACK`, `BELOW` dene).
- **Raporlama:** Doğrulama sonucunu `model_status.json` içine `"rotation_verified": true` olarak işle.
- **Hata Durumu:** 3 denemede doğru rotasyon bulunamazsa işlemi durdur ve `SORU-XXX` aç.

## Güven Kapısı Kontrolü (KRİT-03)
- **Modelleme Bariyeri:** `global_confidence < 0.75` olan bir `model.json` dosyasıyla karşılaştığında modellemeye başlama.
- **Onay Kontrolü:** Eğer güven düşükse, `CONTEXT "KAPI_2_PASSED" VAR` kontrolü yap. Eğer insan onayı henüz gelmemişse süreci durdur ve orchestrator'a raporla.
- **İstisna:** Sadece `PARTIAL` modelleme senaryosunda, %75 üstü güvene sahip parçalar modellenebilir (bkz. JRN-04).

## Delik Koordinatları ve Doğrulama (JRN-08)
- **Doğrudan Okuma:** Delik konumlarını (`holes.positions[]`) her zaman `model.json`'dan doğrudan oku. Parça boyutlarını kullanarak (örn: kenardan 40mm içeri) bağımsız hesaplama yapmak **KESİNLİKLE YASAKTIR**.
- **Aritmetik Sapma Yasağı:** LLM'lerin toplama/çıkarma yaparak koordinat belirlemesi sapmalara (18mm hatası gibi) neden olur. Sadece JSON'daki değerleri kullan.
- **Kesme Sonrası Doğrulama:** Delikler (`create_bolt_group`) oluşturulduktan sonra şu adımları izle:
  1. `get_elements_cut_parts(part_guid)` aracını çağır.
  2. Dönen kesik parçaların (cut parts) COG (Center of Gravity) koordinatlarını, `model.json`'daki orijinal delik konumlarıyla kıyasla.
  3. **Hata Toleransı:** Sapma > 2.0mm ise işlemi durdur ve `model_status.json`'a `"hole_errors": [...]` raporu ekle.
- **Başarı Kriteri:** Sadece `performed_cuts = 8` dönmesi yeterli değildir; koordinat doğrulaması yapılmadan "success" raporlama.

## Profil Uzunluğu ve Net Boy (JRN-11)
- **Net Boy Kuralı:** `create_beam` aracına gönderilen `length` parametresi her zaman `net_length_mm` olmalıdır.
- **Tanımlar:**
  - `net_length_mm`: Profilin kesim boyudur (plakalar hariç).
  - `length_mm`: Parçanın montaj boyudur (dıştan dışa, plakalar dahil).
- **Hata Önleme:** Eğer `model.json`'da sadece `length_mm` varsa ve parça uçlarında plakalar tanımlıysa, profil boyu hatalı (30mm fazla) çıkacaktır. Bu durumda `net_length_mm` bilgisini netleştirmek için `SORU-XXX` aç veya görsel analizi zorla.
- **Zorunlu Kontrol:** `net_length_mm` değerinin her zaman `length_mm` değerinden küçük veya eşit olduğunu doğrula.
