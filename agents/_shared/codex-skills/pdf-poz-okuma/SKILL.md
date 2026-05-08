# PDF Poz Okuma

Use this skill when a technical office job contains PDF drawings that must be classified before DXF/NC1 production.

## Rules
- `poz_no` is the technical mark or part number on the drawing; never treat the PDF page number as the poz number.
- Start with deterministic PDF diagnostics: text layer, vector density, page count, and manual review flags.
- If text is missing or unreadable, create visual candidates only; do not approve them or produce DXF/NC1 directly.
- Low confidence candidates must be marked `approval_required=true` and routed to `teknik-ofis-muduru`.
- Do not ask the user to enable external AI providers. The active visual candidate path is Codex CLI plus local rendering.

## Output
- For readable PDFs, report candidate poz numbers and confidence.
- For visual or unreadable PDFs, report `manual_review_required` with source PDF, page range, reason, and next manager action.
