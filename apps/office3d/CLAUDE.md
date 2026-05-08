# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Bu Proje Nedir?

**Claw3D** — AI agent'larının gerçek zamanlı görselleştirildiği 3D ofis ortamı sunan Next.js web uygulaması. Taşıyıcı sistem OpenClaw (veya Hermes/Demo) gateway'dir; Claw3D yalnızca görselleştirme ve UI katmanıdır.

## Komutlar

```bash
npm run dev              # Geliştirme sunucusu (http://localhost:3000)
npm run dev:https        # HTTPS ile başlat (self-signed sertifika)
npm run build            # Production build
npm run start            # Production sunucusu

npm run lint             # ESLint
npm run typecheck        # TypeScript tip kontrolü
npm run test             # Vitest birim testleri
npm run e2e              # Playwright E2E testleri (önce: npx playwright install)

npm run demo-gateway     # OpenClaw olmadan mock gateway başlat (ws://localhost:18789)
npm run hermes-adapter   # Hermes WebSocket adaptörü
```

## Mimari

### Sistem Sınırları

```
Tarayıcı UI (Next.js)
    ↓ HTTP / WebSocket
Studio API Rotaları + WebSocket Proxy (/api/gateway/ws)
    ↓ WebSocket (upstream)
OpenClaw Gateway  ←  agent kayıtları, oturumlar, onaylar, runtime olayları
```

- **Gateway kaynağı sahiplenir** — Claw3D hiçbir zaman kendi yerel state'ini tutmaz; her şey gateway'den türetilir.
- Tarayıcı, Studio proxy'si üzerinden bağlanır (`server/gateway-proxy.js`). Kimlik bilgileri sunucu tarafında kalır.

### Dizin Yapısı — Büyük Resim

| Yol | Amaç |
|-----|-------|
| `server/` | Özel Node.js sunucusu — Next.js handler, WebSocket proxy, erişim denetimi |
| `src/app/` | Next.js App Router rotaları ve API endpoint'leri |
| `src/features/` | Dikey özellik dilimleri — her özelliğin kendi state/component/hook'ları |
| `src/lib/` | Özellikler arası paylaşılan domain mantığı ve adaptörler |
| `src/components/` | Paylaşılan UI bileşenleri |

### Kritik Özellik Dilimleri

- **`src/features/agents/`** — Agent filosu UI, sohbet, onaylar, state iş akışları
- **`src/features/retro-office/`** — React Three Fiber tabanlı 3D ofis sahnesi
- **`src/features/office/`** — Ofis ekranları ve Phaser tabanlı ofis düzenleyici
- **`src/features/company-builder/`** — Şirket/çalışma alanı kurulum akışları

### Olay Akışı (State Yönetimi)

```
Gateway olayı gelir
    → gatewayRuntimeEventHandler.ts  (sınıflandırır ve yönlendirir)
    → runtimeChatEventWorkflow.ts    (sohbet akışı)
    → runtimeAgentEventWorkflow.ts   (agent yaşam döngüsü)
    → runtimeEventCoordinatorWorkflow.ts  (store'a uygular)
    → historySyncOperation.ts        (eksik akışları tamamlar)
    → UI & 3D ofis tüketir
```

### 3D Ofis Animasyon Türetme

Animasyonlar hiçbir zaman doğrudan tetiklenmez — runtime olaylarından türetilir:
1. `src/lib/office/eventTriggers.ts` → olaylardan animasyon "hold"ları türetir
2. `src/lib/office/deskDirectives.ts` → doğal dil yönergelerini ayrıştırır
3. `src/features/retro-office/RetroOffice3D.tsx` → hold'ları 3D destinasyonlara eşler

## Önemli Dosyalar

| Dosya | Rol |
|-------|-----|
| `server/index.js` | Sunucu başlangıç noktası |
| `server/gateway-proxy.js` | Tarayıcı ↔ gateway WebSocket köprüsü |
| `src/app/office/page.tsx` | Ana 3D ofis rotası |
| `src/features/agents/state/store.tsx` | Merkezi agent/oturum state store'u |
| `src/features/retro-office/RetroOffice3D.tsx` | Ana 3D sahne bileşeni |
| `src/lib/gateway/GatewayClient.ts` | Gateway taşıma istemcisi |
| `src/lib/runtime/createRuntimeProvider.ts` | Runtime adaptörü fabrikası (OpenClaw/Hermes/Demo/Custom) |
| `src/lib/studio/settings-store.ts` | Yerel ayarlar (`~/.openclaw/claw3d/settings.json`) |

## Çevre Değişkenleri

```bash
CLAW3D_GATEWAY_URL=ws://localhost:18789   # Runtime gateway URL (yeniden build gerekmez)
CLAW3D_GATEWAY_TOKEN=                    # Runtime token
NEXT_PUBLIC_GATEWAY_URL=                 # Build-time URL (değişince build şart)
STUDIO_ACCESS_TOKEN=                     # Halka açık deploy'larda zorunlu
ELEVENLABS_API_KEY=                      # Ses özellikleri için
PORT=3000
```

## Mimari Kurallar (`.cursor/rules/` içinden)

- **Gateway-first**: Claw3D hiçbir zaman gateway'in yerine geçmez; yalnızca okur ve görselleştirir.
- **Türetilmiş state**: UI durumu gateway olaylarından türetilir, doğrudan mutasyon yapılmaz.
- **Özellik-önce organizasyon**: Özellik mantığı `src/features/` içinde kalır; yalnızca gerçekten paylaşılan kod `src/lib/`'e taşınır.
- **Sunucu sınırları**: Dosya sistemi erişimi, SSH, token işlemleri yalnızca sunucu tarafında.
- **Ofis niyet katmanı**: Tüm doğal dil yönergeleri `deskDirectives.ts` üzerinden geçer.
- Yeni ofis odaları/davranışları eklerken `eventTriggers.ts` ve `deskDirectives.ts`'i birlikte güncelle.

## Testler

- **Birim testleri** (`tests/unit/`): Vitest + jsdom — build gerekmez
- **E2E testleri** (`tests/e2e/`): Playwright — `http://127.0.0.1:3000` karşısında çalışır, dev sunucusunu otomatik başlatır
- Tek test dosyası: `npm run test -- tests/unit/deskDirectives.test.ts`

## Sık Karşılaşılan Sorunlar

- `EPROTO` → `wss://` yerine `ws://` kullan (TLS'siz endpoint için)
- `401 Studio access token required` → `STUDIO_ACCESS_TOKEN` ayarlı ama cookie eksik
- `NEXT_PUBLIC_GATEWAY_URL` değiştirdikten sonra mutlaka `npm run build` çalıştır
- OpenClaw yoksa geliştirme için `npm run demo-gateway` kullan
