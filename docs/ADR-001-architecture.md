# ADR-001: Architecture and recorded decisions

Status: accepted, 15 November 2026. Independent reconstruction from the written pack only; the original source was not read.

## Stack

| Layer | Choice | Why |
|---|---|---|
| API | Python 3.13, FastAPI, Pydantic 2 | Matches the contract style in the pack (`/api`, bearer token, `{"detail": ...}` errors) and generates OpenAPI from the real handlers. |
| Database | **PostgreSQL 16** (SQLAlchemy 2, Alembic migrations) | See "Why not MongoDB" below. |
| Office web | React 19, TypeScript, Vite, hand-written CSS design tokens | Small dependency surface; fonts are bundled, so the NAS never calls an outside font host. |
| Phone | Planned as an installable web app (PWA) sharing the same API and feature definitions | Decided in the phone stage; recorded here so office and phone stay on one contract. |
| Files | Content-addressed files on a volume with metadata rows (stage 2) | One store instead of several GridFS buckets; hash, size and MIME type recorded per file. |
| Deployment | Docker Compose: `db`, `api`, `web` (nginx serves the app and proxies `/api`) | Fits the existing Portainer/Umbrel workflow. Multi-arch base images run on the ARM NAS. |

### Why not MongoDB

The pack permits another stack if behaviour and external APIs are preserved. PostgreSQL was chosen because:

1. **Every stage can be tested against the real database in the build environment.** MongoDB could not be installed there, and tests against an imitation database would not prove persistence, uniqueness or concurrency rules.
2. The rules that matter most are relational and transactional: one immutable locked diesel week, idempotent imports by source key, frozen pay-run snapshots, optimistic revisions, foreign keys that survive archive and restore. Unique constraints and transactions enforce these in the database rather than in application code.
3. Money and rates use exact `NUMERIC` types.
4. Flexible shapes (form answers, permission matrices, provider payloads) use `JSONB`.

Cost: a later import of existing data is a mapped conversion, not a collection copy. The pack already treats migration as a separately approved step with a mapping.

## Recorded decisions

| ID | Decision | Reason |
|---|---|---|
| D-001 | Bearer tokens are opaque random values. Only a SHA-256 hash is stored, one row per session. | Gives real revocation, session history, idle timeout and "sign out everywhere" without a token-version workaround. API shape (`access_token`, `token_type`, `user`) is unchanged. |
| D-002 | `status` (`invited`/`active`/`disabled`) is the single account state. `activation_status` in responses is derived from it. | Two independent state fields can disagree. |
| D-003 | The built-in `admin` role holds every *supported* action on ordinary resources. | The observed admin matrix lacked `open` on site visitors, which reads as an omission rather than intent. |
| D-004 | Built-in `manager` starts as a copy of `supervisor`. | The pack lists `manager` as a role key but gives no default matrix. Least privilege until the owner says otherwise. |
| D-005 | `payroll` is never granted by a role or an override, only to the organisation's payroll owner (stored on the organisation; the account holder by default) and, later, named grants. | Spec section 14: an ordinary admin does not gain payroll access. Overrides the broad observed admin default. |
| D-006 | `comms_safe_mode` is never granted by a role default, only by an explicit per-user override set by an admin. | Spec section 4: a deliberately restricted capability. |
| D-007 | A non-admin can never grant access they do not hold (roles, overrides, presets, role assignment), cannot change their own role or permissions, and cannot alter an admin. | Closes privilege escalation through `users.edit`. A safeguard added by the reconstruction. |
| D-008 | `POST /api/users` creates an invited account. The baseline returned 410 and relied on Simpro import only. | Spec section 4 requires authorised invitation as well as import. It never creates a worker record. |
| D-009 | `GET /api/users` returns `{items, total, limit, offset}` instead of a bare array. | Spec section 3 requires bounded pagination with a filtered total. Intentional contract change, listed in the endpoint ledger. |
| D-010 | Custom roles are described as `resource.action` tokens. | The pack names `permission_tokens` but not their format. |
| D-011 | Applying a preset stores only the cells that differ from the person's own role; the role is not changed, and specially controlled cells are untouched. | The pack gives the response shape but not the semantics. This keeps later role changes effective. |
| D-012 | One-time links are shown once to the admin and recorded in the outbox as **held**. Nothing is emailed. | Email delivery is a later stage; a simulator must never look like a successful send. |
| D-013 | Link addresses use `PUBLIC_BASE_URL`, otherwise the admin browser's Origin. | Stops an internal API host leaking into invitation links behind a proxy. No preview-host default exists. |
| D-014 | Cross-organisation IDs answer 404, not 403. | Does not confirm that another tenant's record exists. |
| D-015 | Failed sign-ins lock the account for 5 minutes after 5 tries (423), as observed. Per-address throttling is deferred to the operations stage. | Recorded as outstanding in the ledger. |
| D-016 | An empty install is claimed through a first-run setup screen protected by `SETUP_KEY` from the server configuration. The person who completes it becomes the **account holder**. | No identity is hardcoded in the build, and a stranger who reaches a fresh install cannot claim it. |
| D-017 | The account holder must stay an active admin. Other admins cannot edit, disable, delete, demote, reset or sign out that account; only the holder can transfer it. Transfer does not move payroll ownership. | Protects the owner's account from takeover by another admin. |
| D-018 | Uploaded bytes are stored on a volume addressed by organisation and SHA-256, with a metadata row per file. Content is sniffed; a mislabelled file is refused. Missing bytes answer 410, never an empty file. | Spec section 8. Replaces the several GridFS buckets with one store. |
| D-019 | The logo route needs no session and serves only files stored as a logo. Sign-in branding is shown only when the install has a single organisation. | Branding must appear before sign-in and in reports; nothing else is reachable that way. |
| D-020 | Organisation contact fields live in one JSON column; companies keep stable ids across renames and cannot be removed while people are assigned. `slug` and `default_workspace_id` are deferred until sites exist. | Keeps foreign keys intact. |

## Security posture (applies to every stage)

- Organisation, role and record scope are checked on the server for reads, writes, downloads and exports. The menu only mirrors those checks.
- Request bodies cannot choose an organisation or act as another user; scope comes from the session.
- Passwords are hashed with Argon2id. Tokens, passwords and secrets never enter the audit log.
- Payroll stays test-only and no message, payment or lodgement is ever sent by the build; the block lives in the backend.
