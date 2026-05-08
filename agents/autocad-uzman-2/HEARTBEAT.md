# autocad-uzman-2 Heartbeat

## Schedule
Görev bazlı (On-demand).

## Each Cycle

### 1. Read Context
- Check assigned `data/imports/jobs/<job_id>/input.pdf`.
- Check optional `positions.csv` or `positions.json`.

### 2. Assess State
- Check if PDF can be read.
- Check if the job is vector/text-readable or requires manual review.

### 3. Execute Skill
- Run `../_shared/skills/PDF_POZ_OKUMA.md`.
- Run `../_shared/skills/PLAKA_GEOMETRI_CIKARMA.md`.
- Run `../_shared/skills/DXF_2013_URETIMI.md`.
- Run `../_shared/skills/DSTV_NC1_URETIMI.md`.

### 4. Log to Journal
- Log completion status.
- Include output paths and any manual review reason.
- Use `../_shared/skills/OGRENME_VE_HAFIZA_YONETIMI.md` for new PDF/layout patterns; write first to journal, then MEMORY only after confirmation.
