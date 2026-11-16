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
| `form_assignments` | Who may fill in a form: role, person or company rules | Unique `(template_id, target_kind, target_id)`; no rules means open to all form users |
| `safety_records` | One entry in any safety register: kind, title, status, business date, the schema it was captured on, answers, version links | Unique `(org_id, source_submission_id)` so a form projects once; soft delete; archive batch id |
| `safety_record_files` | Photos, attachments and signatures of a record | Reachable only through the record |
| `safety_record_events` | Append-only history: created, edited (with earlier answers), submitted, reviewed, status changes, archive | Never rewritten |
| `safety_assignments` | Who a SWMS is assigned to | Unique `(record_id, target_kind, target_id)` |
| `safety_acknowledgements` | A person's sign-off on one SWMS version | Unique `(record_id, user_id)` |
| `workers` | The operational person record, separate from a login | `source_data` (last import) and `locally_edited` (hand-edited imported fields); unique `(org_id, source_system, source_company, source_id)` for imports; `details` JSONB (address, emergency contact, notes, masked hints); `protected` is encrypted JSON (birth date, TFN, USI); active flag, `archived_at`, soft delete |
| `certification_kinds` | Per-organisation certificate types with aliases | Unique `(org_id, slug)`; slug never changes; retire with `active=false`; `category` certificate or induction (induction kinds are the matrix columns) |
| `certifications` | One certificate held by a worker | `supersedes_id` / `superseded_by_id` chain keeps renewals; `no_expiry` explicit; evidence in `stored_files`; archive and soft delete |
| `sites` | A place where work happens | Unique `scan_token` (the QR secret, rotatable); unique source key for imports; `signon_questions` JSONB; active flag; soft delete |
| `site_attendance` | One visit by a signed-in worker or a visitor without a login | Partial unique `(site_id, user_id)` while open (workers); partial unique `(site_id, client_ref)`; answers stored with the wording asked; sign-out source and actor; archive and soft delete |
| `suppliers` | One directory of external organisations; `is_contractor` marks those who do work for us | Unique source key for imports; `source_data` (last import) kept apart from local columns; `locally_edited` lists hand-edited fields; archive and soft delete |
| `supplier_notes`, `supplier_tasks`, `supplier_members` | Local panels on a supplier | Never touched by an import; soft delete |
| `compliance_doc_types` | What can be asked of a supplier | Unique `(org_id, slug)`; `required_for_contractors`; retire with `active=false` |
| `supplier_documents` | Compliance documents held | `superseded_by_id` keeps earlier versions; source office or renewal |
| `renewal_links` | A scoped request for documents from a supplier or worker | Unique `token_hash` (token never stored); status, expiry, round number; outbox message reference |
| `renewal_uploads` | Files supplied through a link and the office's decision | Per round; `replaced_at` when re-uploaded; `result_id` points at the document or certificate created |
| `induction_exemptions` | A worker explicitly marked "not applicable" for one induction | Unique `(worker_id, kind_slug)` |
| `training_records` | Courses a worker has completed | Evidence in `stored_files`; soft delete |
| `worker_hr_documents` | Private and confidential files on a worker | File immutable; archive and soft delete; opens are audited |
| `daily_jobs` | A job issued to one worker for one day | Partial unique `(worker_id, job_date)` while issued or accepted; worker and issuer names kept as issued; linked form template ids; outbox message reference |
| `stored_files` | Metadata for uploaded bytes: hash, size, MIME type, original name, purpose | Bytes on the files volume by organisation and SHA-256; identity separate from name |
| `companies` | Divisions inside an organisation (for example Paneltec, Viatec) | Unique `(org_id, code)` |
| `roles` | Role catalogue: built-in (read-only) and custom | Unique `(org_id, role_id)`; `permissions` is `{resource: {action: bool}}`; soft delete |
| `users` | Login identity | Email unique and lower-case; `status` invited/active/disabled; `worker_id` links to `workers` (unique: one login per worker); `supplier_id` scopes an external representative to one supplier; soft delete before hard delete |
| `user_permission_overrides` | Explicit per-user allow/deny cells with reasons | One row per user; survives role changes |
| `permission_presets` | Named bundles of cells | Per organisation; soft delete |
| `user_sessions` | One row per sign-in | Token stored as SHA-256 hash only; expiry, idle timeout, revoke reason, IP and device |
| `auth_tokens` | Single-use invitation and reset links | Hash only; expiry; issuing a new link revokes the previous one |
| `audit_log` | Actor, action, target, before/after detail, request ID | Secrets are stripped before writing |
| `outbox_messages` | Record of every outbound message | States held, queued, sent, simulated, failed, cancelled; hold code and reason; text stored only when it holds no one-time link; dedupe key; soft delete |
| `outbox_attempts` | Each attempt to deliver a message | Outcome and a redacted detail |
| `integration_configs` | One provider connection per organisation | Unique `(org_id, kind)`; settings JSON; secrets encrypted with the names set kept beside them; mode off/simulator/live; last test result and capabilities; last sync kept separately |
| `notification_reads` | A person has read one notification | Unique `(user_id, notification_id)`; the notifications themselves are computed |
| `import_runs` | One run of an import: mode, dry run or committed, counts, per-company results | Previews are recorded but never count as a sync |
| `import_review_items` | An incoming record that looks like one already on file | Unique `(org_id, resource, source_system, source_company, source_id)`; status open, linked, created or ignored |
| `assets` | A vehicle, item of plant, trailer or piece of equipment | Unique `(org_id, source_system, source_id)` for provider trackers; unique `scan_token`; `odometer_km` and `engine_hours` separate and nullable; `meter_review` holds a lower provider reading awaiting a person; `source_missing` flags a tracker no longer listed |
| `equipment_categories` | Named groups of equipment | Seeded with six defaults on first use; cannot be removed while in use |
| `asset_documents` | Photos, manuals and certificates kept with an asset | Optional issue and expiry dates |
| `asset_meter_readings` | Every odometer and hour-meter reading | `source` manual, navixy, simulator, service, fuel or reset; `applied=false` rows are evidence that was not used |
| `asset_schedules` | A recurring service for one asset | Any of `interval_days`, `interval_km`, `interval_hours`; baseline values; no stored due date |
| `asset_service_records` | Work done: service, repair, inspection | `schedule_name` frozen at the time; `cost` numeric(12,2); checklist JSON |
| `fuel_transactions` | One fill | Unique `(org_id, source_system, source_id)`; `litres` numeric(12,3); `source_unit_price`/`source_total` are the supplier's figures; `report_rate` numeric(8,4) and `report_cost` numeric(12,2) are written once; `attribution` direct, card, manual or unlinked; `raw` keeps the original row |
| `fuel_weekly_prices` | The diesel rate for one Monday-to-Sunday week | Unique `(org_id, week_start, simulated)`; seven `daily_cents`; never updated or deleted |
| `fuel_cards` | A fuel card and what it is assigned to | Unique `(org_id, card_number)`; created when first seen |
| `fuel_batches` | One import run | `via` csv, api or simulator; file hash; timezone used; counts, errors and warnings |
| `fuel_anomalies` | One reason a fill needs a look | Unique `(transaction_id, rule)`; status open, resolved or dismissed with note, actor and time |
| `provider_calls` | Calls made to a rate-limited provider | Counted per minute, hour and day before each SmartFill call |

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
