# Claw3D Vizyonu

Claw3D, OpenClaw tarafından desteklenen AI agent'larını görselleştirmek ve onlarla etkileşim kurmak için açık kaynaklı bir 3D ortamdır.

Claw3D'nin uzun vadeli hedefi, AI agent'larının ve insanların birlikte çalıştığı canlı bir 3D dünya inşa etmektir: agent'ların paylaşılan bir görsel alanda çalıştığı, iletişim kurduğu ve görevler yürüttüğü bir tür dijital şehir.

OpenClaw zeka ve orkestrasyon motoru olarak işlev görürken, Claw3D agent faaliyetini anlaşılır, incelenebilir ve iş birliğine açık kılan görsel katmanı ve etkileşimli ortamı sağlar.

Bu belge projenin yönünü ve gelişimini yönlendiren kuralları açıklamaktadır.

Proje genel bakışı ve geliştirici dokümantasyonu şu dosyalarda bulunabilir:

- [`README.md`](README.md)
- [`ROADMAP.md`](ROADMAP.md)
- [`CONTRIBUTING.md`](CONTRIBUTING.md)

## Claw3D Neden Var?

AI sistemleri giderek daha yetenekli hale geliyor, ancak davranışları çoğunlukla görünmez veya anlaşılması zor.

Claw3D, AI sistemleri için bir görsel arayüz sunarak bu sorunu çözmeyi hedefler ve insanların:

- AI agent'larının gerçek zamanlı çalışmasını gözlemlemesine,
- sistem davranışını görsel olarak anlamasına,
- paylaşılan ortamlarda AI ile iş birliği yapmasına,
- karmaşık agent etkileşimlerini hata ayıklamasına ve incelemesine

olanak tanır.

Nihai vizyon, bir AI agent'ları 3D şehridir; burada:

- agent'lar servisleri, görevleri ve iş akışlarını temsil eder,
- insanlar onları keşfedebilir, izleyebilir ve etkileşim kurabilir,
- sistemler uzamsal etkileşim yoluyla anlaşılır hale gelir.

## OpenClaw ile İlişkisi

Claw3D, OpenClaw'un yerini almak için değil, onunla birlikte çalışmak için tasarlanmıştır.

OpenClaw şunları sağlar:

- agent orkestrasyonu,
- araçlar ve entegrasyonlar,
- iletişim kanalları,
- görev yürütme,
- model sağlayıcı entegrasyonları.

Claw3D şunları sağlar:

- görselleştirme,
- etkileşim,
- agent'ların ve sistemlerin uzamsal temsili,
- insanlar ve AI için iş birliği ortamları.

Basit bir ifadeyle:

```text
OpenClaw -> zeka ve görev yürütme
Claw3D   -> görselleştirme ve etkileşim katmanı
```

OpenClaw entegrasyonuyla uyumluluğu korumak önemli bir tasarım hedefidir.

OpenClaw entegrasyonunu bozan özellikler, güçlü bir mimari neden olmadıkça genellikle kabul edilmez.

## Mevcut Öncelikler

Claw3D hâlâ gelişiminin erken aşamasındadır.

Mevcut öncelikler şunlardır:

### Kararlılık ve Güvenilirlik

- hata düzeltmeleri,
- öngörülebilir render davranışı,
- geliştirici deneyimini iyileştirme.

### Temel Mimari

- agent'ların görsel varlıklara nasıl eşlendiğini tanımlama,
- ölçeklenebilir bir dünya modeli oluşturma,
- OpenClaw ile temiz bir entegrasyon yolu oluşturma.

### Geliştirici Ergonomisi

- ortamı genişletmek için net API'ler,
- kolay yerel kurulum,
- anlaşılır katkı yolları.

### Görselleştirme Temelleri

- agent'ların temsili,
- iş akışlarının temsili,
- sistem faaliyetinin uzamsal biçimde temsili.

## Katkı Kuralları

Projeyi sürdürülebilir tutmak için:

- Bir PR = bir konu. İlgisiz değişiklikleri bir arada sunmaktan kaçının.
- Çok büyük PR'lar reddedilebilir veya daha küçük parçalara bölünebilir.
- Mimari değişiklikler, uygulamadan önce issue'larda tartışılmalıdır.
- Katkıda bulunanlar projenin yönüne ve kapsamına saygı göstermelidir.

Claw3D hâlâ hızla geliştiği için iterasyon beklenmektedir.

## Mimari Yön

Claw3D, agent sistemlerinin üzerinde bir görsel katman olarak tasarlanmıştır.

Sistem şu özelliklerini korumalıdır:

- modüler,
- genişletilebilir,
- denemesi kolay.

Mevcut teknoloji yığını şunlara odaklanmaktadır:

- Three.js,
- WebGL,
- tarayıcı tabanlı render,
- OpenClaw runtime sistemleriyle entegrasyon.

Amaç, ortamın geliştiriciler ve katkıda bulunanlar için erişilebilir kalmasıdır.

## Şimdilik Birleştirilmeyecekler

Odağı korumak için aşağıdaki katkı türlerinden genellikle kaçınılır:

- OpenClaw uyumluluğunu bozan özellikler,
- önceden tartışılmadan yapılan büyük mimari yeniden yazımlar,
- güçlü teknik gerekçe olmadan render yığınının değiştirilmesi,
- hacklenebilirliği azaltan ağır framework katmanları,
- önceden koordinasyon sağlanmadan sunulan son derece büyük PR'lar,
- Claw3D vizyonunu ilerletmeyen ilgisiz ürün deneyleri.

Bu liste yönlendirici bir kural olup kalıcı bir kısıtlama değildir.

Güçlü teknik argümanlar veya kullanıcı talebi bu kararları değiştirebilir.

## Uzun Vadeli Yön

Claw3D'nin uzun vadeli vizyonu iddialıdır:

**AI agent'larından oluşan bir 3D şehir.**

Bu ortamda:

- AI agent'ları görünür varlıklar olarak çalışır,
- sistemler uzamsal olarak anlaşılır hale gelir,
- insanlar agent sistemleriyle gerçek zamanlı etkileşim kurabilir,
- insanlar ve AI arasındaki iş birliği doğal bir hal alır.

Günlükler ve panolar aracılığıyla görünmez sistemlerle etkileşim kurmak yerine, kullanıcılar sistemlerin içinde dolaşabilecek ve onlarla doğrudan etkileşime girebilecektir.

Claw3D, o geleceğe doğru atılmış erken bir adımdır.
