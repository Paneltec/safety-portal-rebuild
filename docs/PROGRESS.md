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

## Next, in order

1. **Stage 4**: workers register (separate from users, Simpro link fields), HR privacy, certifications and kinds, inductions matrix, suppliers and contractors, renewal links, sites with QR and visitors, daily jobs. Then wire the site, job and worker pickers and the certification gate into forms and registers.
1a. Carried forward: bulk pre-start import (IMP-01), reference catalogues (REF-01), AI drafting, positions and asset-type assignment rules, authorised corrections after submit.
2. Stage 5 onwards as listed in `STAGED_PLAN.md`.

## Not verified

- The Docker Compose deployment has not been run (no Docker daemon in the build environment). The API, database migrations and web app were run directly.

## Working notes for whoever continues the build

- The reconstruction pack is the only source. Read the relevant operations in `api_contracts.json` and models in `request_models.json` before each part; record every deviation as a decision in `ADR-001-architecture.md`.
- A part is finished only with: migration, handlers with permission and tenant checks, tests for allowed and denied paths across two organisations, screens with empty/loading/denied/error states, a step in `e2e/walkthrough.py`, ledger entries in `docs/ledger/update_ledger.py`, and a refreshed `docs/openapi.json`.
- Commands: `cd backend && .venv/bin/python -m pytest -q`; `cd frontend && npm run build`; `./e2e/run.sh`.
- New migration: `cd backend && .venv/bin/python -m alembic upgrade head && .venv/bin/python -m alembic revision --autogenerate -m "<what>" --rev-id 000N`.
- Regenerate OpenAPI: `cd backend && .venv/bin/python -c "import json; from app.main import app; json.dump(app.openapi(), open('../docs/openapi.json','w'), indent=1, sort_keys=True)"`.
