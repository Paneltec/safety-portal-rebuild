# Staged implementation plan

A stage is complete only when it has working persistence, server-side access control, validation, failure states, screens and automated tests. Status per requirement lives in `docs/ledger/feature_coverage.csv`; status per API operation lives in `docs/ledger/endpoint_coverage.csv`.

| Stage | Scope | Requirement IDs | Status |
|---|---|---|---|
| 1 | Sign-in, sessions, invitations and resets, users, role catalogue, permission matrix, per-user overrides, presets, audit log, office shell | CORE-01 (part), AUTH-01 (part), PERM-01 (part), NAV-01 (part) | **Built and tested** |
| 2 | Organisation settings and branding, companies, file storage with hashes, document library, form engine (templates, field families, submissions, PDFs), assignments | ORG-01, DOC-01, FORM-01, FORM-02, REF-01 | Not started |
| 3 | Safety capture: SWMS, pre-starts, site diary, incidents and hazards, inspections, risk and SSRA, bulk import | SWMS-01, PRE-01, DIARY-01, INC-01, INSP-01, RISK-01, IMP-01 | Not started |
| 4 | Workers, HR privacy, certifications, inductions, suppliers, renewals, sites and visitors, daily jobs | WORK-01/02, CERT-01, IND-01, SUP-01, REN-01, SITE-01, JOB-01 | Not started |
| 5 | Integration registry with encrypted config, adapters plus labelled simulators (Simpro, Navixy, SmartFill, Microsoft 365, TextMagic, Dropbox), outbox, safe mode, notifications | INT-01..05, DBX-01, COM-01 | Not started |
| 6 | Fleet and equipment, service schedules, SmartFill import, weekly diesel lock, fuel reports, anomaly inbox | FLEET-01, SERV-01, EQUIP-01, FUEL-01..04 | Not started |
| 7 | Leave, phone timesheets, approvals, payroll access, employee settings, weekly workbench, lifecycle, reports, payslips, ABA, provider readiness (all test-only) | LEAVE-01, TIME-01/02, PAY-01..09 | Not started |
| 8 | Phone app: shell, My Work, forms, QR and camera lifecycle, offline queue, preview, builds | PHONE-01..03 | Not started |
| 9 | Dashboard, search and Ask, audit exports, backups and NAS agent, system and diagnostics, manuals, WHS references, final parity report | DASH-01, AI-01, DOC-02, BACK-01, OPS-01, ADMIN-01, HELP-01, LEGAL-01, DEL-01 | Not started |

## Order of work inside each stage

1. Data model and migration.
2. API handlers with permission checks and tenant scope.
3. Tests: allowed and denied paths, two organisations, duplicates and retries.
4. Screens, including empty, loading, denied, error and retry states.
5. Browser walkthrough, ledger update, generated OpenAPI refresh.

## Stage 1 items deliberately left for later stages

| Item | Goes to |
|---|---|
| Mobile PIN sign-in, onboarding cards and device hints | Stage 8 |
| Admin-console PIN and destructive admin operations | Stage 9 |
| Sign-in with Simpro, import users from Simpro, role sync from Simpro positions | Stage 5 |
| Role form allowlist | Stage 2 |
| Named payroll grants and accountant invitation | Stage 7 |
| Emailing invitation and reset links | Stage 5 |
| Scoped download tokens | Stage 2 |
| Per-address sign-in throttling | Stage 9 |
