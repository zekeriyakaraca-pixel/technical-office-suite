# CLAUDE.md - Technical Office Suite

This file is the operating guide for agents working in this repository.
Last checked against the codebase: 2026-05-20 (session 10 — manager chat stability: selected-job issue reports now stay deterministic instead of being rewritten by Gemini; `isle ilgili` is not treated as a run command; DXF/NC1 artifact geometry corrections such as hole coordinate fixes are local manager actions, not project/code edits; `_looks_like_hole_coordinate_correction_request()` + `_handle_hole_coordinate_correction()` patch approved specs, rerun production/QC, and resolve `delik koordinati` notes; 172 runtime tests and 190 MCP tests pass).

## Current Project Status

The active product is the Technical Office Codex Runtime. It is a local FastAPI
runtime with a built-in 2D dashboard, a Codex CLI manager bridge, and a
deterministic PDF -> DXF/NC1/QC/partlist production pipeline.

| Area | Path | Current role |
| --- | --- | --- |
| Runtime API/dashboard | `runtime/technical_office_runtime/` | FastAPI app (`app.py`, version `0.2.0`) and built-in 2D dashboard (`static/index.html`) |
| Closure and learning finalizer | `runtime/technical_office_runtime/completion.py` | Shared close-out path for QC, partlist, retrospective, memory bridge, skill proposals, manager notification, and learning health/backfill |
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
| Memory Bridge | `runtime/technical_office_runtime/memory_bridge.py` | SQLite cross-job pattern learning, PDF fingerprint cache, and manager page-exclusion decision store (`page_classification_hints` table, keyed by PDF SHA256) |
| Background Workers | `runtime/technical_office_runtime/workers.py` | Post-extraction pre-validation, memory update, and consensus checks |
| Completion Finalizer | `runtime/technical_office_runtime/completion.py` | Single close-out contract for production/QC -> partlist -> retrospective -> memory bridge -> skill proposal -> manager notification |
| Auth | `runtime/technical_office_runtime/auth.py` | HMAC-SHA256 bearer tokens via `TOFFICE_API_SECRET`; open local mode when unset |
| Metrics | `runtime/technical_office_runtime/metrics.py` | Pure-Python in-memory counters/histograms and Prometheus text output |
| Audit | `runtime/technical_office_runtime/audit.py` | Append-only JSONL audit trail under `workspace/audit/audit_trail.jsonl` |
| SLA Monitor | `runtime/technical_office_runtime/sla.py` | 24 h assignment / 72 h completion monitoring |
| Sessions | `runtime/technical_office_runtime/session_store.py` | Persistent manager and OpenAI-compatible chat session history |
| Manager Memory | `runtime/technical_office_runtime/manager_memory.py` | Persistent SQLite manager brain for job facts, decisions, recent turns, and Markdown vault export |
| Agent Context | `runtime/technical_office_runtime/agent_context.py` | Loads agent brain + skill files into system prompt; `load_expert_agent_memories()` reads MEMORY.md and RULES.md from all 4 expert agents and injects them into the manager Codex and Gemini contexts |
| Gemini Bridge | `runtime/technical_office_runtime/gemini_bridge.py` | HTTP client for Google Gemini 2.5 Flash; timeout 90 s; activated by `GEMINI_API_KEY` |
| Chat Detectors | `runtime/technical_office_runtime/chat_detectors.py` | Pure text-pattern functions: all `_looks_like_*`, `_extract_*`, corner-relief parse helpers, and context-marker utilities extracted from `orchestrator.py` (~1 388 lines) |

## Runtime Workflow

