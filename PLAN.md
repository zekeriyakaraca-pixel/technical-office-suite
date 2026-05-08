# Technical Office Codex Runtime Plan

## Summary
Active product target is the FastAPI runtime in `runtime/technical_office_runtime` plus the deterministic AutoCAD MCP technical office pipeline. The legacy `apps/office3d` application stays parked, and no new frontend app is created under `apps/`.

Codex CLI is the only active AI agent engine. PDF diagnostics, DXF, NC1, QC, and ERT partlist generation remain deterministic Python pipeline work. Codex CLI is used for substantive manager chat and visual PDF candidate extraction after local PDF pages are rendered to images; lightweight greetings/status prompts are answered locally to keep the dashboard responsive.

## Active Architecture
- Runtime API and dashboard: `runtime/technical_office_runtime`
- AutoCAD/headless production pipeline: `mcp/autocad-mcp-server/src/autocad_mcp/technical_office`
- Workspace input: `workspace/imports/jobs/<job_id>/`
- Workspace output: `workspace/outputs/jobs/<job_id>/`
- Codex run state: `.state/codex-runs/`
- Codex-compatible skills: `agents/_shared/codex-skills/<skill>/SKILL.md`

## Key Changes
- Keep the current FastAPI dashboard and extend it with PDF preview, live event stream, editable visual candidates, output downloads, QC status, and partlist generation.
- Use `PyMuPDF` in the runtime to render unreadable PDF pages before Codex visual candidate extraction.
- Stream `codex.cmd exec --json` output into job events while Codex runs.
- Keep `/api/events/{job_id}` as a server-sent event stream that replays existing events and tails new ones until completion or failure.
- Add `/api/jobs/{job_id}/partlist` to create ERT Excel only from QC `ok=true` rows.
- Use `scripts/toffice.ps1 doctor` and `scripts/codex-mcp.ps1` to validate Codex CLI and detect stale global `autocad-mcp` paths.

## API Surface
- `POST /api/jobs`: upload one or more PDFs and create a job.
- `GET /api/jobs`: list jobs and status.
- `GET /api/jobs/{job_id}`: inspect metadata, PDFs, diagnostics, candidates, approvals, outputs, partlist, and events.
- `GET /api/jobs/{job_id}/files/{filename}`: download input PDFs or output files.
- `POST /api/jobs/{job_id}/run`: run deterministic diagnostics/pipeline and Codex visual candidate extraction when required.
- `POST /api/jobs/{job_id}/approve-candidates`: write manager-approved `PlateSpec` rows and run production.
- `POST /api/jobs/{job_id}/partlist`: create or block the ERT partlist based on QC/manual-review gates.
- `GET /api/events/{job_id}`: live SSE job event stream.
- `POST /api/manager/chat`: manager-only chat with local lightweight responses and Codex CLI for substantive reasoning.

## Codex CLI Compatibility
Codex CLI does not use this repo's `.mcp.json` as its source of truth. It reads Codex global MCP configuration. The runtime therefore checks:

```powershell
codex.cmd --version
codex.cmd exec --help
codex.cmd mcp list
codex.cmd mcp get autocad-mcp
```

If `autocad-mcp` points to an old machine-specific path, the doctor prints manual fix commands. Global Codex config is not changed automatically.

## Test Plan
- Runtime tests: `uv run --project runtime --extra dev pytest runtime/tests -q`
- MCP pipeline tests: `uv run --project mcp/autocad-mcp-server --extra dev pytest mcp/autocad-mcp-server/tests/test_technical_office_pipeline.py -q`
- Smoke check: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke.ps1`
- Doctor check: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\toffice.ps1 doctor`

Acceptance criteria:
- Runtime and MCP tests pass.
- Codex executable, login, and `codex exec` help are ready.
- Codex MCP doctor rejects stale `Technical_office_engineer` paths.
- PDF visual candidate rendering works through `PyMuPDF`.
- Partlist is blocked until QC `ok=true` rows exist.
