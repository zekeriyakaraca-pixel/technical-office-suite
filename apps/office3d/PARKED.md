# Parked 3D Office

`apps/office3d` is intentionally parked for the Technical Office 2D + Codex CLI runtime.

The active v1 path is:

1. FastAPI runtime and 2D dashboard on `http://127.0.0.1:7770/`.
2. Server-side Codex CLI bridge for manager chat and visual PDF candidate extraction.
3. Deterministic Python pipeline for DXF, NC1, QC, and partlist outputs.

Do not start this app from `scripts/suite.ps1` or `scripts/smoke.ps1`. Reactivating it should be a separate legacy/visualization task with an explicit flag and tests.