1. Create a job by uploading one or more PDFs through the dashboard or `POST /api/jobs`.
2. Run the pipeline with the dashboard `Pipeline Calistir` button or `POST /api/jobs/{job_id}/run`. Before the pipeline starts, the runtime checks the memory bridge for prior manager page-exclusion decisions matching the PDF's SHA256 hash; if found, `page_exclusions.json` is written automatically so the same pages are skipped without requiring a new manager instruction.
3. Deterministic diagnostics classify the PDFs and produce normal outputs when the drawing is machine-readable.
4. If visual reading is required — any `manual_reviews` entry has reason `visual_text_required`, `text_layer_unreadable`, or `plate_geometry_not_found` — the runtime renders PDF pages with PyMuPDF and calls `codex.cmd exec --json` for candidate extraction. Pages listed in `page_exclusions_applied.json` are skipped during rendering so Codex never processes excluded pages. Visual Codex analysis runs automatically before any manager notification or blocking event is emitted.
5. Codex and pipeline events are streamed into `workspace/outputs/jobs/<job_id>/events.jsonl`.
6. The manager reviews, edits, and approves candidates in the dashboard.
7. Approved candidates write `approved_plate_specs.json` and trigger deterministic DXF/NC1/QC production.
8. Successful production enters the shared finalizer: QC gate -> partlist -> retrospective -> memory bridge -> skill proposal -> manager final notification.
9. A terminal `completed` event means the partlist and retrospective chain has also completed. If QC, manager notes, or partlist block the job, the FSM must stay `awaiting_approval`.
10. Successful close-out writes `workspace/outputs/jobs/<job_id>/retrospective.json`, appends `workspace/manager_vault/jobs/<job_id>.md`, records approved specs in `workspace/memory/extraction_patterns.db`, and creates proposal-only learning notes under `journal/skill_proposals/`.

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
- `Isler` shows all known jobs and their FSM status pills. Each job item has a left-border accent color by FSM state: green = completed, amber = awaiting_approval, red = failed, purple = active (producing/classifying/extracting/retrying).
- Clicking a job selects it, loads `/api/jobs/{job_id}`, opens the first PDF preview, renders candidates/outputs, and starts the event stream.
- `Learning Health` calls `/api/learning/health` and shows missing retrospectives, open manager notes, memory pattern count, pending skill proposals, agent registry health, and recent closure events.
- The `Backfill Kontrol` action calls `POST /api/learning/backfill` with `dry_run: true`; real backfill must be explicit through API/client action.

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

Active FSM states (`producing`, `classifying`, `extracting`, `retrying`) show a pulse animation on their status pills.

### Job detail actions

- `Pipeline Calistir` calls `POST /api/jobs/{job_id}/run` with `autocad_live_policy: "off"` from the UI. After the call completes, the button shows an FSM-aware message: `awaiting_approval` → "Görsel adaylar hazır — onay bekliyor."; `completed` → "Pipeline tamamlandı."; `failed` → "Pipeline başarısız oldu."; any other state → "Pipeline çalıştı — durum: <state>".
- `Secili Adaylari Onayla` is enabled only when Codex candidates exist. It sends edited selected rows to `POST /api/jobs/{job_id}/approve-candidates`.
- `Partlist Uret` calls `POST /api/jobs/{job_id}/partlist`. If a job summary exists, the backend routes through the shared finalizer; otherwise it creates or blocks the standalone partlist and writes a retrospective/blocking manager notice.
- Do not rerun a job while it is in `classifying`, `extracting`, `producing`, `qc_checking`, or `retrying`; the API intentionally returns an in-progress error.

### Tab navigation

The job detail area has two tabs:

- **Ana Görünüm** — the default working view: PDF Önizleme (left) + Müdür Chat (right), then Adaylar (left) + Çıktılar ve QC (right) below.
- **Sistem Logu** — raw diagnostic and event log view: Diagnostics (left) + Canlı Event Stream (right) side by side.

When a new event arrives on the Event Stream while the user is on the Ana Görünüm tab, a purple unread badge counter appears on the Sistem Logu tab. Switching to Sistem Logu resets the counter to zero.

`focusManagerChat()` always switches to Ana Görünüm first so the hidden chat panel becomes visible before `scrollIntoView` is called.

### Ana Görünüm — PDF Önizleme

- The PDF buttons are generated from `job.pdfs`.
- Clicking a PDF button changes the iframe preview source to the inline preview URL (`?inline=true`).
- The PDF file-name link is the download action; approval buttons must not trigger a PDF download.
- The first uploaded PDF is previewed automatically when a job is selected.
- PDF Önizleme occupies the left column; Müdür Chat occupies the right column in the same row.

### Ana Görünüm — Adaylar

- This panel shows Codex visual extraction candidates from `job.codex_candidates.candidates`.
- Each row can be selected or deselected before approval.
- Editable fields are `poz_no`, `width`, `height`, `thickness`, `material`, and `quantity`.
- The confidence column is color-coded: high confidence is green, medium is amber, low is red.
- If deterministic candidates and Codex candidates both exist and their average confidence differs by more than `0.3`, the UI shows a conflict warning. Review and edit before approval.
- Approval writes manager-approved plate specs and then triggers deterministic production.
- Visual/unreadable PDFs must have page coverage before approval. If a PDF has 26 visual pages and only pages 1-3 have approved candidates, production must remain blocked.
- Candidates whose evidence mentions polygon, side offset, chamfer/pah, cugul, or corner relief cannot be approved with empty `corner_reliefs`.

