# Cizim NC Kalite Kontrolu

Use this skill for independent checks of produced DXF, NC1, and plate QC reports.

## Rules
- QC is a gate: `ok=true` opens delivery and partlist, `ok=false` blocks them.
- Compare produced outputs against the same approved `PlateSpec`.
- If the PDF required manual review or a candidate is unapproved, delivery remains closed.
- The quality-control agent reports findings to `teknik-ofis-muduru`; it does not ask the user to change model/provider settings.

## QC Checks (build_qc_report)
- DXF version: `AC1027`
- Circle count = `len(PlateSpec.holes)`
- `PLATE_SLOTS` closed polyline count = `len(PlateSpec.slots)`
- Corner relief count (bulge arcs + extra polygon vertices) ≥ `len(PlateSpec.corner_reliefs)`
- NC1 contains `ST`, poz_no, and `EN` markers

Report fields: `ok`, `dxf.circle_count`, `dxf.expected_holes`, `dxf.slot_polyline_count`,
`dxf.expected_slots`, `dxf.corner_relief_count`, `nc1.ok`, `autocad_live_check`.

## Output
- Report job ID, poz number, output paths, `ok` status, and blocking reasons.
- Keep the action sentence clear: manager review, re-production, or partlist allowed.
