# Claw3D + OpenClaw + Tailscale Kurulum Rehberi

Bu rehber, en yaygın prodüksiyona benzer kurulum için adım adım bir kılavuzdur:

- **Makine A**, **OpenClaw Gateway**'i çalıştırır.
- **Makine B**, **Claw3D**'yi çalıştırır.
- **Tailscale** her iki makineyi güvenli biçimde birbirine bağlar.

Bunu tam olarak uygularsanız en yaygın karışıklıktan kaçınılır: **Claw3D, OpenClaw'u sizin için kurmaz veya çalıştırmaz.**

---

## 0) Mimari ve Sorumluluklar

- **OpenClaw**, runtime ve Gateway'dir.
- **Claw3D**, UI ve Studio proxy'sidir.
- Claw3D, hâlihazırda çalışan bir OpenClaw Gateway'e bağlanır.
- Bu rehberde Gateway, Claw3D'den farklı bir makinede bulunur.

---

## 1) Ön Koşullar

### Makine A (Gateway sunucusu)

- macOS, Linux veya WSL2.
- İnternet erişimi.
- OpenClaw ve Tailscale kurabilme imkânı.

### Makine B (Claw3D sunucusu)

- Node.js `20+` önerilir.
- npm `10+` önerilir.
- İnternet erişimi.
- Tailscale kurabilme imkânı.

### Hesaplar ve İzinler

- Tailnet'iniz için bir Tailscale hesabı.
- Tailnet'iniz cihaz onayı kullanıyorsa Tailscale yönetiminde Sahip/Yönetici/BT yöneticisi erişimine ihtiyacınız vardır.

---

## 2) Makine A'ya OpenClaw Kurma ve Başlatma

