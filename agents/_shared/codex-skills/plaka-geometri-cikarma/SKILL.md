# Plaka Geometri Cikarma

Use this skill when approved PDF or visual candidates need to become validated plate specifications.

## Rules
- Never infer missing dimensions, material, thickness, hole coordinates, or quantity.
- A valid plate candidate needs `poz_no`, `width`, `height`, `thickness`, `material`, and `quantity`.
- Holes require `x`, `y`, and `diameter`; slots require `x`, `y`, `length`, `width`, and optional `rotation_deg`.
- Every candidate from visual reading remains manager-approved input, not automatic production truth.

## Output
- Emit `PlateSpec` compatible JSON only after manager approval.
- Include `source_pdf`, optional `source_page`, confidence, and notes for traceability.