### Ana Görünüm — Ciktilar ve QC

- Output files are read from `job.files` where `group == "output"`.
- Each output row includes relative path, byte size, and an `indir` download link.
- Partlist status is shown when `job.partlist` exists.
- Common output groups include DXF, NC1, QC JSON, rendered previews, candidate JSON, and partlist workbooks.
- If approved specs do not include explicit partlist metrics, QC/partlist enriches them deterministically: `unit_surface_area_m2 = 2*(width*height + width*thickness + height*thickness)/1e6` and `unit_weight_kg = width*height*thickness*7850/1e9`.

### Ana Görünüm — Mudur Chat

- Sits in the right column alongside PDF Önizleme.
- Uses `POST /api/manager/chat`.
- Only `teknik-ofis-muduru` is enabled through this endpoint.
- The selected job ID is sent as context, so questions like "bu isin durumunu ozetle" are answered with job context when a job is selected.
- The browser keeps recent chat history and also uses the persistent dashboard session `agent:teknik-ofis-muduru:dashboard`.
- Before answering, the backend recalls relevant manager memory from `workspace/memory/manager_memory.db`.
- Manager memory stores job-scoped facts, restart intents, manager decisions, recent turns, tags, PDF names, and poz numbers.
- Job memory is exported as readable Markdown under `workspace/manager_vault/jobs/<job_id>.md`; Obsidian can open this folder, but the runtime source of truth is SQLite.
- Generic job-list responses must not become the active job memory. "Bu is" should bind to an explicit selected job, an explicit job ID, or a prior job-specific turn.
- The UI timeout is 210 seconds so longer Codex CLI manager work can finish without the browser aborting early.
- Lightweight greetings, capability/status prompts, selected-job issue discussion, and safe job actions can be answered locally. The lightweight-greeting detector covers: "merhaba", "selam", "naber", "nasılsın", "iyi misin", "günaydın", "iyi günler", "iyi akşamlar", "iyi geceler", "görüşürüz", "hoşça kal", "sağol", "teşekkürler", "tamam anladım", and similar — none of these trigger Codex CLI.
- **LLM-first query synthesis**: When `GEMINI_API_KEY` is set, local handlers for job status, manual review detail, job learning, page exclusion, and approved-spec-patch-restart may route their raw template output through `_synthesize_query_with_gemini()` before replying. Gemini receives the raw data block plus the user's question and produces a natural, decision-oriented manager-voice response. Critical selected-job issue reports must stay deterministic and return the local template directly so status, note-writing, and production gates cannot be softened or misphrased by an LLM rewrite.
- **Gemini system prompt enrichment**: Every `_run_gemini_manager()` and `_synthesize_query_with_gemini()` call injects (1) expert agent memories from all 4 expert agent MEMORY.md/RULES.md files, (2) the last 3 successful extraction patterns from the memory bridge, and (3) a live job context block (`_build_live_job_context()`) with FSM state, project name, pipeline ok/produced/manual_review counts, Codex candidate count, approved-spec presence, recent failure details, and open manager notes.
- **`_build_live_job_context(paths, job_id) -> str`**: Reads `fsm_state.json`, `job.json`, `job_summary.json`, `codex_candidates.json`, `approved_plate_specs.json`, `events.jsonl`, and open manager issue notes to build a `## Seçili İş:` block injected into Gemini's system prompt.
- General substantive conversation (not a local handler match) uses Gemini as primary LLM when `GEMINI_API_KEY` is set; falls back to Codex CLI in read-only mode. Before calling Codex, the last 3 successful extraction patterns are read from the memory bridge and injected as "Son Başarılı Çizim Desenleri".
- Explicit project/code/file edit requests use Codex CLI in workspace-write mode with a longer timeout, then report changed files and verification.
- Selected-job DXF/NC1/QC artifact geometry issues are not project/code edit requests. Messages like "olusturulan DXF'te delik konumu hatali", "Poz 4042 alt delik X=85 Y=75 yerine X=85 Y=98.5 olmali", "pah/kose/poligon kontur hatali" must route to local manager handlers, not Codex CLI workspace-write.

### Sistem Logu — Diagnostics

- Located on the **Sistem Logu** tab, not the main view.
- Shows `job.diagnostics` when available; otherwise shows `job.summary`.
- Use this panel to understand why a PDF was automatically produced, blocked, or sent to manager approval.
- Rendered in monospace font (JetBrains Mono) for readability.