OpenClaw resmi kurulum belgeleri: [Install](https://docs.openclaw.ai/install/index.md) ve [Getting Started](https://docs.openclaw.ai/start/getting-started.md).

### 2.1 OpenClaw'u Kur

**Makine A**'da:

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

### 2.2 Kullanıcı katılımını çalıştır ve daemon'u kur

```bash
openclaw onboard --install-daemon
```

### 2.3 Gateway sağlığını doğrula

```bash
openclaw gateway status
openclaw status
```

Runtime çalışıyor ve RPC probe tamam gibi sağlıklı bir sonuç istiyorsunuz.

### 2.4 Gateway token'ınızı alın

Bu token'a Claw3D'de ihtiyacınız olacak:

```bash
openclaw config get gateway.auth.token
```

Güvenli bir yerde saklayın.

---

## 3) Her İki Makineye Tailscale Kurma ve Yetkilendirme

Tailscale belgeleri: [Serve overview](https://tailscale.com/kb/1312/serve), [Serve CLI](https://tailscale.com/docs/reference/tailscale-cli/serve) ve [Device approval](https://tailscale.com/kb/1099/device-approval).

### 3.1 Tailscale'i Kur

**Makine A** ve **Makine B**'ye resmi yükleyicileri kullanarak Tailscale'i kurun: [Tailscale downloads](https://tailscale.com/download).

### 3.2 Her iki makineyi aynı tailnet'e katın

Her makinede:

```bash
tailscale up
tailscale status
```

Her iki makinenin de aynı tailnet'te göründüğünü doğrulayın.

### 3.3 Tailnet'iniz onay gerektiriyorsa cihazları onaylayın

Tailscale yönetiminde:

1. [Machines](https://login.tailscale.com/admin/machines) sayfasını açın.
2. **Needs approval** olarak işaretlenmiş cihazları bulun.
3. Makine A ve Makine B'yi onaylayın.

Bu yapılmadan makineler tailnet trafiği üzerinden iletişim kuramaz.

---

## 4) Makine A'da OpenClaw Gateway'i Tailscale Üzerinden Yayınlama

İki geçerli yöntem vardır. Birini seçin.

### Seçenek A (basit ve açık): Tailscale Serve komutu

**Makine A**'da Gateway'i yerel olarak bağlı tutun (`127.0.0.1:18789`) ve Serve aracılığıyla yayınlayın:

```bash
tailscale serve --yes --bg --https=443 http://127.0.0.1:18789
tailscale serve status
```

Notlar:

- Daha yeni Tailscale CLI `--https=443` kullanır.
- Eski belgeler/komutlar üzerindeyseniz `--https 443` gibi bir sözdizimi görebilirsiniz. Kurulu sürümünüzde `tailscale serve --help` kullanın.

### Seçenek B (OpenClaw tarafından yönetilen Tailscale modu)

OpenClaw, Tailscale modunu kendisi yönetebilir:

```bash
openclaw gateway --tailscale serve
```

OpenClaw Tailscale belgeleri: [Gateway Tailscale](https://docs.openclaw.ai/gateway/tailscale.md).

### 4.1 Genel tailnet URL'sini doğrulayın

`https://<gateway-sunucusu>.<tailnet>.ts.net` sunucu adresine ihtiyacınız var.

Claw3D'nin `wss://<gateway-sunucusu>.<tailnet>.ts.net` olarak kullanacağı adres budur.

---

## 5) Makine B'ye Claw3D Kurma ve Çalıştırma

**Makine B**'de:

```bash
git clone https://github.com/iamlukethedev/Claw3D.git claw3d
cd claw3d
npm install
cp .env.example .env
npm run dev
```

Ardından şu adresi açın:

- `http://localhost:3000`

---

## 6) Claw3D'yi OpenClaw'a Bağlama

Claw3D bağlantı UI'ında:

1. **Gateway URL'sini** şu şekilde ayarlayın:
   - `wss://<gateway-sunucusu>.<tailnet>.ts.net`
2. Makine A'dan aldığınız token'ı yapıştırın (`openclaw config get gateway.auth.token`).
3. **Bağlan**'a tıklayın.

Önemli:

- Tailscale HTTPS endpoint'leri için `wss://` kullanın.
- `ws://localhost:18789` yalnızca Gateway, Claw3D ile aynı makinedeyken veya SSH tüneli kullanılırken kullanılmalıdır.

---

## 7) Gerekli Cihaz Eşleştirme Onayı Adımı

İnsanların sıklıkla atlattığı adım budur.

Claw3D çalışıyor ve ilk kez bağlanmaya çalıştıktan sonra **Makine A**'da bekleyen cihaz eşleştirmeyi onaylayın:

```bash
openclaw devices list
openclaw devices approve --latest
```

OpenClaw cihazları belgeleri: [openclaw devices](https://docs.openclaw.ai/cli/devices.md).

Birden fazla istek beklemedeyse bunun yerine kimliğe göre onaylayın:

```bash
openclaw devices approve <requestId>
```

---

## 8) Doğrulama Kontrol Listesi

Bu kontrol listesini sırayla uygulayın:

1. Makine A'da `openclaw gateway status` sağlıklı runtime gösterir.
2. Her iki makinede `tailscale status`, aynı tailnet'teki bağlı cihazları gösterir.
3. Makine A'da `tailscale serve status`, `443` portunda `127.0.0.1:18789`'a yönelik aktif Serve yapılandırmasını gösterir.
4. Claw3D bağlantı UI'ı `wss://...ts.net` ve geçerli token kullanır.
5. İlk bağlantı girişiminden sonra `openclaw devices approve --latest` çalıştırıldı.
6. Claw3D UI'ı gateway'in bağlı olduğunu gösterir ve agent'ları yükler.

---

## 9) Sorun Giderme

### `EPROTO` veya `wrong version number`

- Genellikle protokol uyumsuzluğu anlamına gelir.
- Düzeltme: endpoint'iniz HTTPS/Tailscale Serve ise `wss://...` kullanın.
- Düz `ws://` endpoint'ine karşı `wss://` kullanmayın.

### Claw3D'den `401` veya kimlik doğrulama hataları

- Token'ı Makine A'dan yeniden kopyalayın:
  - `openclaw config get gateway.auth.token`.
- Gateway kimlik doğrulama modunu ve token'ın güncel olduğunu doğrulayın.

### Token doğru olmasına rağmen Claw3D hâlâ bağlanamıyor

- Bekleyen cihazı onaylayın:
  - `openclaw devices approve --latest`.
- Bekleyen istekleri kontrol edin:
  - `openclaw devices list`.

### Tailscale URL hiçbir yerde çalışmıyor

- Cihaz onayı etkinse her iki cihazın da Tailscale yönetiminde onaylandığını doğrulayın.
- Şunları yeniden çalıştırın:
  - `tailscale status`.
  - `tailscale serve status`.
- Gerekirse serve yapılandırmasını yeniden oluşturun:
  - `tailscale serve reset`.
  - `tailscale serve --yes --bg --https=443 http://127.0.0.1:18789`.

### Gateway'in kendisi sağlıksız

- Şunları çalıştırın:
  - `openclaw doctor`.
  - `openclaw gateway restart`.
  - `openclaw gateway status`.

---

## 10) Güvenlik Notları

- Kasıtlı bir nedeniniz olmadıkça Gateway'i loopback'e bağlı tutun.
- Token'ları git'e veya paylaşmak amacıyla tasarlanmış `.env` dosyalarına commit etmeyin.
- Ham Gateway portlarını herkese açık olarak dışarıya açmak yerine Tailscale Serve'i tercih edin.
- OpenClaw cihaz eşleştirme onayını tek seferlik bir rahatsızlık olarak değil, bir güvenlik kapısı olarak değerlendirin.

---

## Referanslar

- OpenClaw kurulum: [docs.openclaw.ai/install/index.md](https://docs.openclaw.ai/install/index.md).
- OpenClaw başlarken: [docs.openclaw.ai/start/getting-started.md](https://docs.openclaw.ai/start/getting-started.md).
- OpenClaw gateway kılavuzu: [docs.openclaw.ai/gateway/index.md](https://docs.openclaw.ai/gateway/index.md).
- OpenClaw cihazlar CLI: [docs.openclaw.ai/cli/devices.md](https://docs.openclaw.ai/cli/devices.md).
- OpenClaw tailscale gateway modu: [docs.openclaw.ai/gateway/tailscale.md](https://docs.openclaw.ai/gateway/tailscale.md).
- Tailscale Serve: [tailscale.com/kb/1312/serve](https://tailscale.com/kb/1312/serve).
- Tailscale serve CLI: [tailscale.com/docs/reference/tailscale-cli/serve](https://tailscale.com/docs/reference/tailscale-cli/serve).
- Tailscale cihaz onayı: [tailscale.com/kb/1099/device-approval](https://tailscale.com/kb/1099/device-approval).
