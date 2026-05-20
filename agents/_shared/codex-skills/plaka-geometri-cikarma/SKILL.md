# Plaka Geometri Cikarma

Use this skill when approved PDF or visual candidates need to become validated plate specifications.

## Rules
- Never infer missing dimensions, material, thickness, hole coordinates, or quantity.
- A valid plate candidate needs `poz_no`, `width`, `height`, `thickness`, `material`, and `quantity`.
- Holes require `x`, `y`, and `diameter`; slots require `x`, `y`, `length`, `width`, and optional `rotation_deg`.
- Every candidate from visual reading remains manager-approved input, not automatic production truth.
- If the outer contour is non-rectangular (polygon/sloped), populate `polygon_vertices` with all corner coordinates in CCW order, (0,0) = bottom-left, in mm. If coordinates cannot be read reliably, set `polygon_vertices` to null and add an entry to `uncertainties`.
- A polygon outer contour and chamfer corner reliefs are valid together: use `polygon_vertices` for the overall shape and `corner_reliefs` for individual corner chamfers simultaneously.
- Visual candidates must keep `source_trace`, `analysis_confidence`, `uncertainties`, `microzoom_manifest_path`, and `evidence_images` through approval.
- `analysis_confidence` is reading confidence only; it is not QC confidence or permission to produce.

## Output
- Emit `PlateSpec` compatible JSON only after manager approval.
- Include `source_pdf`, optional `source_page`, confidence, and notes for traceability.