### Sistem Logu — Canli Event Stream

- Located on the **Sistem Logu** tab, not the main view.
- Uses `EventSource` against `/api/events/{job_id}`.
- Existing events are replayed first from the selected job detail.
- Heartbeats are ignored in the UI.
- The stream closes when a `completed` or `failed` event arrives, **unless** the completed event has `payload.status === 'needs_manager_approval'` — in that case the stream stays open so that subsequent background worker events (pre-validation, consensus check) remain visible. The stream closes naturally via server-side timeout in the approval-pending case.
- New events increment the unread badge on the Sistem Logu tab when the user is on Ana Görünüm.
- Rendered in monospace font (JetBrains Mono) for readability.

### Dashboard auth note

- When `TOFFICE_API_SECRET` is unset, protected write endpoints run in open local mode.
- When `TOFFICE_API_SECRET` is set, job read/write, manager chat, file access, sessions, memory, audit, SLA, metrics/status, and admin-style endpoints require `Authorization: Bearer <token>`.
- Create dashboard/API tokens with `.\scripts\toffice.ps1 token create --hours 24`.
- The dashboard header has a bearer token input. It stores the token in browser `localStorage` under `toffice_api_token` and injects it into dashboard `fetch` calls.
- PDF preview/download uses `POST /api/jobs/{job_id}/file-ticket` for short-lived URLs bound to job, relative file path, inline/download mode, and a 5 minute expiry.
- Rate limiting is dependency-free and in-memory: upload `10 / 10 dakika`, manager chat `20 / dakika`, job mutations `30 / dakika`, file tickets `120 / dakika`. Tests can disable it with `TOFFICE_RATE_LIMIT_DISABLED=1`.
- Upload limits default to 100 MB per PDF and 300 MB per job; override with `TOFFICE_MAX_UPLOAD_MB` and `TOFFICE_MAX_JOB_UPLOAD_MB`.
- CORS origins can be configured with `TOFFICE_CORS_ORIGINS`; wildcard origins are not used with credentials.

## Manager Chat and Agent Behavior

