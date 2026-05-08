# ERT Partlist Excel Uretimi

Use this skill when a completed job needs an ERT `Part_List_holes` Excel workbook.

## Rules
- Partlist rows may include only QC `ok=true` positions.
- Do not guess `unit_surface_area_m2` or `unit_weight_kg`.
- If production has manual reviews, missing QC, failed QC, or missing metrics, write `partlist_manual_review_required.json` and block the workbook.
- Save successful workbooks as `<safe_project_name>_partlist.xlsx` under `workspace/outputs/jobs/<job_id>/`.

## Output
- Return workbook path, row count, and manual review list.
- Delivery is allowed only when the partlist result is `ok=true`.
