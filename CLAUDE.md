# CLAUDE.md - Technical Office Suite

This file is the operating guide for agents working in this repository.
Last checked against the codebase: 2026-05-07.

## Current Project Status

The active product is the Technical Office Codex Runtime. It is a local FastAPI
runtime with a built-in 2D dashboard, a Codex CLI manager bridge, and a
deterministic PDF -> DXF/NC1/QC/partlist production pipeline.

| Area | Path | Current role |
| --- | --- | --- |
| Runtime API/dashboard | `runtime/technical_office_runtime/` | FastAPI app (`app.py`, version `0.2.0`) and built-in 2D dashboard (`static/index.html`) |
| Technical office pipeline | `mcp/autocad-mcp-server/src/autocad_mcp/technical_office/` | PDF diagnostics, deterministic extraction, DXF, NC1, QC, and ERT partlist logic |
| Agents and skills | `agents/` | Manager/specialist instructions, registry, and Codex-ready skill packages |
| Workspace | `workspace/` | Local job imports, outputs, sessions, memory, and audit files |
| Scripts | `scripts/` | Local startup, smoke, CLI, batch, and MCP doctor helpers |
| Legacy UI | `apps/office3d/` | Parked reference app only; not the active runtime target |

The active dashboard URL after startup is:

```text
http://127.0.0.1:7770/
```

## Startup Rules

Always run startup commands from the repository root.

Primary local startup:

```powershell
.\scripts\suite.ps1 start
```

Primary verification after startup:

```powershell
.\scripts\suite.ps1 status
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:7770/health
```

Useful operations:

```powershell
.\scripts\suite.ps1 logs -Tail 80
.\scripts\suite.ps1 restart
.\scripts\suite.ps1 stop
```

Important startup behavior:

- `suite.ps1 start` creates `.state/suite`, `.state/suite/logs`, `.state/uv-cache`, and `.state/pytest-tmp`.
- `suite.ps1 start` stops stale suite-owned listeners before starting the runtime.
- The runtime is launched through `scripts/runtime-server.ps1`, which sets `TECH_OFFICE_SUITE_ROOT`, `UV_CACHE_DIR`, `TEMP`, `TMP`, and `PYTHONPATH`.
- The runtime listens on `127.0.0.1:7770` by default. Use `-RuntimePort <port>` only when the user explicitly wants another port.
- `suite.ps1 status` reads `.state/suite/processes.json` and also checks the listening TCP port. The saved launcher PID and the actual port PID can differ; the important signal is `alive=True` and a listening `port PID`.
- If port `7770` is occupied by a process that is not part of this suite, the script refuses to kill it unless `-Force` is supplied. Use `-Force` only after confirming that stopping that process is intended.
- If a sandboxed or automated shell cannot keep the hidden Windows background process alive, rerun the same `suite.ps1 start` command in an approved/out-of-sandbox shell instead of inventing a second startup path.

Validation and smoke:

```powershell
uv run --project runtime --extra dev pytest runtime\tests -q
uv run --project mcp\autocad-mcp-server --extra dev pytest mcp\autocad-mcp-server\tests\test_technical_office_pipeline.py -q
.\scripts\smoke.ps1
```

Smoke test warning:

- `scripts/smoke.ps1` starts its own runtime on port `7770` and stops it in `finally`.
- Do not run `smoke.ps1` while the user expects the dashboard to stay online.
- If smoke is run, start the suite again afterward with `.\scripts\suite.ps1 start`.

Docker startup:

```powershell
docker compose up -d
docker compose logs -f runtime
docker compose down
```

Docker exposes runtime on `7770`, nginx on `80`, and uses the `autocad-mcp`
service with the `ezdxf` backend.

## Runtime Modules

| Module | Path | Purpose |
| --- | --- | --- |
| Job FSM | `runtime/technical_office_runtime/job_fsm.py` | 10-state explicit FSM (`uploaded` to `completed`/`failed`), persisted per job as `fsm_state.json` |
| Retry | `runtime/technical_office_runtime/retry.py` | Async/sync retry with exponential backoff and Codex timeout handling |
| Memory Bridge | `runtime/technical_office_runtime/memory_bridge.py` | SQLite cross-job pattern learning and PDF fingerprint cache |
| Background Workers | `runtime/technical_office_runtime/workers.py` | Post-extraction pre-validation, memory update, and consensus checks |
| Auth | `runtime/technical_office_runtime/auth.py` | HMAC-SHA256 bearer tokens via `TOFFICE_API_SECRET`; open local mode when unset |
| Metrics | `runtime/technical_office_runtime/metrics.py` | Pure-Python in-memory counters/histograms and Prometheus text output |
| Audit | `runtime/technical_office_runtime/audit.py` | Append-only JSONL audit trail under `workspace/audit/audit_trail.jsonl` |
| SLA Monitor | `runtime/technical_office_runtime/sla.py` | 24 h assignment / 72 h completion monitoring |
| Sessions | `runtime/technical_office_runtime/session_store.py` | Persistent manager and OpenAI-compatible chat session history |
| Manager Memory | `runtime/technical_office_runtime/manager_memory.py` | Persistent SQLite manager brain for job facts, decisions, recent turns, and Markdown vault export |