- The manager chat is session-backed and selected-job aware.
- The manager has a persistent memory layer in addition to short session history. It records every manager turn, extracts job IDs, PDF names, poz numbers, tags, issue facts, restart intents, and manager decisions.
- Memory recall is injected into manager prompts as `Mudur Hafizasi` before Codex CLI is called and is also used by local handlers for references such as "bu is".
- Raw chat memory must not be promoted directly into agent skill files. Only verified repeated rules should become `agents/*/MEMORY.md` or shared Codex skills.
- General substantive conversation uses Codex CLI in read-only mode.
- Explicit project/code/file edit requests use Codex CLI in workspace-write mode.
- If Codex CLI times out after producing a useful partial manager analysis, the dashboard response should show that partial analysis with a timeout note instead of replacing it with a generic failure.
- Safe explicit actions such as job listing, job run, QC lookup, partlist creation, and agent draft creation are routed through local tools.
- Page exclusion requests ("sayfa X plaka değil", "sayfa X-Y geçilsin", "sayfa X ve Y profil detayları" etc.) are detected locally by `_looks_like_page_exclusion_request()`. On detection, `_apply_page_exclusion_decision()` writes `page_exclusions.json`, records the decision in the memory bridge (`page_classification_hints` table keyed by PDF SHA256), and reruns the pipeline. Subsequent jobs using the same PDF file get the exclusion applied automatically.
- Page number extraction handles: single ("sayfa 3"), ordinal ("1. sayfa"), list ("sayfa 2 ve 3", "sayfa 1, 2 ve 3"), and range ("sayfa 2-4") forms.
- Selected-job production problems such as missing pages, wrong poz counts, missing chamfers, or polygon/contour issues are recorded as manager issue notes instead of being mistaken for a rerun command.
- Selected-job action requests such as "bu pozu duzeltelim", "tekrar uret", or "aksiyon zamani" are not handled as another issue-report prompt. The manager must resolve the latest relevant open note, patch approved specs when the geometry is known, rerun production/QC/finalizer, and report the concrete result.
- The phrase `isle ilgili` means "about the job" and must not be interpreted as the imperative `isle` / "process this". Do not let `456 numarali isle ilgili hatalar var` trigger `run_autocad_job`.
- Hole coordinate corrections are first-class local manager actions. `_looks_like_hole_coordinate_correction_request()` detects selected-job messages containing `delik`/`hole`, coordinate terms, and expected values; `_handle_hole_coordinate_correction()` must identify the job/poz/hole, patch `approved_plate_specs.json` `holes`, append/resolve a `delik koordinati` manager issue note, rerun production/QC, and keep the job `awaiting_approval` if any actionable manager notes remain.
- Open manager issue notes with tags including `hata bildirimi`, `delik koordinati`, `pah/kose eksigi`, `poligon kontur`, `gorsel analiz notu`, `eksik uretim`, or `eksik sayfa/poz` are blocking signals. Even if `job_summary.ok == true`, status responses must say the job is not completed for delivery until those notes are resolved.
- Job learning questions must first read `workspace/outputs/jobs/<job_id>/retrospective.json`; if it is missing but the job is eligible, offer or run learning backfill instead of claiming that the system learned from nowhere.
- New agents are created as drafts under `agents/_drafts`; activation still requires explicit approval.
- The manager operates with **proactive authority**: it selects and applies skills (IS_DAGITIMI, CIZIM_NC_KALITE_KONTROLU, OGRENME_VE_HAFIZA_YONETIMI) without waiting for the user to name them; it initiates visual analysis, QC, and partlist steps autonomously when the job state warrants it; it reports what it did and why. Only genuine technical ambiguity (low confidence + unknown geometry type) triggers an escalation question.
- The manager Codex context includes **expert agent memories**: at each `_run_codex_manager()` call, `load_expert_agent_memories()` reads `MEMORY.md` and `RULES.md` from `autocad-uzman-1`, `autocad-uzman-2`, `kalite-kontrol`, and `dokuman-kontrol` agent directories and injects them as "Uzman Hafızaları ve Kuralları". The manager can thus reference what experts have learned and which rules they follow.
- The manager loads **10 skills** via `agents/teknik-ofis-muduru/AGENT.md`: the original 5 (IS_DAGITIMI, AUTOCAD_MCP_HAZIRLIK, SUREC_IZLEME, CIZIM_NC_KALITE_KONTROLU, OGRENME_VE_HAFIZA_YONETIMI) plus 5 expert skill references (PDF_POZ_OKUMA, PLAKA_GEOMETRI_CIKARMA, DXF_2013_URETIMI, DSTV_NC1_URETIMI, ERT_PARTLIST_EXCEL_URETIMI) — all loaded automatically by `_load_referenced_skills()`.
- **Skill update chat flow** (two-step, manager-initiated): (1) User says "autocad uzman hafızasına ekle: ..." → `_looks_like_skill_update_request()` detects it → `_handle_skill_update_request()` writes a proposal file to `journal/skill_proposals/<agent>-memory-<ts>.md` and replies with the proposal ID. (2) User says "proposal <id> onayla" → `_looks_like_skill_promote_request()` detects it → `_handle_skill_promote_request()` reads the proposal, appends the learning note to `agents/<agent>/MEMORY.md`, and confirms. Expert skill files are never edited directly from chat without this two-step gate.
- **Polygon contour corner relief**: When a manager chat says "poligon olarak çiz" or similar (`_looks_like_polygon_draw_instruction()`), the pending corner-relief candidates are marked with `corner_reliefs: [{"type": "polygon_contour"}]` and exit the corner-relief conversation loop. `_candidates_needing_corner_reliefs()` treats any non-empty `corner_reliefs` list (including `polygon_contour` entries) as resolved, so the polygon instruction ends the loop without requiring explicit dimension input.
- The manager must not tell users to enable an alternate vision provider or bypass deterministic QC gates.

## API Surface

### Core job API

- `POST /api/jobs` - upload PDFs as a new job; auth required when `TOFFICE_API_SECRET` is set.
- `GET /api/jobs` - list jobs.
- `GET /api/jobs/{job_id}` - inspect diagnostics, candidates, approvals, outputs, partlist, events, `fsm_state`, and reconciled `active_manual_review_count` / `active_manual_reviews` (pages covered by Codex candidates or excluded via `page_exclusions_applied.json` are removed from the active count dynamically).
- `GET /api/jobs/{job_id}/files/{filename}` - download input or output files.
- `POST /api/jobs/{job_id}/file-ticket` - create a short-lived file URL for dashboard iframe preview/download in token-required mode.
- `POST /api/jobs/{job_id}/run` - run diagnostics/pipeline; idempotent guard blocks active jobs.
- `POST /api/jobs/{job_id}/approve-candidates` - manager approval; auth required when enabled.
- `POST /api/jobs/{job_id}/partlist` - create or block ERT partlist; auth required when enabled.
- `GET /api/learning/health` - closure and learning health: missing retrospectives, open manager notes, memory pattern stats, pending skill proposals, agent registry status, and recent closure events.
- `POST /api/jobs/{job_id}/learning/backfill` - create retrospective, vault summary, memory bridge record, skill proposal, and closure events for one eligible old job.
- `POST /api/learning/backfill` - scan all eligible jobs; defaults to `dry_run: true` and writes only when explicitly called with `dry_run: false`.
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
.\scripts\toffice.ps1 token create --hours 24
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

