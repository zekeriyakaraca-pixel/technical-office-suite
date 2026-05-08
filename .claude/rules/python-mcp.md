# Python Runtime and MCP Rules

## Active Python Surface
- Runtime API: `runtime/technical_office_runtime/`
- Runtime tests: `uv run --project runtime --extra dev pytest runtime/tests -q`
- AutoCAD MCP pipeline: `mcp/autocad-mcp-server/src/autocad_mcp/technical_office/`
- Pipeline tests: `uv run --project mcp/autocad-mcp-server --extra dev pytest mcp/autocad-mcp-server/tests/test_technical_office_pipeline.py -q`

## Backend Policy
- Default production path is deterministic Python plus Codex CLI candidate extraction.
- Headless DXF/NC1/QC tests do not require AutoCAD when `AUTOCAD_MCP_BACKEND=ezdxf` or `auto` falls back to ezdxf.
- AutoCAD live/File IPC checks are optional and must be explicitly requested.

## MCP Tool Design
- Keep the 8 consolidated AutoCAD MCP tools: `drawing`, `entity`, `layer`, `block`, `annotation`, `pid`, `view`, `system`.
- Add new behavior as sub-operations unless the existing public MCP shape cannot represent it.
- Return JSON-serializable dictionaries with clear `ok` or `status` values and user-safe error text.

## Codex CLI Compatibility
- Do not assume `.mcp.json` configures Codex CLI. Codex reads its own global MCP config.
- Use `scripts/codex-mcp.ps1` or `scripts/toffice.ps1 doctor` to detect stale `autocad-mcp` paths.
- Do not edit global Codex config without explicit user approval.