## Runtime Workflow

1. Create a job by uploading one or more PDFs through the dashboard or `POST /api/jobs`.
2. Run the pipeline with the dashboard `Pipeline Calistir` button or `POST /api/jobs/{job_id}/run`.
3. Deterministic diagnostics classify the PDFs and produce normal outputs when the drawing is machine-readable.
4. If visual reading is required, runtime renders PDF pages with PyMuPDF and calls `codex.cmd exec --json` for candidate extraction.
5. Codex and pipeline events are streamed into `workspace/outputs/jobs/<job_id>/events.jsonl`.
6. The manager reviews, edits, and approves candidates in the dashboard.
7. Approved candidates write `approved_plate_specs.json` and trigger deterministic DXF/NC1/QC production.
8. Partlist is generated only after the QC gate allows it.

## Dashboard UI Guide

The dashboard is served from `runtime/technical_office_runtime/static/index.html`.
It is a single-page local operations console for creating jobs, running the
pipeline, approving candidates, reading diagnostics, downloading outputs, and
talking to the `teknik-ofis-muduru` manager agent.

### Header and status bar

- `Technical Office 2D Runtime` identifies the active app.
- Status pills are refreshed every 30 seconds.
- `Hazir`/status comes from `/api/health`.
- `Aktif is` and `Toplam` come from the current job snapshot.
- `Bellek kaliplari` and `Ort. guven` come from `/api/memory/stats`.
- `Uptime` comes from `/api/metrics`.

### Left panel: new job and job list

- `Proje adi` is required and becomes the human-readable project name.
- `Job ID` is optional. If left blank, the API generates a safe ID from the project name and timestamp.
- `PDF dosyalari` accepts one or more PDF files only.
- `Yukle` posts a multipart request to `/api/jobs`.
- `Yenile` reloads the list from `/api/jobs`.
- `Isler` shows all known jobs and their FSM status pills.
- Clicking a job selects it, loads `/api/jobs/{job_id}`, opens the first PDF preview, renders candidates/outputs, and starts the event stream.

FSM labels shown in the UI:

| FSM state | UI meaning |
| --- | --- |
| `uploaded` | Uploaded, not yet processed |
| `classifying` | Diagnostics/classification running |
| `classified` | Classified and ready for the next step |
| `extracting` | Codex visual candidate extraction running |
| `awaiting_approval` | Manager approval is required |
| `producing` | DXF/NC1/QC production running |
| `qc_checking` | QC stage running |
| `completed` | Pipeline completed |
| `failed` | Pipeline failed |
| `retrying` | Retry in progress |

### Job detail actions

- `Pipeline Calistir` calls `POST /api/jobs/{job_id}/run` with `autocad_live_policy: "off"` from the UI.
- `Secili Adaylari Onayla` is enabled only when Codex candidates exist. It sends edited selected rows to `POST /api/jobs/{job_id}/approve-candidates`.
- `Partlist Uret` calls `POST /api/jobs/{job_id}/partlist`. The backend still enforces QC/manual-review gates even if the button is visible.
- Do not rerun a job while it is in `classifying`, `extracting`, `producing`, `qc_checking`, or `retrying`; the API intentionally returns an in-progress error.

### PDF Onizleme

- The PDF buttons are generated from `job.pdfs`.
- Clicking a PDF button changes the iframe preview source to the inline preview URL (`?inline=true`).
- The PDF file-name link is the download action; approval buttons must not trigger a PDF download.
- The first uploaded PDF is previewed automatically when a job is selected.

### Adaylar

