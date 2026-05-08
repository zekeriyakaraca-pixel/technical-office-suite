# Yol Haritası

Bu dosya, dış katkıda bulunanların mevcut önceliklerle örtüşen çalışmalar bulabilmesi için Claw3D'nin kısa vadeli yönünü aktarmaktadır.

## Şu An

- Açık kaynak hazırlığı: belgeler, destek rotaları, CI, ifşa dosyaları ve herkese açık güvenli varsayılanlar.
- Runtime güvenilirliği: gateway olay yönetimini, geçmiş uzlaştırmasını ve aktarım özgü kurtarmayı daha öngörülebilir hale getirme.
- Ofis mimarisi netliği: ofis niyet katmanını merkezileştirilmiş tutma ve geçici oda bazlı davranışları azaltma.

## Sonra

- Sürükleyici ofis ve builder yığınını daha net bir paylaşılan model üzerinde birleştirme.
- Çözüme kavuşturulmamış paketlenmiş varlıkları ve bağımlılık lisanslama risklerini tamamen giderme.
- Studio erişim bootstrap'i ve runtime token yönetimi etrafındaki güvenlik durumunu iyileştirme.

## Daha Sonra

- Daha kapsamlı ofis yazarlık iş akışları ve zengin dünya inşa araçları.
- Daha iyi katkıda bulunan otomasyonu, yayın süreci ve yayın araçları.
- Mevcut ofis niyeti ve runtime olay modeli üzerine inşa edilen daha sürükleyici agent/sistem yüzeyleri.

## OpenClaw Bağımlılığını Azaltmak İçin Ürün Fikirleri

- Mevcut oyun kitabı şablonları, kullanıcı katılımı akışı ve agent oluşturma adımları üzerine inşa ederek yeni agent sihirbazını yeniden kullanılabilir agent şablonları ve ön ayarlarına dönüştürme.
- Mevcut kullanıcı katılımı ve bağlantı deneyimini, gateway erişimini, izinleri, yerel-uzak davranışını ve yaygın entegrasyonları tek bir yerde doğrulayan daha kapsamlı bir çalışma alanı kurulum sihirbazına dönüştürme.
- Zamanlı otomasyonları, `HEARTBEAT.md`'yi ve ilgili varsayılanları kurulumu birden fazla yüzey arasında bölmek yerine bir rehberli UI'da birleştiren birinci sınıf bir heartbeat builder ekleme.
- Kullanıcıların tüm ofis genelindeki agent izinlerini ve izin verilen araçları tek tek agent yerine toplu olarak yönetebilmesi için toplu kontrollerle bir filo düzeyinde araç erişim matrisi ekleme.
- Her agenti bağımsız olarak düzenlemek yerine `USER.md` varsayılanlarını birden fazla agent genelinde yönetip isteğe bağlı olarak senkronize edebilen paylaşılan bir kullanıcı profil merkezi ekleme.
- Mevcut sonuçlar/gelen kutusu yüzeylerinin ötesine geçen ve kullanıcıların agent'lar arasında iş atayıp yeniden deneyip yönlendirebileceği gerçek bir agent gelen kutusu ve görev kuyruğu ekleme.
- Gateway durumunu, başarısız çalıştırmaları, heartbeat sorunlarını, eksik bağımlılıkları ve entegrasyon sorunlarını tek bir operasyonel görünümde bir araya getiren özel bir sağlık panosu ekleme.
- Kullanıcıların tekrarlayan iş akışlarını daha kolay kaydedip göz atıp yeniden kullanabilmesi için mevcut oyun kitabı şablonu temeline dayanan daha kapsamlı bir istem ve oyun kitabı kütüphanesi ekleme.
- Kullanıcıların yinelenen davranışları ve oda bazlı eylemleri alt düzey gateway kavramlarına güvenmek yerine doğrudan ofisten yapılandırmasına olanak tanıyan görsel ofis otomasyon özellikleri ekleme.
- Kullanıcıların ham yapılandırmayı düzenlemek zorunda kalmadan hangi agent'ların iş birliği yapacağını, aktaracağını veya birbirleriyle iletişim kuracağını yapılandırabilmesi için bir agent ilişkileri ve iletişim haritası ekleme.
- Mevcut deneyim yalnızca agent başına `MEMORY.md`'yi sunduğundan, çapraz agent bağlamı için paylaşılan bellek yönetimi ekleme.
- PM -> Mühendis -> QA gibi yaygın diziler için manuel koordinasyona güvenmek yerine açık UI ile çok agent orkestrasyonu ve aktarım iş akışları ekleme.
- Gateway genelindeki değişikliklerin Claw3D'den incelenip güvenle geri alınabilmesi için yapılandırma diff ve geri alma araçları ekleme.
- Başarılı bir sohbet veya ofis etkileşimini yeniden kullanılabilir yeni bir agent'a dönüştürebilen konuşmadan-agent bootstrap akışları ekleme.
- Mevcut sahte telefon/metin senaryolarını daha kapsamlı çok agent prova ve test akışlarına genişleten daha zengin bir senaryo simülatörü ekleme.

## Halihazırda Devam Eden veya Kısmen Kapsanan Konular

- Skill yükleyici uyumluluk kontrolleri zaten mevcuttur ve yeniden icat edilmek yerine genişletilmelidir.
- Oyun kitabı şablonları, zamanlı otomasyonlar ve kullanıcı katılımı akışları, şablonlar/kurulum hikayesinin bir kısmını zaten kapsamaktadır.
- Agent başına yetenek kontrolleri ve araç ayarları zaten mevcuttur, ancak henüz filo genelinde bir matris olarak değil.
- Analitik, bağlantı durumu ve ofis faaliyet yüzeyleri, gelecekteki sağlık panosu hikayesinin bir kısmını zaten kapsamaktadır.
- Ofis builder'ı, sürükleyici ofis ve olay tetiklemeli davranış, görsel otomasyon hikayesinin bir kısmını zaten kapsamaktadır.

## İyi İlk Katkı Alanları

- Belgeler ve geliştirici kullanıcı katılımı düzeltmeleri.
- Runtime iş akışları veya ofis niyet davranışı etrafında odaklanmış birim testi eklemeleri.
- Tek bir özellik alanı içinde kalan küçük UI cilalama sorunları.
- Genel belgelerdeki bayat örneklerin, yer tutucu metinlerin veya yalnızca dahili varsayımların değiştirilmesi.

## Büyük Çalışmalara Başlamadan Önce

- `README.md`, `CODE_DOCUMENTATION.md` ve `KNOWN_ISSUES.md`'yi okuyun.
- Büyük mimari değişikliklerden önce GitHub issue'su açmayı veya bağlamayı tercih edin.
