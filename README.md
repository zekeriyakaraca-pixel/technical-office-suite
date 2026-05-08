# Technical Office Agent Suite

Local technical office workspace for PDF/job intake, Codex CLI assisted manager workflows, and deterministic AutoCAD PDF to DXF/NC1/QC/partlist production.

## Quick Start

From this folder:

```powershell
.\scripts\suite.ps1 start
```

Runtime API and dashboard:

```text
http://127.0.0.1:7770/
```

Useful commands:

```powershell
.\scripts\suite.ps1 status
.\scripts\suite.ps1 logs
.\scripts\suite.ps1 restart
.\scripts\suite.ps1 stop
```

## Local CLI

```powershell
.\scripts\toffice.ps1 doctor
.\scripts\codex-mcp.ps1
.\scripts\toffice.ps1 ask "test-001 isini AutoCAD live kapali calistir ve QC ozetle"
.\scripts\toffice.ps1 job run test-001 --autocad off
.\scripts\toffice.ps1 agent draft "Tekla DXF kontrol ajani"
```

`doctor` checks the local `codex.cmd` executable, ChatGPT login state, `codex.cmd exec` readiness, and whether Codex CLI's global `autocad-mcp` entry points to this repo.

## Runtime API

- `POST /api/jobs`: upload PDFs as a new job.
- `GET /api/jobs`: list jobs.
- `GET /api/jobs/{job_id}`: inspect diagnostics, candidates, approvals, outputs, partlist, and events.
- `GET /api/jobs/{job_id}/files/{filename}`: download input or output files.
- `POST /api/jobs/{job_id}/run`: run diagnostics/pipeline and Codex visual candidate extraction when needed.
- `POST /api/jobs/{job_id}/approve-candidates`: approve edited candidates and run production.
- `POST /api/jobs/{job_id}/partlist`: create or block ERT partlist.
- `GET /api/events/{job_id}`: live server-sent event stream.
- `POST /api/manager/chat`: session-backed manager Codex CLI chat with selected-job context.

## Validation

```powershell
uv run --project runtime --extra dev pytest runtime\tests -q
uv run --project mcp\autocad-mcp-server --extra dev pytest mcp\autocad-mcp-server\tests\test_technical_office_pipeline.py -q
.\scripts\smoke.ps1
```

Outputs are written under `workspace/outputs/jobs/<job_id>/`.

## Parked 3D Office

The legacy 3D office remains in `apps/office3d` for reference, but it is no longer started by `suite.ps1`, smoke tests, hooks, or runtime validation. See `apps/office3d/PARKED.md`.