- This panel shows Codex visual extraction candidates from `job.codex_candidates.candidates`.
- Each row can be selected or deselected before approval.
- Editable fields are `poz_no`, `width`, `height`, `thickness`, `material`, and `quantity`.
- The confidence column is color-coded: high confidence is green, medium is amber, low is red.
- If deterministic candidates and Codex candidates both exist and their average confidence differs by more than `0.3`, the UI shows a conflict warning. Review and edit before approval.
- Approval writes manager-approved plate specs and then triggers deterministic production.
- Visual/unreadable PDFs must have page coverage before approval. If a PDF has 26 visual pages and only pages 1-3 have approved candidates, production must remain blocked.
- Candidates whose evidence mentions polygon, side offset, chamfer/pah, cugul, or corner relief cannot be approved with empty `corner_reliefs`.

### Ciktilar ve QC

- Output files are read from `job.files` where `group == "output"`.
- Each output row includes relative path, byte size, and an `indir` download link.
- Partlist status is shown when `job.partlist` exists.
- Common output groups include DXF, NC1, QC JSON, rendered previews, candidate JSON, and partlist workbooks.

### Diagnostics

- Shows `job.diagnostics` when available; otherwise shows `job.summary`.
- Use this panel to understand why a PDF was automatically produced, blocked, or sent to manager approval.

### Canli Event Stream

- Uses `EventSource` against `/api/events/{job_id}`.
- Existing events are replayed first from the selected job detail.
- Heartbeats are ignored in the UI.
- The stream closes when a `completed` or `failed` event arrives, or when the server-side stream times out.

### Mudur Chat

- Uses `POST /api/manager/chat`.
- Only `teknik-ofis-muduru` is enabled through this endpoint.
- The selected job ID is sent as context, so questions like "bu isin durumunu ozetle" are answered with job context when a job is selected.
- The browser keeps recent chat history and also uses the persistent dashboard session `agent:teknik-ofis-muduru:dashboard`.
- Before answering, the backend recalls relevant manager memory from `workspace/memory/manager_memory.db`.
- Manager memory stores job-scoped facts, restart intents, manager decisions, recent turns, tags, PDF names, and poz numbers.
- Job memory is exported as readable Markdown under `workspace/manager_vault/jobs/<job_id>.md`; Obsidian can open this folder, but the runtime source of truth is SQLite.
- Generic job-list responses must not become the active job memory. "Bu is" should bind to an explicit selected job, an explicit job ID, or a prior job-specific turn.
- The UI timeout is 210 seconds so longer Codex CLI manager work can finish without the browser aborting early.
- Lightweight greetings, capability/status prompts, selected-job issue discussion, and safe job actions can be answered locally.
- General substantive conversation uses Codex CLI in read-only mode.
- Explicit project/code/file edit requests use Codex CLI in workspace-write mode with a longer timeout, then report changed files and verification.

### Dashboard auth note

- When `TOFFICE_API_SECRET` is unset, protected write endpoints run in open local mode.
- When `TOFFICE_API_SECRET` is set, protected endpoints require `Authorization: Bearer <token>`.
- The current dashboard does not expose a token input. In token-required mode, use an API client or add UI token support before relying on browser writes.

## Manager Chat and Agent Behavior

- The manager chat is session-backed and selected-job aware.
- The manager has a persistent memory layer in addition to short session history. It records every manager turn, extracts job IDs, PDF names, poz numbers, tags, issue facts, restart intents, and manager decisions.
- Memory recall is injected into manager prompts as `Mudur Hafizasi` before Codex CLI is called and is also used by local handlers for references such as "bu is".
- Raw chat memory must not be promoted directly into agent skill files. Only verified repeated rules should become `agents/*/MEMORY.md` or shared Codex skills.
- General substantive conversation uses Codex CLI in read-only mode.
- Explicit project/code/file edit requests use Codex CLI in workspace-write mode.
- If Codex CLI times out after producing a useful partial manager analysis, the dashboard response should show that partial analysis with a timeout note instead of replacing it with a generic failure.
- Safe explicit actions such as job listing, job run, QC lookup, partlist creation, and agent draft creation are routed through local tools.
- Selected-job production problems such as missing pages, wrong poz counts, missing chamfers, or polygon/contour issues are recorded as manager issue notes instead of being mistaken for a rerun command.
- New agents are created as drafts under `agents/_drafts`; activation still requires explicit approval.
- The manager must not tell users to enable an alternate vision provider or bypass deterministic QC gates.

## API Surface

### Core job API

