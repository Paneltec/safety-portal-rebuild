# Build progress

Updated at the end of each finished part. The ledgers in `docs/ledger/` hold the per-requirement and per-operation detail.

## Built and tested

| Part | What works | Evidence |
|---|---|---|
| Stage 1 | Sign-in, lockout, sessions (expiry, idle timeout, revoke), invitations, password resets, forced password change, users, role catalogue, custom roles, permission matrix, per-person overrides, presets, audit log, tenant isolation | `backend/tests/test_auth.py`, `test_users.py`, `test_permissions_unit.py`, `test_roles_sessions_tenancy.py` |
| Account holder | First-run setup protected by `SETUP_KEY`; the account holder is the protected top-level admin and payroll owner; transfer by the holder only | `backend/tests/test_org_setup_files.py` |
| Stage 2a | Organisation settings with validated addresses, contact details, logo upload/replace/remove, companies, file storage with hash/size/type and a clear "missing" state | `backend/tests/test_org_setup_files.py`, `e2e/walkthrough.py` |
| Stage 2b | Document library: nested folders, multi-file upload with type allowlist, authorised download and inline preview, rename, move, expiry, tags, archive/restore, delete, revision history, missing-content repair, counts, search | `backend/tests/test_documents.py`, `e2e/walkthrough.py` |
| Stage 2c | Form engine: 20 field families, template editor with preview, published versions, draft/submit with photos, attachments and drawn signatures, idempotent retry, read-only record view, import/export, branded PDF, assignments by role/person/company with an effective preview | `backend/tests/test_forms_engine.py`, `test_forms_api.py`, `e2e/walkthrough.py` |
| Stage 3 | Safety registers: SWMS (review, assignments, acknowledgements, versions), pre-starts, site diary, incidents, hazards, inspections, risk assessments, SSRA; review and tracking workflows, history, photos and signatures, PDFs, bulk archive; forms project into their register once | `backend/tests/test_safety.py`, `e2e/walkthrough.py` |
| Stage 4a | Workers register (active/inactive/archived, details, photo, duplicate warning, history), privacy tiers with encrypted and masked protected fields and audited reveal, login link and provisioning, self-service edits; certifications (types with aliases, expiry states, evidence, renewals that keep earlier evidence, register with matching totals, expected certificates); certification gate on forms (two switches, checked on start and submit) | `backend/tests/test_workers.py`, `e2e/walkthrough.py` (workers step) |
| Stage 4b | Sites register with sign-on questions and visitor rules, QR code and printable A4 gate sign, revocable codes; public gate page for visitors (sign in, sign out, remembered on the phone) and signed-in sign-on for workers; live occupancy, history, office sign-out; visitor register with counts, reasoned corrections, archive, delete and CSV export; site picker in forms | `backend/tests/test_sites.py`, `e2e/walkthrough.py` (sites and visitors step) |
| Stage 4c | Supplier and contractor directory (ABN check, people, tasks, notes, status), compliance documents with expiry states, history and missing required documents; external representative logins scoped to one supplier; renewal links for suppliers and workers (scoped public upload page, office accept/reject per file, reissue, delivery state) | `backend/tests/test_suppliers_renewals.py`, `e2e/walkthrough.py` (suppliers and renewals step) |
| Stage 4d | Inductions matrix (types, cells with date / does not expire / not applicable / clear, evidence, own row, CSV export) sharing one store with certifications; completed training with evidence; HR documents with audited opens; daily jobs (issue to one or many with trial run and explicit replace, one active job per worker per day, accept/decline/done, My work, Home card, linked forms, job picker; SMS held) | `backend/tests/test_inductions_jobs.py`, `e2e/walkthrough.py` (inductions and jobs step) |
| Stage 5a | Connections catalogue (Simpro, Navixy, SmartFill, Microsoft 365, TextMagic) with encrypted details, read-only tests, labelled simulator and honest status; safe mode (on by default, server lock, explicit grant); single send boundary; outbox with history, retry, cancel; one-time links never stored; notification bell. Provider calls are tested against a mock only | `backend/tests/test_comms.py`, `e2e/walkthrough.py` (connections and outbox step) |
| Stage 5b | Simpro import of workers, sites and suppliers (dry run, pages, rate-limit retry, per-company results, hand edits kept, review queue, labelled simulator data); expiry reminders for certificates and supplier documents with preview and dedupe. Simpro calls are tested against a mock only | `backend/tests/test_imports.py`, `backend/tests/test_comms.py`, `e2e/walkthrough.py` (connections and outbox step) |
| Stage 6a | Fleet and equipment register; QR labels; meters with history and regression review; documents and certificates; equipment categories; Navixy tracker list (preview, sync, tags, sign-in refresh) feeding the register and the form vehicle picker, with typing a vehicle in always allowed. Navixy calls are tested against a mock only | `backend/tests/test_fleet.py`, `e2e/walkthrough.py` (fleet and equipment step) |

## Next, in order

1. Left over from stage 5: Simpro jobs, customers, attachments and scheduled sync; compose-and-send for reports; Dropbox.
1a. **Stage 6b and 6c**: service schedules and records; SmartFill and CSV fuel import, fuel cards, weekly diesel lock, fuel reports, anomaly inbox.
1b. Left over from stage 4 (all recorded as PARTIAL in the ledger): worker ID card and QR, mobile PIN, supplier QR induction, inductions spreadsheet import and printable matrix, PDF/SMS job import, GPS distance checks at sign-on, supplier document folders.
1a. Carried forward: bulk pre-start import (IMP-01), reference catalogues (REF-01), AI drafting, positions and asset-type assignment rules, authorised corrections after submit.
2. Stage 5 onwards as listed in `STAGED_PLAN.md`.

## Not verified

- The Docker Compose deployment has not been run (no Docker daemon in the build environment). The API, database migrations and web app were run directly.

## Working notes for whoever continues the build

- The reconstruction pack is the only source. Read the relevant operations in `api_contracts.json` and models in `request_models.json` before each part; record every deviation as a decision in `ADR-001-architecture.md`.
- A part is finished only with: migration, handlers with permission and tenant checks, tests for allowed and denied paths across two organisations, screens with empty/loading/denied/error states, a step in `e2e/walkthrough.py`, ledger entries in `docs/ledger/update_ledger.py`, and a refreshed `docs/openapi.json`.
- `ENCRYPTION_KEY` must be set for protected worker fields; tests and `e2e/run.sh` generate a throwaway key. Never commit a real key.
- Set `PUBLIC_BASE_URL` before printing site QR signs: the code embeds that address.
- Before committing, check the test run itself succeeded (`pytest -q > log && tail -1 log`); piping pytest into `tail` hides a failure from `&&`.
- `COMMS_SAFE_MODE` defaults to `on` (locked). Tests set it to `off` so each organisation's switch can be exercised; `e2e/run.sh` leaves it locked.
- Provider adapters are in `backend/app/integrations.py`; tests replace `integrations.transport` with an `httpx.MockTransport`. Nothing has been run against a real provider.
- Commands: `cd backend && .venv/bin/python -m pytest -q`; `cd frontend && npm run build`; `./e2e/run.sh`.
- New migration: `cd backend && .venv/bin/python -m alembic upgrade head && .venv/bin/python -m alembic revision --autogenerate -m "<what>" --rev-id 000N`.
- Regenerate OpenAPI: `cd backend && .venv/bin/python -c "import json; from app.main import app; json.dump(app.openapi(), open('../docs/openapi.json','w'), indent=1, sort_keys=True)"`.