## MCP Tool Shape (autocad-mcp)

Eight consolidated tools. Each tool takes an `operation` string plus typed
parameters. The ezdxf backend implements all operations listed below; the
AutoCAD File IPC backend implements the same interface but may return
`ok=false` for operations it does not support.

### `drawing`

| Operation | Parameters | Notes |
| --- | --- | --- |
| `create` | `data: {name?}` | |
| `open` | `data: {path}` | |
| `info` | — | extents, entity count, layers, blocks |
| `save` | `data: {path?}` | QSAVE if path omitted |
| `save_as_dxf` | `data: {path}` | |
| `plot_pdf` | `data: {path}` | |
| `purge` | — | |
| `get_variables` | `data: {names: [...]}` | |
| `list_layouts` | — | returns all paper-space layout names |
| `get_extents` | — | model-space bounding box (min, max, width, height) |
| `undo` | — | |
| `redo` | — | |

### `entity`

| Operation | Parameters | Notes |
| --- | --- | --- |
| `create_line` | `x1, y1, x2, y2, layer?` | |
| `create_circle` | `data: {cx, cy, radius}, layer?` | |
| `create_polyline` | `points: [[x,y],...], data: {closed?}, layer?` | |
| `create_rectangle` | `x1, y1, x2, y2, layer?` | |
| `create_arc` | `data: {cx, cy, radius, start_angle, end_angle}, layer?` | |
| `create_ellipse` | `data: {cx, cy, major_x, major_y, ratio}, layer?` | |
| `create_mtext` | `data: {x, y, width, text, height?}, layer?` | |
| `create_hatch` | `entity_id, data: {pattern?}` | |
| `create_spline` | `points: [[x,y],...], layer?` | ezdxf only |
| `list` | `layer?` | |
| `count` | `layer?` | |
| `get` | `entity_id` | |
| `query` | `data: {type?, layer?, bbox?: [xmin,ymin,xmax,ymax]}` | bbox tests center point for CIRCLE/ARC/LINE, any vertex for LWPOLYLINE |
| `copy` | `entity_id, data: {dx, dy}` | |
| `move` | `entity_id, data: {dx, dy}` | |
| `rotate` | `entity_id, data: {cx, cy, angle}` | |
| `scale` | `entity_id, data: {cx, cy, factor}` | |
| `mirror` | `entity_id, x1, y1, x2, y2` | |
| `offset` | `entity_id, data: {distance}` | LINE → perpendicular offset; CIRCLE → radius change |
| `array` | `entity_id, data: {rows, cols, row_dist, col_dist}` | |
| `fillet` | `data: {id1, id2, radius}` | not supported on ezdxf |
| `chamfer` | `data: {id1, id2, dist1, dist2}` | not supported on ezdxf |
| `set_properties` | `entity_id, data: {layer?, color?, linetype?, lineweight?}` | |
| `erase` | `entity_id` | |
| `erase_many` | `data: {ids: [...]}` | returns erased + not_found lists |
| `explode` | `entity_id` | INSERT → virtual entities; LWPOLYLINE → LINE segments |

### `layer`

| Operation | Parameters | Notes |
| --- | --- | --- |
| `create` | `data: {name, color?, linetype?}` | |
| `list` | — | |
| `set_current` | `data: {name}` | |
| `freeze` | `data: {name}` | |
| `thaw` | `data: {name}` | |
| `lock` | `data: {name}` | |
| `unlock` | `data: {name}` | |
| `delete` | `data: {name}` | layer `"0"` is protected |

### `annotation`

| Operation | Parameters | Notes |
| --- | --- | --- |
| `create_text` | `data: {x, y, text, height?, rotation?, layer?}` | |
| `create_dimension_linear` | `data: {x1, y1, x2, y2, dim_x, dim_y}` | |
| `create_dimension_aligned` | `data: {x1, y1, x2, y2, offset}` | |
| `create_dimension_angular` | `data: {cx, cy, x1, y1, x2, y2}` | |
| `create_dimension_radius` | `data: {cx, cy, radius, angle}` | |
| `create_leader` | `data: {points: [[x,y],...], text}` | |
| `create_mleader` | `data: {points: [[x,y],...], text, layer?}` | leader + mtext pair |