- `POST /api/jobs` - upload PDFs as a new job; auth required when `TOFFICE_API_SECRET` is set.
- `GET /api/jobs` - list jobs.
- `GET /api/jobs/{job_id}` - inspect diagnostics, candidates, approvals, outputs, partlist, events, and `fsm_state`.
- `GET /api/jobs/{job_id}/files/{filename}` - download input or output files.
- `POST /api/jobs/{job_id}/run` - run diagnostics/pipeline; idempotent guard blocks active jobs.
- `POST /api/jobs/{job_id}/approve-candidates` - manager approval; auth required when enabled.
- `POST /api/jobs/{job_id}/partlist` - create or block ERT partlist; auth required when enabled.
- `GET /api/events/{job_id}` - server-sent event stream.
- `POST /api/manager/chat` - dashboard manager chat.
- `GET /api/manager/memory` - inspect persistent manager memory; accepts optional `job_id`, `session_id`, and `limit`.

### Observability and runtime API

- `GET /health` - basic liveness and runtime metadata.
- `GET /api/health` - JSON health for the dashboard.
- `GET /metrics` - Prometheus text exposition.
- `GET /api/metrics` - JSON metrics summary.
- `GET /state` - active agent/runtime state snapshot.
- `GET /registry` - runtime registry/model metadata.
- `GET /status` - combined runtime, sessions, agents, and jobs status.

### Sessions and OpenAI-compatible API

- `GET /sessions`
- `GET /sessions/history`
- `POST /sessions/preview`
- `POST /sessions/reset`
- `POST /v1/chat/completions`

### Memory, audit, and SLA API

- `GET /api/memory/stats`
- `GET /api/memory/patterns`
- `GET /api/manager/memory`
- `GET /api/audit`
- `GET /api/audit/{job_id}`
- `GET /api/sla/report`
- `GET /api/sla/overdue`

## Local CLI

```powershell
.\scripts\toffice.ps1 doctor
.\scripts\codex-mcp.ps1
.\scripts\toffice.ps1 ask "test-001 isini AutoCAD live kapali calistir ve QC ozetle"
.\scripts\toffice.ps1 job run test-001 --autocad off
.\scripts\toffice.ps1 agent draft "Tekla DXF kontrol ajani"
```

`doctor` checks the local `codex.cmd` executable, ChatGPT login state,
`codex.cmd exec` readiness, and whether Codex CLI's global `autocad-mcp`
entry points to this repository.

## Codex CLI and MCP

Codex CLI does not use `.mcp.json` as its source of truth. Use:

```powershell
.\scripts\codex-mcp.ps1
```

The doctor checks:

- `codex.cmd --version`
- `codex.cmd exec --help`
- `codex.cmd mcp list`
- `codex.cmd mcp get autocad-mcp`

If `autocad-mcp` points to an old `Technical_office_engineer` path, the doctor
prints manual fix commands. Do not edit Codex global MCP config automatically.

## Non-Negotiable Rules

- Codex CLI is the only active AI agent engine.
- Do not add alternate AI engines or external PDF vision provider paths.
- Do not create a new frontend app under `apps/`; extend the existing FastAPI runtime dashboard.
- Do not use `apps/office3d` as a default validation or development target.
- Do not edit Codex global MCP config automatically. Detect stale config and show manual commands.
- Do not produce DXF/NC1 from visual candidates until `teknik-ofis-muduru` approval writes `approved_plate_specs.json`.
- Do not create partlist/delivery outputs unless QC rows are `ok=true`.
- Do not bypass the FSM by writing `fsm_state.json` directly; always use `JobFSM.transition()` or `force_transition()`.
- Do not re-run a job while it is in an active FSM state (`classifying`, `extracting`, `producing`, `qc_checking`, `retrying`).
- Do not add `prometheus_client` or `aiosqlite` as dependencies; metrics use pure Python and memory bridge uses stdlib `sqlite3`.

## Development Notes

- Keep runtime edits scoped to `runtime/technical_office_runtime`.
- Keep deterministic production logic in `mcp/autocad-mcp-server/src/autocad_mcp/technical_office`.
- Keep dashboard edits in `runtime/technical_office_runtime/static/index.html`.
- Use `agents/_shared/codex-skills/<slug>/SKILL.md` for Codex-ready skill packages.
- Keep old flat skill markdown files as source/reference unless a migration explicitly removes them.
- If generated output or session history contains stale model/provider advice, sanitize the visible text instead of preserving misleading instructions.
- Outputs are written under `workspace/outputs/jobs/<job_id>/`.
- Inputs are stored under `workspace/imports/jobs/<job_id>/`.
- Session history is stored under `workspace/sessions/`.
- Manager memory is stored under `workspace/memory/manager_memory.db`.
- Manager Markdown vault exports are stored under `workspace/manager_vault/`.

## Parked 3D Office

`apps/office3d` is retained for reference only. Work there only when the user
explicitly asks for legacy 3D office maintenance.
