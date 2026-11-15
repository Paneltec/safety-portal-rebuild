# Data model

IDs are opaque strings. Every tenant-owned table carries `org_id`, and every query filters on it. Instants are stored in UTC with time zone; date-only business fields will be `DATE`. The schema is created only by Alembic migrations (`backend/migrations`), and a test fails if the models and migrations drift apart.

## Stage 1 tables (built)

| Table | Purpose | Key rules |
|---|---|---|
| `organisations` | Tenant boundary: name, time zone, staff login URL, public portal URL | Created with the eight built-in roles |
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