### `block`, `pid`, `view`, `system`

Unchanged from initial release. See `mcp/autocad-mcp-server/src/autocad_mcp/server.py`
for the full operation lists.

## DXF Writer Behaviour

- `write_plate_dxf` writes plate outer contour on `PLATE_OUTER`, holes on
  `PLATE_HOLES`, and slots on `PLATE_SLOTS`.
- Slots are drawn as rotated closed LWPOLYLINE rectangles using
  `SlotSpec.rotation_deg` (default `0.0`). Non-zero rotation is applied via a
  2D rotation matrix around the slot centre.
- Label text (`poz_no T=<thickness> <material>`) is written on `PLATE_TEXT`.

## QC Checks

`build_qc_report` in `qc.py` verifies:

- DXF version is `AC1027`.
- Circle count matches `PlateSpec.holes`.
- At least one closed polyline exists (outer contour).
- Corner relief count (bulge arcs + extra polygon vertices) ≥ `len(spec.corner_reliefs)`.
- Slot polyline count on `PLATE_SLOTS` matches `len(spec.slots)`.
- NC1 file contains `ST`, poz_no, and `EN` markers.

`ok=true` is required before partlist/delivery.

## Non-Negotiable Rules

- Codex CLI, proje duzeltme (workspace-write) ve PDF gorsel cikarma icin tek AI motorudur.
- Manager chat sorgu sentezi ve genel sohbet icin `GEMINI_API_KEY` ayarlandiginda Google Gemini 2.5 Flash birincil LLM olarak kullanilir. Codex CLI genel sohbet icin ikincil (fallback) konumundadir; workspace-write ve PDF gorsel cikarma icin hala tek motorudur.
- Gemini API key yoksa tum handler'lar mevcut sablon yanit davranisini korur (tam fallback uyumlulugu).
- PDF gorsel cikarma icin harici vision saglayici ekleme.
- Do not create a new frontend app under `apps/`; extend the existing FastAPI runtime dashboard.
- Do not use `apps/office3d` as a default validation or development target.
- Do not edit Codex global MCP config automatically. Detect stale config and show manual commands.
- Do not produce DXF/NC1 from visual candidates until `teknik-ofis-muduru` approval writes `approved_plate_specs.json`.
- Do not create partlist/delivery outputs unless QC rows are `ok=true`.
- Do not mark a job `completed` until the shared finalizer has completed partlist, retrospective, memory bridge, skill proposal, and manager notification.
- Do not auto-edit agent brain or skill source files as "learning". Runtime learnings are first written as retrospectives, manager vault notes, memory bridge records, and proposal files under `journal/skill_proposals/`. Expert `agents/*/MEMORY.md` files are only updated through the two-step skill update chat flow (proposal → explicit "proposal X onayla" approve).
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
- Completion retrospectives are stored under `workspace/outputs/jobs/<job_id>/retrospective.json`.
- Skill promotion candidates are stored under `journal/skill_proposals/` and require explicit human approval before being copied into shared skills.
- `corner_reliefs: [{"type": "polygon_contour"}]` is a valid resolved state for a candidate. It means the plate will be produced using a polygon contour method; QC does not require explicit corner dimensions for these candidates. Do not re-enter the corner-relief conversation loop for polygon-marked candidates.
- `_synthesize_query_with_gemini(data_block, user_text, history, *, fallback_reason)` in `orchestrator.py` is the standard helper for routing non-critical local template data through Gemini for natural synthesis. Add new read-only query handlers here rather than returning raw `AgentRunResult(content=template_string)`. Exception: selected-job issue reports and production gate decisions must return deterministic local text directly. When Gemini is absent it returns the template directly. The synthesis message explicitly forbids madde listesi, numaralı liste, and key:value format — Gemini must produce 2-4 sentence akıcı Türkçe paragraf. Do NOT weaken these format constraints.
- `_build_live_job_context(paths, job_id)` in `orchestrator.py` reads the current job state from disk and returns a `## Seçili İş:` markdown block for Gemini's system prompt. It reads: `fsm_state.json`, `job.json`, `job_summary.json`, `codex_candidates.json`, `approved_plate_specs.json` (existence check), `events.jsonl` (last failure), and open manager issue notes.
- All `_looks_like_*`, `_extract_*`, corner-relief parse, and context-marker utility functions live in `chat_detectors.py`, not `orchestrator.py`. Add new text-pattern detectors there. `orchestrator.py` imports them via the named import block at the top of the file and keeps only functions that require `RuntimePaths` or external service calls.
- `IN_PROGRESS_STATES` from `job_fsm.py` is the authoritative set of active FSM states. Do not re-define the states as a literal tuple or list elsewhere; always import and reference `IN_PROGRESS_STATES`.
- `_render_candidate_pages()` in `app.py` returns `tuple[list[Path], list[dict]]` — `(full_page_images, evidence_meta)`. Both full-page PNGs and microzoom region PNGs are passed to `CodexRunRequest(images=all_images)`. Tests that call `_render_candidate_pages()` must unpack with `images, _evidence_meta = _render_candidate_pages(...)`.
- `_candidate_prompt()` in `app.py` accepts `pdf_import_paths: list[str] | None` and `manifest_path: str | None` keyword args. Pass these from `_extract_codex_candidates()` so Codex knows which PDF files are being processed and where the microzoom manifest lives. The prompt also now instructs Codex to report coordinate inference and dimension chain inconsistencies in `uncertainties`.
- `coordinate_inferred: bool` is set in `_normalize_candidate()` (app.py) by scanning `uncertainties` for "inferred", "çıkarım", "ambiguous", "belirsiz", "diagonal", and related keywords. The field is preserved through to `approved_plate_specs.json` and adds `"coordinate_inferred_from_uncertainty"` to `notes`. Dashboard shows a ⚠ amber icon in the poz_no cell when this flag is true.
- `_dimension_chain_warnings(row)` in `approval_validation.py` scans `uncertainties` for dimension/chain/zincir keywords and returns non-blocking warning strings. Warnings are written to `data["dimension_chain_warnings"]` in `validate_approved_rows()`. Dashboard shows ⚠ icon and detail text in the evidence panel.
- Confidence default in `approval_validation.py:83` uses `_raw_conf if (_raw_conf := _optional_float(...)) is not None else 0.5`. Do NOT revert to `or 1.0` — that masks missing confidence as maximum confidence.
- Relief type normalization has one canonical source: `mcp/autocad-mcp-server/src/autocad_mcp/technical_office/relief_types.py`. Runtime code must call the thin wrapper in `runtime/technical_office_runtime/relief_types.py`. Recognized input aliases: `pah/bevel/beveled/chamfered -> chamfer`; `round_relief/rounded/radius -> round`. `polygon_contour` is a sentinel and must be filtered at all read/write paths, never passed to `CornerReliefSpec`.
- `read_json(path, *, default=None)` in `state_io.py` is the canonical JSON file reader. Use it instead of inline `json.loads(path.read_text(...))`. It handles `FileNotFoundError` silently, logs `JSONDecodeError` and `OSError` via stdlib `logging`, and returns `default` on any failure. Do NOT add new inline `json.loads(path.read_text(...))` patterns in runtime code.
- `chat_detectors.py` module-level compiled regex constants: `_RE_JOB_ID`, `_RE_POZ_NO`, `_RE_NUMERIC_REF`, `_RE_RADIUS_DOT`, `_RE_RADIUS_COMMA`, `_RE_DIM_PAIR`, `_RE_NUM_DOT`, `_RE_PROJECT_NAME`, `_RE_ISLE_WORD`, `_RE_URET_WORD`, `_RE_STEP_START`, `_RE_STEP_PATTERNS`. New regex patterns added to `chat_detectors.py` MUST be defined as module-level `_RE_*` compiled constants, not inline in function bodies.
- `_DispatchEntry` + `_MANAGER_DISPATCH` in `orchestrator.py`: new local handlers for the teknik-ofis-muduru manager chat MUST be added to the `_MANAGER_DISPATCH` list (module level, before `AgentOrchestrator` class) rather than as inline `if _looks_like_X(text)` branches in `run()`. `_run_dispatch()` iterates the table in order — position matters. Handlers with `needs_session=True` receive `(text, history, session_id=session_id)`; handlers with `text_only=True` receive only `(text,)`; others receive `(text, history)`. Handlers that have conditional logic beyond a single detector call (e.g., `_looks_like_project_edit_request` which branches on `self.allow_codex`) must stay in `run()` directly.

## Parked 3D Office

`apps/office3d` is retained for reference only. Work there only when the user
explicitly asks for legacy 3D office maintenance.
