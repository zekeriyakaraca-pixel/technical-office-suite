# dokuman-kontrol Heartbeat

## Schedule
Daily.

## Each Cycle

### 1. Read Context
- Check `outputs/` for new files to review.
- Check `data/imports/jobs/<job_id>/job.json` for manager-approved `project_name`.

### 2. Assess State
- Identify unformatted or unarchived files.
- Identify jobs with QC `ok=true` and no ERT partlist Excel.

### 3. Execute Skill
- Run `../_shared/skills/DOKUMAN_FORMATLAMA.md`.
- Run `../_shared/skills/ERT_PARTLIST_EXCEL_URETIMI.md` for completed AutoCAD plate jobs.
- Run `../_shared/skills/OGRENME_VE_HAFIZA_YONETIMI.md` for repeated document-control issues.

### 4. Log to Journal
- Log review outcomes.
- Log generated partlist path or `partlist_manual_review_required.json` reason.
