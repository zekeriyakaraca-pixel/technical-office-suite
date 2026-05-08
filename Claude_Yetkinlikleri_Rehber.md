# Claude ve Codex Yetkinlikleri Rehberi

Bu projede aktif hedef, Claude/agent ergonomisini Codex CLI uyumlu Technical Office Runtime etrafinda toplamak.

## Aktif Skill Yaklasimi

- Kaynak teknik ofis skill notlari: `agents/_shared/skills/*.md`
- Codex uyumlu paketler: `agents/_shared/codex-skills/<slug>/SKILL.md`
- Runtime loader, ayni skill icin Codex paketi varsa `SKILL.md` dosyasini tercih eder.

## Oncelikli Proje Skill'leri

| Skill | Paket |
| --- | --- |
| PDF poz okuma | `agents/_shared/codex-skills/pdf-poz-okuma/SKILL.md` |
| Plaka geometri cikarma | `agents/_shared/codex-skills/plaka-geometri-cikarma/SKILL.md` |
| DXF 2013 uretimi | `agents/_shared/codex-skills/dxf-2013-uretimi/SKILL.md` |
| Cizim/NC kalite kontrolu | `agents/_shared/codex-skills/cizim-nc-kalite-kontrolu/SKILL.md` |
| ERT partlist Excel uretimi | `agents/_shared/codex-skills/ert-partlist-excel-uretimi/SKILL.md` |

## Kullanilacak Komutlar

```powershell
.\scripts\toffice.ps1 doctor
.\scripts\codex-mcp.ps1
uv run --project runtime --extra dev pytest runtime\tests -q
uv run --project mcp\autocad-mcp-server --extra dev pytest mcp\autocad-mcp-server\tests\test_technical_office_pipeline.py -q
```

## Uyum Kurallari

- Codex CLI aktif agent motorudur.
- Harici PDF vision provider veya yerel model kurulum talimati varsayilmaz.
- `.mcp.json` Claude/MCP istemcileri icin referanstir; Codex CLI global MCP config ayrica doctor ile kontrol edilir.
- Legacy `apps/office3d` skill ve Next.js kurallari aktif runtime hedefi degildir.
