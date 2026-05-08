# teknik-ofis-muduru Heartbeat

## Schedule
Daily ve yeni `data/imports/jobs/<job_id>/input.pdf` geldiğinde on-demand.

## Each Cycle

### 1. Read Context
- Check `data/imports/jobs/` for new `input.pdf` files.
- Check optional `positions.csv` or `positions.json`.
- Check `job.json`; if a new project has no metadata, create it with `project_name`, `manager_agent` and `created_at`.
- Check `journal/` for updates from engineers.

### 2. Assess State
- Identify unassigned PDF plate jobs.
- Identify delayed jobs and manual review queues.
- Confirm each produced poz has DXF, NC1 and QC paths.
- Hangi autocad-uzman'ın müsait olduğunu belirle: `journal/` içindeki son log girişlerini kontrol et. "started job X" kaydı olan uzman meşgul, "completed job X" kaydı olan veya hiç kaydı olmayan uzman müsaittir. İkisi de meşgulse işi `status: "queued"` olarak işaretle; bir sonraki döngüde tekrar kontrol et.

### 3. Execute Skill
- Run `../_shared/skills/AUTOCAD_MCP_HAZIRLIK.md` before each new AutoCAD/DXF plate job.
- Run `../_shared/skills/IS_DAGITIMI.md` if new tasks exist.
- Run `../_shared/skills/SUREC_IZLEME.md` to update status.
- Run `../_shared/skills/OGRENME_VE_HAFIZA_YONETIMI.md` during job closeout and weekly review.
- Assign `../_shared/skills/ERT_PARTLIST_EXCEL_URETIMI.md` to `dokuman-kontrol` after QC `ok=true`.

### 4. Log to Journal
- Log assignments and status updates.
- Log learning candidates and skill-promotion decisions.

## Weekly Review
- Review team performance against KPIs.
- Update `MEMORY.md` with workload patterns.
- Promote only QC-backed repeated learnings from journal/MEMORY into shared skills.

## Monthly Review
- Flag if workload exceeds capacity.

## Escalation Rules
- Critical delay > 3 days.

## Rules
- Never leave a PDF plate job unassigned for more than 24h.
- Never mark delivery complete without QC `ok=true`.
- Never stop DXF/NC1 production only because AutoCAD live validation is unavailable; write the skipped status into QC.
