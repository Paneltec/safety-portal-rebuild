# Data model

IDs are opaque strings. Every tenant-owned table carries `org_id`, and every query filters on it. Instants are stored in UTC with time zone; date-only business fields will be `DATE`. The schema is created only by Alembic migrations (`backend/migrations`), and a test fails if the models and migrations drift apart.

## Tables built so far

| Table | Purpose | Key rules |
|---|---|---|
| `organisations` | Tenant boundary: name, time zone, portal addresses, contact details, logo, account holder, payroll owner | Created with the eight built-in roles |
| `document_folders` | Nested library folders | Unique name per parent (case-insensitive, enforced in the API); soft delete only when empty |
| `documents` | A library file: display name, folder, current bytes, expiry, tags, archive and lost states | Identity is the id, not the name |
| `document_revisions` | Every set of bytes a document has had | Unique `(document_id, revision)`; never overwritten |
| `form_templates` | Form definition: working draft schema, status, published version number | Draft edits never touch published versions |
| `form_template_versions` | Immutable published snapshots | Unique `(template_id, version)` |
| `form_submissions` | Answers, status, revision, author, launch provenance | Unique `(org_id, submitted_by, client_submission_id)` for idempotent retries; points at the version it was filled in on |
| `form_submission_files` | Photos, attachments and signatures per field | Bytes in `stored_files`; reachable only through the submission |
| `stored_files` | Metadata for uploaded bytes: hash, size, MIME type, original name, purpose | Bytes on the files volume by organisation and SHA-256; identity separate from name |
| `companies` | Divisions inside an organisation (for example Paneltec, Viatec) | Unique `(org_id, code)` |
| `roles` | Role catalogue: built-in (read-only) and custom | Unique `(org_id, role_id)`; `permissions` is `{resource: {action: bool}}`; soft delete |
| `users` | Login identity | Email unique and lower-case; `status` invited/active/disabled; `worker_id` link is separate from the account; soft delete before hard delete |
| `user_permission_overrides` | Explicit per-user allow/deny cells with reasons | One row per user; survives role changes |
| `permission_presets` | Named bundles of cells | Per organisation; soft delete |
| `user_sessions` | One row per sign-in | Token stored as SHA-256 hash only; expiry, idle timeout, revoke reason, IP and device |
| `auth_tokens` | Single-use invitation and reset links | Hash only; expiry; issuing a new link revokes the previous one |
| `audit_log` | Actor, action, target, before/after detail, request ID | Secrets are stripped before writing |
| `outbox_messages` | Record of every outbound message | Stage 1 only ever writes `held`; no body or link token is stored |

## Relationships to preserve as later stages add tables

```
organisation ─┬─ company ── user ── worker ── Simpro employee
              ├─ role ── user                └─ certifications, inductions, leave, timesheets, pay profile
              ├─ site ── assignments, visits, pre-starts, diary, incidents
              ├─ asset ── Navixy tracker, fuel card, meter history, services, QR
              ├─ form template ── assignment rules ── submissions ── register projections
              └─ pay run ── source timesheets/leave ── frozen lines ── payslips ── completion evidence
```

- A user and a worker are different records. Creating one never creates the other.
- Documents reference stored bytes (hash, size, MIME type), not just file names.
- Imports upsert by organisation plus stable source key; a retry never duplicates.
- Locked diesel weeks, finalised pay runs and submitted form snapshots are immutable; corrections are new revisions.
