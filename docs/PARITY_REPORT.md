# Parity report

Generated 2026-11-16 by `docs/ledger/parity_report.py` from `endpoint_coverage.csv` and `feature_coverage.csv`. It compares this rebuild with the written reconstruction pack. The original software was never seen, run or copied.

## In one paragraph

The pack lists 62 required capabilities. 1 is recorded as fully built, 56 as partly built (the working core is there, with named gaps), and 5 as not built. Of the 1129 server operations in the ledger, 625 are built with tests, 5 deliberately answer with a refusal, 17 are historical, and 482 are not built. "Partly built" is the honest label for most areas: this is a working system with real gaps, not a finished copy.

## Test results when this report was written

- Automated tests (server, against PostgreSQL): 285 passed, 0 failed (16 Nov 2026)
- Browser walkthrough (`e2e/run.sh`): 17 of 17 steps passed (16 Nov 2026), in a desktop browser including phone-sized windows
- These are the results of the runs named above. They do not cover anything in the "Not verified" list below.

## Server operations by state

| State | Operations |
|---|---|
| Built and tested | 625 |
| Not built | 482 |
| Old address kept working | 10 |
| Removed in the original; not rebuilt | 7 |
| Deliberately unavailable (answers with a plain refusal) | 5 |

118 of these operations are new in this rebuild (they have no counterpart in the pack), usually because several original operations were replaced by one.

## Required capabilities

