# DXF 2013 Uretimi

Use this skill when a validated `PlateSpec` is ready for deterministic DXF output.

## Rules
- Produce DXF through the Python technical office pipeline, not by freehand drawing in chat.
- Use AutoCAD live checks only when explicitly requested; headless `ezdxf` output is valid for default tests.
- DXF output must stay under `workspace/outputs/jobs/<job_id>/<poz_no>/`.
- Do not continue to delivery if QC returns `ok=false`.
- Slots are written on `PLATE_SLOTS` as rotated closed LWPOLYLINE using `SlotSpec.rotation_deg`.
  Non-zero rotation is applied correctly — do not assume slots are always axis-aligned.

## Output
- Record the DXF path, related NC1 path, and QC path in the job summary.
- Escalate invalid geometry to manager review.

## QC Contract
- `dxf.slot_polyline_count` must equal `len(PlateSpec.slots)` for `ok=true`.
- `dxf.circle_count` must equal `len(PlateSpec.holes)` for `ok=true`.
