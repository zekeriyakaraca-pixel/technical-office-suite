# Parked Claw3D Notes

`apps/office3d` is a legacy parked application. It remains in the repository for reference, but it is not the active Technical Office runtime.

## Rules
- Do not run `npm run dev`, `npm run lint`, or `npm run test` in `apps/office3d` as the default validation path.
- Do not add new Technical Office features to `apps/office3d`.
- Active UI/API work belongs in `runtime/technical_office_runtime`.
- Only touch `apps/office3d` when the user explicitly asks for legacy 3D office maintenance.