| Id | Area | State | What is built, and what is not |
|---|---|---|---|
| CORE-01 | Identity and tenants | Partly built | Organisation/user/company/role scope and file ownership built. Worker and site scope arrive with those modules. |
| AUTH-01 | Login and account recovery | Partly built | Built: login, lockout, invitation, reset, password change, forced change, sessions, expiry, idle timeout, session admin. Outstanding: mobile PIN, admin-console PIN, Simpro sign-in, emailing links (held in outbox). |
| PERM-01 | Access administration | Partly built | Built: custom roles, action matrix, per-user overrides, presets, audit, grant guards. Outstanding: role form allowlist, payroll named grants, phone module allocation. |
| NAV-01 | Office navigation | Partly built | Shell with permission-filtered menu for built screens only. Preferences, app directory and version display outstanding. |
| DASH-01 | Dashboard | Partly built | Built: permission-scoped figures for certificates, leave, safety registers, renewals, visitors, service, fuel checks, missing library files, outbox and timesheets, each with a definition and drill-down; recent activity; search by name and title. Outstanding: AI briefing, apps directory, per-module chart dashboards, text search inside files. |
| AI-01 | Ask and AI capture | Not built | Permission-scoped retrieval/citations, drafts, review and unavailable state |
| SWMS-01 | SWMS | Partly built | Built: capture on the SWMS form (job steps table, PPE, emergency, legislation, source file), draft/submit/approve/changes/reject, assignments to roles/people/companies, per-version acknowledgements, new versions with supersede, version chain, PDF, archive. Outstanding: AI draft, paste/scan/docx import, asset-type assignment, attendance sheet, recycle bin and version diff. |
| PRE-01 | Daily pre-starts | Partly built | Built: pre-start register with crew sign-on, hazards, vehicle (manual until Navixy), meter, GPS, signature, review cycle, PDF, projection of pre-start form templates. Outstanding: Navixy vehicle list and worker ids (stages 4-6), bulk URL/PDF/ZIP import (IMP-01). |
| DIARY-01 | Site diary | Partly built | Built: daily diary with raw notes, crew, activities, issues, photos, review, PDF. Outstanding: AI structuring of raw notes (AI stage). |
| INC-01 | Incidents and hazards | Partly built | Built: incidents and hazards registers shown as one surface with a type switch, categories, severity, people involved, evidence photos, follow-up actions, open/in progress/closed with resolution note, history that keeps original answers, category counts, bulk archive, PDF. Outstanding: root-cause catalogue, customer-service issues, bulk PDF export. |
| INSP-01 | Inspections | Partly built | Built: inspection register with pass/fail/NA checklist where a fail needs a note, failures surfaced on list, detail and PDF, corrective actions, signature, GPS, review. Custom checklists come from form templates of the inspection category. |
| RISK-01 | Risk and SSRA | Partly built | Built: risk assessment and SSRA as separate registers; scores computed only from valid 1-5 answers and always shown with the rule. Outstanding: editable master risk catalogue (REF-01). |
| FORM-01 | Form engine | Partly built | Built: all 20 field families with a documented value shape and server validation, template editor with preview, immutable published versions, staged draft/submit, photos/attachments/signatures stored separately and linked, idempotent retry, read-only record view from the schema snapshot, definition-only import/export. branded PDF from the saved version with embedded photos and signatures and honest missing-file notes. Outstanding: authorised corrections after submit, scoped PDF share tokens, site/job/asset pickers and Navixy vehicle list (need later stages), AI draft. |
| FORM-02 | Assignments | Partly built | Built: role/user/company assignment with effective preview; certification double-switch gate enforced on start and on submit, including expired, unknown-expiry and missing certificates and unlinked logins. Outstanding: positions and asset-type assignment. |
| IMP-01 | Bulk imports | Not built | URL/PDF/ZIP staging, dryrun, mapping, dedup, cancel/resume |
| DOC-01 | Document library | Partly built | Built: folders (nest, move, rename, delete when empty), upload with type allowlist and content check, hash/size/type, private authorised download, inline preview for PDF and images, rename, move, expiry, tags, archive/restore, delete, revision history, missing-content state with repair or record-as-lost, counts, name/tag search. Outstanding: extracted-text search and AI retry, categories, generated per-worker and per-supplier folders, sharing (historical/optional), reorganise tool. |
| DOC-02 | Audit exports | Partly built | Built: audit packs with manifest, per-file SHA-256, scoped download and soft remove; activity log with CSV. Outstanding: attached files and PDFs inside a pack, background progress for very large packs, PDF rendering of a pack. |
| WORK-01 | Workers | Partly built | Built: roster (active/inactive/archived), details, photo, login link and provisioning, self-service edits, change history, duplicate warning, Simpro import by source key with review queue (unverified against a real Simpro). Outstanding: ID card/QR, mobile PIN, worker documents from Simpro. |
| WORK-02 | HR and privacy | Partly built | Built: birth date, TFN and USI encrypted at rest, masked in listings, permissioned audited reveal; HR documents listed to HR viewers, opened only with the reveal permission, every open audited, archive and delete. Outstanding: HR spreadsheet import and link tools (the baseline's standalone HR roster is not recreated). |
| CERT-01 | Certifications | Partly built | Built: kinds with alias normalisation, issue/expiry, evidence files, valid/expiring/expired/unknown/missing, renewal that keeps earlier evidence, register whose totals match its filters, own view, renewal links for workers. Outstanding: reminders (stage 5), bulk actions. |
| IND-01 | Inductions and training | Partly built | Built: induction types (add, rename, retire), worker-by-induction matrix with date, does-not-expire, not-applicable and clear, evidence, own row on the phone, CSV export, completed training with evidence, audit on every change. Outstanding: spreadsheet import wizard, printable matrix PDF, column clean-up tools (not needed without legacy data). |
| SUP-01 | Suppliers and contractors | Partly built | Built: one supplier/contractor directory with search, status tabs, ABN check, local notes, tasks and members, compliance documents with expiry states and history, missing required documents, external representative scope. Outstanding: Simpro vendor import and address lookup (stage 5), supplier QR induction (4d), document folders. |
| REN-01 | Renewal links | Built and tested | Scoped expiring revocable links for suppliers and workers; validated uploads; office accept/reject per file with reasons; reissue rounds; history and audit kept; delivery state shown (held until email is connected in stage 5). |
| SITE-01 | Sites and visitors | Partly built | Built: sites register, QR code and printable sign, sign-on questions, worker and visitor sign-in/out (idempotent), live occupancy, history, office sign-out, visitor register with corrections, archive, delete and CSV export; QR token reaches one site only. Outstanding: Simpro site import (stage 5), bulk archive by criteria, GPS distance checks, migration mapping for legacy sign-in collections (no legacy data in a rebuild). |
| JOB-01 | Daily jobs | Partly built | Built: issue to one or many workers with site, start time, truck, crew, instructions and linked forms; trial run; explicit replace; one active job per worker per day; accept, decline with reason, mark done; My work and Home card; job picker in forms; SMS prepared and held. Outstanding: PDF/SMS job import with confirmation, job site sign-on link, delivery once SMS is connected (stage 5). |
| FLEET-01 | Fleet | Partly built | Built: register for vehicles, plant and trailers with filters and paging; local identity separate from the Navixy tracker id; Navixy tag selection; last position and last-heard time; odometer and hour meter kept apart with history; QR labels (PNG, PDF, bulk, rotate). NOT VERIFIED against a real Navixy account. Outstanding: trips and map, the three Navixy dashboards, NFC/UHF pairing, public (signed-out) scan page. |
| SERV-01 | Asset service | Partly built | Built: meter history; schedules by days, kilometres and hours (whichever first) with warning margins; status ok, due soon, overdue or cannot-tell; service, repair and inspection records with meters, cost and a checklist; recording a service restarts its schedule; schedule edits never rewrite history; fleet-wide due list; reminders with preview and dedupe. Outstanding: attachments on a schedule or record, the printable service check sheet (PDF and templates), technician picker, assigning a schedule to a person from the screen. |
| EQUIP-01 | Equipment | Partly built | Built: category catalogue, equipment register (as assets of kind equipment), status, where kept, inspection dates with overdue and due-soon states, certificates and documents with expiry. Outstanding: the legacy plant-maintenance catalogue and its re-import. |
| FUEL-01 | Fuel import | Partly built | Built: CSV import with preview, stated timezone, row-level errors and batch history; SmartFill import through the adapter; upsert on the provider's transaction id (gaps filled, conflicts counted, never duplicated); attribution labelled direct (registration), card, manual or unmatched; card assignment with optional re-match; unmatched fuel stays in totals. SmartFill is NOT VERIFIED against the real service. Outstanding: card date windows and worker cards, deleting an import batch, scheduled import. |
| FUEL-02 | Weekly diesel | Partly built | Built and tested against the pack's fixture (200..206 cents gives 2.0300; 10 L = 20.30, 25 L = 50.75, total 71.05): Monday to Sunday weeks in the organisation's timezone, seven source days required, half-up rounding, atomic write-once rate, per-fill cost written once, pending state with unpriced litres disclosed, no fallback price, diesel only, admin entry of the seven prices by hand. NOT VERIFIED: the live FuelCheck request (mocked; its availability and terms must be checked before switching it on). |
| FUEL-03 | Fuel reports | Partly built | Built: per-vehicle default, by day (fill date), driver and card; totals with an explicit not-complete marker; supplier amounts shown separately; CSV download; email as plain text through the outbox. Outstanding: PDF and attachments on the email, drill-down from a report row, per-asset fuel panel on the asset page. |
| FUEL-04 | Anomalies | Partly built | Built: seven plain-language reasons with evidence judged at the time of the fill; one row per fill; no duplicate flags on re-import; resolve, dismiss (reason required), reopen, match to a vehicle; bulk actions with counts; select all matching; a corrected reason closes itself with a note; nothing is sent. Outstanding: filters by driver and site, hiding and restoring dismissed items. |
| LEAVE-01 | Leave | Partly built | Built: own request with hours from working days, overlap refusal, evidence upload, own cancel; office list, entry on a worker's behalf, decision by someone else with a reason to refuse, who is away on a date, summary, settings; cancelling leave a pay run has used is flagged for payroll. Outstanding: leave balances, allocation by a pay run (stage 7c), leave by email, a warning when a daily job is given to someone on leave. |
| TIME-01 | Phone timesheets | Partly built | Built: one record per worker and date; shifts with site and kind of work; hours by category worked out on the server by the pack's allocation rules (all six worked examples reproduced); draft, sent, approved, sent back, locked; optimistic revision; office entry, correction, approval, sending back with a reason, drafts from site sign-ons; nobody approves their own days. Outstanding: client and job catalogue from Simpro, recent clients, unmatched-client review, the phone layout (stage 8). |
| TIME-02 | Office approvals | Partly built | Built in stage 7: office list by week, approve and send back with a reason (not your own), office entry and correction, prefill from site sign-ons, and import of approved days into a pay run that is safe to repeat. Outstanding: bulk actions across weeks. |
| PAY-01 | Payroll access | Partly built | Built: payroll opens only for the configured owner or a named view or edit grant held by an administrator; a role or override never opens it (also tested by writing an override straight into the database); the owner cannot be removed; changes are audited and take effect on the next request. Outstanding: the baseline's separate payroll invitation step. |
| PAY-02 | Employee pay settings | Partly built | Built: per-worker pay settings (rate or salary, employment type, hours, tax scale, reviewed flags, leave weeks and loading, casual rates, opening leave) with revision guard; bank details encrypted, masked, revealed only on request with an audit entry. Outstanding: super fund details, award records, imports of rates and opening balances. |
| PAY-03 | Weekly workbench | Partly built | Built: weekly worksheet from approved timesheets and approved leave, hand-entered hours and adjustments, review ticks, expected-revision saves, stale detection, preview on every read, finalise that freezes inputs, rules, outputs and a hash. Three of the pack's worked rows are reproduced end to end through the API, all nine by the rules. NOT VERIFIED: the withholding table against the Tax Office schedule. Outstanding: allowance and earning lines on screen (the API accepts them), per-division runs. |
| PAY-04 | Pay lifecycle | Partly built | Built: finalise, reopen with a reason (refused once a payment is recorded), numbered extra runs that start from the previous closing balances, history with hashes, locking and release of timesheet days and leave. Runs cannot be deleted. Outstanding: archive view, source fingerprints shown on screen. |
| PAY-05 | Pay reports | Partly built | Built: the pay register as CSV for a finalised run. Outstanding: division and super reports, PDF register, reconciliation views. |
| PAY-06 | Payslips | Partly built | Built: payslip PDF from the frozen figures, marked as a test document; a worker sees only their own, only after release; no delivery by email or text exists. Outstanding: employer branding on the payslip. |
| PAY-07 | Banking and completion | Partly built | Built: completion evidence recorded by hand with reference, date, actor and run hash; corrections keep the earlier entry. Bank (ABA) file export is deliberately refused in this test workbench. Outstanding: employer bank settings, ABA validation and export. |
| PAY-08 | Provider readiness | Partly built | Built: a status route and banner stating that STP, super and bank payment are not connected; the routes that would do them answer with a refusal and an audit entry. Outstanding: nothing is integrated, by design. |
| PAY-09 | Payroll configuration | Partly built | Built: pay rules with validation, revision history and stale-save refusal; earning rule definitions through the API. Outstanding: screens for earning rules and work types, award and fund catalogues, payslip branding. |
| INT-01 | Integration registry | Partly built | Built: one catalogue for Simpro, Navixy, SmartFill, Microsoft 365 and TextMagic; encrypted secrets never returned, kept unless cleared, fail closed without the key; explicit non-destructive test per provider; labelled simulator; status kept separate from sync and delivery. NOT VERIFIED against any real provider (mock transport only). Outstanding: data imports, schedulers, Dropbox, key rotation. |
| INT-02 | Navixy adapter | Partly built | Built: region address, sign-in key stored encrypted and refreshed once when Navixy says it expired, tracker list, tags, states, odometer and hour counters, outage changes nothing, vanished trackers flagged not deleted, simulator labelled. NOT VERIFIED against a real account (mock transport only; the counter route in particular must be confirmed). Outstanding: automatic polling, Navixy groups (tags are used), vehicle records (registration, VIN). |
| INT-03 | Simpro adapter | Partly built | Built: multi-company import of workers, sites and suppliers by company + Simpro id; pagination; bounded retry on rate limits; per-company failure reporting; dry run; hand edits kept; review queue for look-alikes (link, create, ignore); simulator dataset labelled as simulator. NOT VERIFIED against a real Simpro (mock transport only; column names follow the public API documentation and must be confirmed with real credentials). Outstanding: jobs and customers, delta cursor, attachments and ZIP import, position-to-role mapping, scheduled sync. |
| INT-04 | SmartFill adapter | Partly built | Built: the 'parameters' envelope, list or table answers, paging guard (no progress stops; page cap), durable shared call limiter (6 a minute, 60 an hour, 600 a day), simulator labelled and kept out of real totals. NOT VERIFIED against FMT Data (mock transport only; column names in a real answer must be confirmed). Outstanding: scheduled sync, tank levels, Retry-After handling, counting the connection test against the limiter. |
| INT-05 | M365 and TextMagic | Partly built | Built: Microsoft 365 app-only token and sendMail, TextMagic headers and message send, both only through the guarded boundary with safe mode, outbox evidence and no double send. NOT VERIFIED against the real services (mock transport only); no real message has ever been sent by this build. Outstanding: attachments, cost preview before an explicit send. |
| DBX-01 | Dropbox | Not built | OAuth/team namespace/browse/search/activity/live file operations |
| COM-01 | Communications | Partly built | Built: single send boundary; safe mode on by default with a server lock and an explicit-grant switch; automatic callers never send; duplicate keys; daily text limit; outbox with history, retry (never twice), cancel, delete; one-time links sent in memory and never stored; notification bell with per-person read state. Outstanding: compose/send-report screens, attachments, scheduled reminders, real delivery (unverified). |
| PHONE-01 | Phone shell | Partly built | Built: phone layout at /m (home, my work, forms, scan, more) over the same routes and permission checks, with own leave, timesheet, payslips, profile and documents by allocation. Tested only in a desktop browser at 390 and 360 wide, not on real phones. Outstanding: PIN sign-in and onboarding cards, push notifications, weather and briefing. |
| PHONE-02 | QR and preview | Partly built | Built: module allocation with role and person exceptions, a per-person check that explains each hidden module, and scan with a typed-code fallback. The camera reader depends on the phone's browser and is untested on devices. Outstanding: preview that renders the phone screens as that person. |
| PHONE-03 | Offline and builds | Partly built | Built: device queue for own timesheet days and form submissions, sent once on reconnect, safe to resend (client reference on the day; repeated form submit returns the stored record), refusals shown with the reason. Not built: native builds, signing or store artefacts (the download routes say so), offline photos, opening a form with no signal, deep links into a native app. |
| ORG-01 | Organisation branding | Partly built | Built: name, time zone, contact details, validated portal and staff URLs with no host default, logo upload/replace/remove shown in shell and sign-in, companies, account holder. Outstanding: logo and branding in PDFs and email templates (arrive with those features), insurance certificates. |
| OPS-01 | System and releases | Partly built | Built: readiness route, system check with database, database version, file storage, key, backups, sending lock and public address, each reported separately, plus version and build identifier. Not built: updates and rollback from inside the portal, multi-instance scheduler lock, Docker and GitHub checks. The Docker deployment itself has not been run. |
| BACK-01 | Backup and NAS | Partly built | Built: archive with manifest and hashes, verify with distinct verified, failed and could-not-check states, restore rehearsal in a scratch database with an exact comparison, tidy-up with preview, server command for scheduling and for a real restore into an empty database. Not built: scheduling inside the portal, delivery to a second place, NAS agent, destinations, download of archives, agent logs. |
| ADMIN-01 | Diagnostics | Partly built | Built: missing-files list (read-only), active sessions (stage 1), activity log, import run status (stage 5), generated API description. Not built: schema viewer, job watchdogs, a test-data reset (deliberately absent), repair of missing files. |
| REF-01 | Reference catalogues | Not built | Risks/forms/root causes/roles/training/company/maintenance |
| HELP-01 | Manual and schematic | Partly built | Built: written guides for administrators and workers and a backup runbook, matching the current screens. Not built: an in-app manual, reference images, program schematic, overlays, versioned help. |
| LEGAL-01 | WHS references | Not built | Provenance/versioned ingestion/cited scoped retrieval |
| DEL-01 | Delivery and parity | Partly built | Built: source, migrations, generated API description, provider stand-ins, automated tests, browser walkthrough on a throwaway database, environment template without secrets, Docker files, guides, parity report, performance check, decisions list. Not verified: the Docker deployment has never been run. Not built: a one-command synthetic demo seed outside the test and walkthrough data. |

## Server operations by area of the original

Areas with nothing built are listed too, so the gaps are visible.

| Area (original module) | Built and tested | Refused on purpose | Historical | Not built |
|---|---|---|---|---|
| backup_service | 6 | 1 | 0 | 25 |
| dropbox_browse | 0 | 0 | 0 | 18 |
| hr_employees | 0 | 0 | 0 | 16 |
| org_settings | 6 | 0 | 0 | 16 |
| org_url_tiles | 0 | 0 | 0 | 16 |
| simpro_zip_import | 10 | 0 | 0 | 15 |
| integrations_dropbox | 0 | 0 | 0 | 14 |
| document_library_shares | 0 | 0 | 0 | 12 |
| bulk_import_prestarts | 0 | 0 | 0 | 9 |
| ask | 0 | 0 | 0 | 8 |
| asset_service | 15 | 0 | 2 | 8 |
| dashboard | 1 | 0 | 1 | 8 |
| file_pdf | 2 | 0 | 0 | 8 |
| session_timeout | 0 | 0 | 0 | 8 |
| companies | 0 | 0 | 0 | 7 |
| cs_incident | 0 | 0 | 0 | 7 |
| document_library | 18 | 0 | 0 | 7 |
| fleet_fuel | 23 | 0 | 0 | 7 |
| mobile_auth | 0 | 0 | 0 | 7 |
| admin_console_pin | 0 | 0 | 0 | 6 |
| incident_root_causes | 0 | 0 | 0 | 6 |
| integrations_simpro | 9 | 0 | 0 | 6 |
| list_forms | 0 | 0 | 0 | 6 |
| list_roles | 0 | 0 | 0 | 6 |
| master_risks | 0 | 0 | 0 | 6 |
| program_schematic_overlays | 0 | 0 | 0 | 6 |
| roles_catalogue | 7 | 0 | 0 | 6 |
| server | 1 | 0 | 0 | 6 |
| user_prefs | 0 | 0 | 0 | 6 |
| workers_qr | 0 | 0 | 0 | 6 |
| document_categories | 0 | 0 | 0 | 5 |
| mobile_data | 1 | 0 | 0 | 5 |
| swms_extras | 1 | 0 | 2 | 5 |
| swms_phase45 | 0 | 0 | 0 | 5 |
| tile_credentials | 0 | 0 | 0 | 5 |
| users | 12 | 0 | 0 | 5 |
| workers | 18 | 0 | 0 | 5 |
| workspaces | 0 | 0 | 0 | 5 |
| app_updates | 0 | 0 | 0 | 4 |
| assets | 12 | 0 | 0 | 4 |
| health_extras | 1 | 0 | 0 | 4 |
| help_reference_images | 0 | 0 | 0 | 4 |
| help_routes | 0 | 0 | 0 | 4 |
| induction_columns | 0 | 0 | 0 | 4 |
| mobile_daily_jobs_admin | 3 | 0 | 0 | 4 |
| payroll_delivery | 1 | 0 | 0 | 4 |
| payroll_run_register | 2 | 0 | 0 | 4 |
| payroll_workbench | 12 | 0 | 0 | 4 |
| simpro_import_users | 0 | 0 | 0 | 4 |
| workers_inductions | 8 | 0 | 0 | 4 |
| admin_schema | 0 | 0 | 0 | 3 |
| admin_test_activity_reset | 0 | 0 | 0 | 3 |
| ai | 0 | 0 | 0 | 3 |
| asset_navixy_dashboards | 0 | 0 | 0 | 3 |
| fleet | 18 | 0 | 0 | 3 |
| forms | 19 | 0 | 2 | 3 |
| imports | 0 | 0 | 0 | 3 |
| integrations_simpro_workers | 1 | 0 | 0 | 3 |
| mobile_daily_jobs | 4 | 0 | 0 | 3 |
| payroll_award_settings | 0 | 0 | 0 | 3 |
| payroll_banking | 3 | 0 | 0 | 3 |
| payroll_catalog_settings | 0 | 0 | 0 | 3 |
| payroll_employee_records | 2 | 0 | 0 | 3 |
| payroll_fund_settings | 0 | 0 | 0 | 3 |
| suppliers_qr | 0 | 0 | 0 | 3 |
| whs_legislation.api | 0 | 0 | 0 | 3 |
| admin_active_sessions | 3 | 0 | 0 | 2 |
| admin_forms_autoseed | 0 | 0 | 0 | 2 |
| auth | 6 | 0 | 1 | 2 |
| auth_invite | 9 | 0 | 0 | 2 |
| auth_mobile_pin | 0 | 0 | 0 | 2 |
| completed_training | 5 | 0 | 0 | 2 |
| integrations_nas | 0 | 0 | 0 | 2 |
| leave_requests | 12 | 0 | 0 | 2 |
| mobile_home | 1 | 0 | 0 | 2 |
| org_archive_rules | 0 | 0 | 0 | 2 |
| payroll_balance_import | 0 | 0 | 0 | 2 |
| payroll_branding | 0 | 0 | 0 | 2 |
| payroll_work_types | 0 | 0 | 0 | 2 |
| preview_sources | 0 | 0 | 0 | 2 |
| settings_nav | 0 | 0 | 0 | 2 |
| sites_signon_v127 | 8 | 0 | 0 | 2 |
| worker_user_provisioning_routes | 2 | 0 | 0 | 2 |
| admin_forms_import_service_check_sheet | 0 | 0 | 0 | 1 |
| admin_missing_files | 1 | 0 | 0 | 1 |
| admin_purge_test_data | 0 | 0 | 0 | 1 |
| asset_navixy_sync | 1 | 0 | 0 | 1 |
| asset_trip_summary | 0 | 0 | 0 | 1 |
| daily_jobs_batch | 4 | 0 | 0 | 1 |
| dashboards | 0 | 0 | 0 | 1 |
| docs_manual | 0 | 0 | 0 | 1 |
| email_outbox | 6 | 0 | 0 | 1 |
| exports | 4 | 0 | 0 | 1 |
| forms_pickers | 1 | 2 | 0 | 1 |
| fuel_price_settings | 2 | 0 | 0 | 1 |
| incidents_bulk | 0 | 0 | 0 | 1 |
| integrations_textmagic | 1 | 0 | 0 | 1 |
| metrics_routes | 0 | 0 | 0 | 1 |
| mobile_downloads | 0 | 2 | 0 | 1 |
| mobile_onboarding_cards | 0 | 0 | 0 | 1 |
| mobile_sms_parse | 0 | 0 | 0 | 1 |
| payroll | 22 | 0 | 0 | 1 |
| payroll_completion | 2 | 0 | 0 | 1 |
| payroll_payslips | 3 | 0 | 0 | 1 |
| payroll_rate_import | 0 | 0 | 0 | 1 |
| payroll_segments | 4 | 0 | 0 | 1 |
| payroll_worker_payslips | 2 | 0 | 0 | 1 |
| pdf_routes | 0 | 0 | 0 | 1 |
| plant_maintenance | 4 | 0 | 0 | 1 |
| sites_qr | 7 | 0 | 0 | 1 |
| suppliers | 17 | 0 | 0 | 1 |
| visitor_signins | 10 | 0 | 0 | 1 |
| worker_certifications | 13 | 0 | 0 | 1 |
| (new) | 37 | 0 | 0 | 0 |
| asset_meter_history | 2 | 0 | 0 | 0 |
| audit | 4 | 0 | 0 | 0 |
| bulk_permissions | 1 | 0 | 0 | 0 |
| category_counts | 7 | 0 | 0 | 0 |
| certifications | 5 | 0 | 0 | 0 |
| comms_safe_mode | 5 | 0 | 0 | 0 |
| contractors | 8 | 0 | 0 | 0 |
| crud | 64 | 0 | 0 | 0 |
| daily jobs | 5 | 0 | 0 | 0 |
| equipment_register | 16 | 0 | 0 | 0 |
| fleet_fuel_reports | 3 | 0 | 0 | 0 |
| fleet_navixy_tags | 1 | 0 | 0 | 0 |
| fuel_weekly_prices | 3 | 0 | 0 | 0 |
| induction_types | 4 | 0 | 0 | 0 |
| inductions | 1 | 0 | 0 | 0 |
| integrations | 14 | 0 | 1 | 0 |
| integrations_m365 | 2 | 0 | 0 | 0 |
| leave | 2 | 0 | 0 | 0 |
| mobile_modules | 5 | 0 | 0 | 0 |
| mobile_preview | 2 | 0 | 0 | 0 |
| mobile_sites | 0 | 0 | 7 | 0 |
| notifications | 3 | 0 | 0 | 0 |
| overview | 1 | 0 | 0 | 0 |
| payroll_lifecycle | 3 | 0 | 0 | 0 |
| payroll_stp_readiness | 1 | 0 | 0 | 0 |
| permission_presets | 7 | 0 | 0 | 0 |
| phone | 1 | 0 | 0 | 0 |
| renewals | 18 | 0 | 0 | 0 |
| session_history | 1 | 0 | 0 | 0 |
| sites | 10 | 0 | 0 | 0 |
| sites_admin | 4 | 0 | 0 | 0 |
| sites_qr_v132dk | 1 | 0 | 1 | 0 |
| supplier_panels | 13 | 0 | 0 | 0 |
| system | 3 | 0 | 0 | 0 |
| training | 2 | 0 | 0 | 0 |

## Not verified

Built, but not proven in the conditions it will be used in:

- The Docker Compose deployment has not been run (no Docker daemon in the build environment). The API, database migrations and web app were run directly.
- No real provider has been contacted. Simpro, Navixy, SmartFill (FMT Data), FuelCheck Tasmania, Microsoft 365 and TextMagic are each tested against a stand-in built from the pack's description. Field names, paging and error shapes in real answers must be confirmed with real credentials before relying on them. In particular: the Navixy counter (odometer and hour) call, the column names in a SmartFill answer, and whether the FuelCheck price list can be read at all.
- The weekly tax withholding table in `backend/app/payroll_engine.py` is the one the baseline software used (paydays 1 July 2026 to 30 June 2027). It reproduces the pack's examples but has not been checked against the Tax Office's published schedule. The overtime, night and public holiday multipliers and the 12% super rate are the baseline's defaults, not a ruling on any award.
- The pay walkthrough in `e2e/walkthrough.py` uses last week's dates. After 30 June 2027 the built-in withholding table no longer covers the payday, so that walkthrough (not the fixed-date tests) will report tax as not worked out until the table is updated.
- Performance was measured once, by one simulated person, on a small cloud build machine with the database on the same machine (`docs/PERFORMANCE.md`). It has not been measured on the NAS, over a network, with stored files, or with several people at once.
- Backups: the commands in `docs/BACKUP_RESTORE.md` that use Docker have not been run. The restore command was tested into a scratch database on the same database server, not onto a separate server. A NAS shared folder has not been used as the backup folder.
- The phone app has only been run in a desktop browser set to phone sizes (390x844 and 360 wide). It has not been installed on a real Android phone or iPhone. Adding to the home screen, the camera QR reader (which depends on the phone's browser; a typed code is the fallback) and behaviour when the signal really drops are untested on devices. Installing needs the portal served over HTTPS.
- There is no native Android or iPhone app, no push notifications, and no offline photo capture. Wide tables scroll sideways on a phone rather than being redrawn as cards.

## Things this rebuild will not do, by design

- Send a payment, lodge STP, submit super, or email or text a payslip. The pay area is a test workbench and says so on every screen.
- Send any email or text while the server's safe mode lock is on (the default).
- Restore a backup over the running portal, reset or bulk-delete test data, or change the activity log.
- Show a simulated connection as a live one.
- Offer an Android or iPhone app download.

## Where to look next

- `docs/PROGRESS.md`: what each stage delivered and what is left over.
- `docs/ADR-001-architecture.md`: every decision and deviation, numbered.
- `docs/ledger/endpoint_coverage.csv`: one row per server operation with its test evidence.
- `docs/PERFORMANCE.md`: timings on a synthetic dataset.
- `docs/BACKUP_RESTORE.md`, `docs/ADMIN_GUIDE.md`, `docs/WORKER_GUIDE.md`.
