# Technical Office Runtime Rules

## Active App
- Primary app: `runtime/technical_office_runtime`
- Start: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\suite.ps1 start`
- Doctor: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\toffice.ps1 doctor`
- Codex MCP check: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\codex-mcp.ps1`

## Product Boundary
- Keep Codex CLI as the only active AI agent engine.
- Do not add alternate AI engines or external PDF vision provider paths.
- PDF visual extraction uses local page rendering plus Codex CLI candidates, followed by manager approval.

## Delivery Gates
- `approved_plate_specs.json` is required before visual candidates can produce DXF/NC1.
- QC `ok=true` is required before partlist and delivery.
- `partlist_manual_review_required.json` blocks delivery until manager review is resolved.
