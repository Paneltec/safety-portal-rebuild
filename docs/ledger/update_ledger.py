"""Record build status in the two coverage ledgers. Re-runnable; add entries as stages land."""
import csv
from pathlib import Path

HERE = Path(__file__).parent
T_AUTH, T_USERS, T_RST = "tests/test_auth.py", "tests/test_users.py", "tests/test_roles_sessions_tenancy.py"
T_ORG = "tests/test_org_setup_files.py"
T_DOC = "tests/test_documents.py"
T_FORM = "tests/test_forms_api.py; tests/test_forms_engine.py"
T_SAFE = "tests/test_safety.py"

# (method, baseline path) -> (classification, new handler, tests, status, decision)
DONE = {
    ("GET", "/api/"): ("public-scoped", "org.root", T_AUTH, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/auth/login"): ("public-scoped", "auth.login", T_AUTH, "IMPLEMENTED_TESTED", "D-001 opaque session token"),
    ("GET", "/api/auth/me"): ("authenticated-own", "auth.me", T_AUTH, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/auth/logout"): ("authenticated-own", "auth.logout", T_AUTH, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/auth/refresh"): ("authenticated-own", "auth.refresh", T_AUTH, "IMPLEMENTED_TESTED", "Rotates the token"),
    ("POST", "/api/auth/change-password"): ("authenticated-own", "auth.change_password", T_AUTH, "IMPLEMENTED_TESTED", "Typed body current_password/new_password"),
    ("POST", "/api/auth/update-profile"): ("authenticated-own", "auth.update_profile", T_AUTH, "IMPLEMENTED_TESTED", "No token re-issue needed with D-001"),
    ("POST", "/api/auth/forgot-password"): ("public-scoped", "auth.forgot_password", T_AUTH, "IMPLEMENTED_TESTED", "D-012 message held, never sent"),
    ("POST", "/api/auth/invite/validate"): ("public-scoped", "auth.invite_validate", T_AUTH, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/auth/invite/redeem"): ("public-scoped", "auth.invite_redeem", T_AUTH, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/auth/reset/validate"): ("public-scoped", "auth.reset_validate", T_AUTH, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/auth/reset/redeem"): ("public-scoped", "auth.reset_redeem", T_AUTH, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/auth/signup"): ("public-scoped", "auth.signup_retired", T_AUTH, "HISTORICAL_COMPATIBILITY", "Retired: always 410"),
    ("GET", "/api/users"): ("team/resource", "users.list_users", T_USERS, "IMPLEMENTED_TESTED", "D-009 paginated envelope"),
    ("POST", "/api/users"): ("team/resource", "users.create_user", T_USERS, "IMPLEMENTED_TESTED", "D-008 authorised invite replaces baseline 410"),
    ("GET", "/api/users/{user_id}"): ("team/resource", "users.get_user", T_USERS, "IMPLEMENTED_TESTED", ""),
    ("PATCH", "/api/users/{user_id}"): ("team/resource", "users.update_user", T_USERS, "IMPLEMENTED_TESTED", "D-007 grant guard"),
    ("DELETE", "/api/users/{user_id}"): ("team/resource", "users.delete_user", T_USERS, "IMPLEMENTED_TESTED", "Requires users.delete; hard delete admin only"),
    ("POST", "/api/users/bulk-assign-role"): ("team/resource", "users.bulk_assign_role", T_USERS, "IMPLEMENTED_TESTED", "auto_create_from_position deferred to stage 5"),
    ("POST", "/api/users/{user_id}/force-signout"): ("team/resource", "users.force_signout", T_USERS, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/users/{user_id}/unlock"): ("admin-owner", "users.unlock_user", T_AUTH, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/users/{user_id}/set-password"): ("admin-owner", "users.admin_set_password", T_USERS, "IMPLEMENTED_TESTED", "Admin only; forces change at next sign-in"),
    ("POST", "/api/users/{user_id}/invite"): ("admin-owner", "users.send_invite", T_AUTH, "IMPLEMENTED_TESTED", "D-012 link shown once, delivery held"),
    ("POST", "/api/users/{user_id}/reset-password"): ("admin-owner", "users.send_reset", T_AUTH, "IMPLEMENTED_TESTED", "D-012"),
    ("GET", "/api/users/{user_id}/access-status"): ("admin-owner", "users.access_status", T_USERS, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/users/{user_id}/permissions"): ("team/resource", "users.get_permissions", T_USERS, "IMPLEMENTED_TESTED", ""),
    ("PUT", "/api/users/{user_id}/permissions"): ("team/resource", "users.put_permissions", T_USERS, "IMPLEMENTED_TESTED", "Strict booleans; D-005/D-006/D-007"),
    ("POST", "/api/users/{user_id}/permissions/reset"): ("team/resource", "users.reset_permissions", T_USERS, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/users/{user_id}/permissions/apply-preset"): ("team/resource", "users.apply_preset", T_USERS, "IMPLEMENTED_TESTED", "D-011"),
    ("POST", "/api/permissions/bulk-restrict"): ("team/resource", "users.bulk_restrict", T_USERS, "IMPLEMENTED_TESTED", "color_groups deferred"),
    ("GET", "/api/admin/roles"): ("team/resource", "roles.list_roles", T_RST, "IMPLEMENTED_TESTED", "users.view instead of admin-only"),
    ("POST", "/api/admin/roles"): ("team/resource", "roles.create_role", T_RST, "IMPLEMENTED_TESTED", "D-010"),
    ("GET", "/api/admin/roles/{role_id}"): ("team/resource", "roles.get_role_detail", T_RST, "IMPLEMENTED_TESTED", ""),
    ("PATCH", "/api/admin/roles/{role_id}"): ("team/resource", "roles.patch_role", T_RST, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/admin/roles/{role_id}"): ("team/resource", "roles.delete_role", T_RST, "IMPLEMENTED_TESTED", "Soft delete"),
    ("GET", "/api/admin/roles/{role_id}/assignees-count"): ("team/resource", "roles.assignees_count", T_RST, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/admin/roles/{role_id}/audit"): ("team/resource", "roles.role_audit", T_RST, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/permission-presets"): ("team/resource", "presets.list_presets", T_RST, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/permission-presets"): ("team/resource", "presets.create_preset", T_RST, "IMPLEMENTED_TESTED", ""),
    ("PUT", "/api/permission-presets/{preset_id}"): ("team/resource", "presets.update_preset", T_RST, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/permission-presets/{preset_id}"): ("team/resource", "presets.delete_preset", T_RST, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/permission-presets/{preset_id}/duplicate"): ("team/resource", "presets.duplicate_preset", T_RST, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/permission-presets/{preset_id}/assignees"): ("team/resource", "presets.preset_assignees", T_USERS, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/admin/active-sessions"): ("admin-owner", "sessions.list_active_sessions", T_RST, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/admin/active-sessions/{jti}"): ("admin-owner", "sessions.revoke_session", T_RST, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/admin/active-sessions/bulk-revoke"): ("admin-owner", "sessions.bulk_revoke", T_RST, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/admin/users/{user_id}/session-history"): ("admin-owner", "sessions.session_history", T_RST, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/org"): ("authenticated-own", "org.get_org", T_ORG, "IMPLEMENTED_TESTED", "Insurance blocks arrive with documents"),
    ("PATCH", "/api/org"): ("admin-owner", "org.patch_org", T_ORG, "IMPLEMENTED_TESTED", "D-020; slug and default_workspace_id deferred; insurance deferred"),
    ("GET", "/api/org/companies"): ("authenticated-own", "org.list_companies", T_ORG, "IMPLEMENTED_TESTED", "No self-seeded pilot companies"),
    ("PUT", "/api/org/companies"): ("admin-owner", "org.replace_companies", T_ORG, "IMPLEMENTED_TESTED", "D-020 stable ids"),
    ("POST", "/api/org/logo/upload"): ("admin-owner", "org.upload_logo", T_ORG, "IMPLEMENTED_TESTED", "D-018 content sniffing, 2 MB"),
    ("GET", "/api/org/logo/{gridfs_id}"): ("public-scoped", "org.get_logo", T_ORG, "IMPLEMENTED_TESTED", "D-019 logo-only public route"),

    # ---- document library (stage 2b)
    ("GET", "/api/document-library/counts"): ("team/resource", "documents.counts", T_DOC, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/document-library/counts/missing-binary"): ("team/resource", "documents.missing_binary", T_DOC, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/document-library/files/{file_id}"): ("team/resource", "documents.delete_file", T_DOC, "IMPLEMENTED_TESTED", "Soft delete; needs documents.delete"),
    ("PATCH", "/api/document-library/files/{file_id}"): ("team/resource", "documents.patch_file", T_DOC, "IMPLEMENTED_TESTED", "Adds tags"),
    ("POST", "/api/document-library/files/{file_id}/archive"): ("team/resource", "documents.archive_file", T_DOC, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/document-library/files/{file_id}/download"): ("team/resource", "documents.download_file", T_DOC, "IMPLEMENTED_TESTED", "Inline only for PDF and images; revision query added"),
    ("POST", "/api/document-library/files/{file_id}/mark-gone"): ("team/resource", "documents.mark_gone", T_DOC, "IMPLEMENTED_TESTED", "Only when bytes are really missing"),
    ("POST", "/api/document-library/files/{file_id}/replace-binary"): ("team/resource", "documents.replace_binary", T_DOC, "IMPLEMENTED_TESTED", "Creates a new revision; earlier bytes kept"),
    ("POST", "/api/document-library/files/{file_id}/restore"): ("team/resource", "documents.restore_file", T_DOC, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/document-library/folders"): ("team/resource", "documents.list_root_folders", T_DOC, "IMPLEMENTED_TESTED", "Requires documents.view"),
    ("POST", "/api/document-library/folders"): ("team/resource", "documents.create_folder", T_DOC, "IMPLEMENTED_TESTED", "Duplicate name in same parent is 409"),
    ("GET", "/api/document-library/folders/all"): ("team/resource", "documents.list_all_folders", T_DOC, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/document-library/folders/{folder_id}"): ("team/resource", "documents.delete_folder", T_DOC, "IMPLEMENTED_TESTED", "Refuses a non-empty folder (409)"),
    ("PATCH", "/api/document-library/folders/{folder_id}"): ("team/resource", "documents.patch_folder", T_DOC, "IMPLEMENTED_TESTED", "Cycle check"),
    ("GET", "/api/document-library/folders/{folder_id}/files"): ("team/resource", "documents.list_files", T_DOC, "IMPLEMENTED_TESTED", "Paginated envelope"),
    ("POST", "/api/document-library/folders/{folder_id}/files"): ("team/resource", "documents.upload_files", T_DOC, "IMPLEMENTED_TESTED", "Per-file saved/rejected; type allowlist; duplicate-safe"),
    ("GET", "/api/document-library/folders/{folder_id}/subfolders"): ("team/resource", "documents.list_subfolders", T_DOC, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/document-library/search"): ("team/resource", "documents.search", T_DOC, "IMPLEMENTED_TESTED", "Name and tag search; extracted-text search arrives with AI stage"),

    # ---- form engine (stage 2c)
    ("GET", "/api/forms/templates"): ("team/resource", "forms.list_templates", T_FORM, "IMPLEMENTED_TESTED", "Unpublished forms hidden from people who cannot manage templates; assignment filter arrives with FORM-02"),
    ("POST", "/api/forms/templates"): ("team/resource", "forms.create_template", T_FORM, "IMPLEMENTED_TESTED", "D-023 needs forms.use"),
    ("POST", "/api/forms/templates/import"): ("team/resource", "forms.import_templates", T_FORM, "IMPLEMENTED_TESTED", "Creates drafts; per-entry errors"),
    ("GET", "/api/forms/templates/{template_id}"): ("team/resource", "forms.get_template", T_FORM, "IMPLEMENTED_TESTED", ""),
    ("PATCH", "/api/forms/templates/{template_id}"): ("team/resource", "forms.patch_template", T_FORM, "IMPLEMENTED_TESTED", "Edits the draft only; D-024 publish creates an immutable version"),
    ("DELETE", "/api/forms/templates/{template_id}"): ("team/resource", "forms.delete_template", T_FORM, "IMPLEMENTED_TESTED", "Soft delete; submissions kept"),
    ("GET", "/api/forms/templates/{template_id}/access-check"): ("authenticated-own", "forms.access_check", T_FORM, "IMPLEMENTED_TESTED", "Published and permission checks; assignment and certification gate arrive with FORM-02"),
    ("GET", "/api/forms/templates/{template_id}/submissions"): ("team/resource", "forms.list_submissions", T_FORM, "IMPLEMENTED_TESTED", "Own-only without forms.team_view; paginated"),
    ("POST", "/api/forms/templates/{template_id}/submissions"): ("authenticated-own", "forms.create_submission", T_FORM, "IMPLEMENTED_TESTED", "D-025 staged draft then submit; client_submission_id idempotency"),
    ("GET", "/api/forms/submissions/{submission_id}"): ("team/resource", "forms.get_submission", T_FORM, "IMPLEMENTED_TESTED", "Carries the schema snapshot"),
    ("DELETE", "/api/forms/submissions/{submission_id}"): ("team/resource", "forms.delete_submission", T_FORM, "IMPLEMENTED_TESTED", "Own draft, or forms.delete"),
    ("POST", "/api/forms/submissions/{submission_id}/photos"): ("authenticated-own", "forms.upload_photo", T_FORM, "IMPLEMENTED_TESTED", "Draft only; images only"),
    ("POST", "/api/forms/submissions/{submission_id}/attachments"): ("authenticated-own", "forms.upload_attachment", T_FORM, "IMPLEMENTED_TESTED", "Draft only; images and PDF"),
    ("GET", "/api/forms/submissions/{submission_id}/photos/{stored_name}"): ("team/resource", "forms.download_submission_file", T_FORM, "HISTORICAL_COMPATIBILITY", "Replaced by GET /submissions/{id}/files/{file_id}: files are addressed by id, not stored name"),
    ("GET", "/api/forms/submissions/{submission_id}/attachments/{stored_name}"): ("team/resource", "forms.download_submission_file", T_FORM, "HISTORICAL_COMPATIBILITY", "Replaced by GET /submissions/{id}/files/{file_id}"),
    ("GET", "/api/forms/pickers/workers"): ("authenticated-own", "forms.pick_workers", T_FORM, "IMPLEMENTED_TESTED", "Lists active users until the worker register exists (stage 4); value shape unchanged"),
    ("GET", "/api/forms/pickers/sites"): ("authenticated-own", "forms.pick_sites", T_FORM, "EXPLICITLY_UNAVAILABLE", "Returns available:false until the sites register (stage 4)"),
    ("GET", "/api/forms/pickers/jobs"): ("authenticated-own", "forms.pick_jobs", T_FORM, "EXPLICITLY_UNAVAILABLE", "Returns available:false until daily jobs (stage 4)"),
    ("GET", "/api/forms/fleet/vehicles"): ("authenticated-own", "forms.pick_vehicles", T_FORM, "EXPLICITLY_UNAVAILABLE", "Returns available:false until Navixy (stages 5-6); manual entry allowed and labelled"),
    ("GET", "/api/forms/submissions/{submission_id}/pdf"): ("team/resource", "forms.submission_pdf_report", T_FORM, "IMPLEMENTED_TESTED", "Session-authorised; rendered from the saved version; branded from organisation settings"),
    ("GET", "/api/form-templates/assignments"): ("team/resource", "forms.get_assignments", T_FORM, "HISTORICAL_COMPATIBILITY", "Replaced by GET /api/forms/templates/{id}/assignments (per template)"),
    ("POST", "/api/form-templates/assignments/bulk"): ("team/resource", "forms.put_assignments", T_FORM, "HISTORICAL_COMPATIBILITY", "Replaced by PUT /api/forms/templates/{id}/assignments (replace rules per template)"),
}

# ---- safety registers (stage 3): the same route set exists for every register
REGISTER_PREFIXES = ["swms", "pre-starts", "site-diary", "hazards", "incidents", "inspections", "risk-assessments"]
OPTIONAL = {}
for _p in REGISTER_PREFIXES:
    _b = f"/api/{_p}"
    OPTIONAL.update({
        ("GET", _b): ("team/resource", "safety.list_records", T_SAFE, "IMPLEMENTED_TESTED", "Paginated; own-only without team_view on team-scoped registers"),
        ("POST", _b): ("team/resource", "safety.create", T_SAFE, "IMPLEMENTED_TESTED", "D-030 body is {fields:[{id,value}]} validated against the register's form schema"),
        ("POST", f"{_b}/archive"): ("team/resource", "safety.bulk_archive", T_SAFE, "IMPLEMENTED_TESTED", "Needs the archive right; dry run by default"),
        ("POST", f"{_b}/unarchive-batch/{{batch_id}}"): ("team/resource", "safety.unarchive_batch", T_SAFE, "IMPLEMENTED_TESTED", ""),
        ("GET", f"{_b}/{{item_id}}"): ("team/resource", "safety.get_record", T_SAFE, "IMPLEMENTED_TESTED", ""),
        ("PATCH", f"{_b}/{{item_id}}"): ("team/resource", "safety.patch", T_SAFE, "IMPLEMENTED_TESTED", "Earlier answers kept in history"),
        ("DELETE", f"{_b}/{{item_id}}"): ("team/resource", "safety.delete", T_SAFE, "IMPLEMENTED_TESTED", "Own draft, or the delete right (baseline used edit)"),
        ("POST", f"{_b}/{{item_id}}/archive"): ("team/resource", "safety.archive", T_SAFE, "IMPLEMENTED_TESTED", ""),
        ("POST", f"{_b}/{{item_id}}/unarchive"): ("team/resource", "safety.unarchive", T_SAFE, "IMPLEMENTED_TESTED", ""),
        ("GET", f"{_b}/category-counts"): ("team/resource", "safety.category_counts", T_SAFE, "IMPLEMENTED_TESTED", "Same scope as the list"),
    })
OPTIONAL.update({
    ("POST", "/api/swms/{item_id}/review"): ("team/resource", "safety.review", T_SAFE, "IMPLEMENTED_TESTED", "D-029 needs approve (admin, HSEQ lead); same route on every review register"),
    ("GET", "/api/swms/{swms_id}/history"): ("team/resource", "safety.versions", T_SAFE, "IMPLEMENTED_TESTED", "Served as GET /api/swms/{id}/versions; /history is the event log"),
    ("PUT", "/api/swms/assignments/{swms_id}"): ("team/resource", "safety.put_assignments", T_SAFE, "HISTORICAL_COMPATIBILITY", "Replaced by PUT /api/swms/{id}/assignments"),
    ("GET", "/api/swms/assignments"): ("team/resource", "safety.get_assignments", T_SAFE, "HISTORICAL_COMPATIBILITY", "Replaced by GET /api/swms/{id}/assignments"),
})

NEW_ROWS = [
    ["GET", "/api/{register}/schema", "(new)", "schema", "authenticated-own", "safety.schema", T_SAFE, "IMPLEMENTED_TESTED", "Built-in form, statuses and scope for each of the 8 registers"],
    ["POST", "/api/{register}/{id}/submit", "(new)", "submit", "team/resource", "safety.submit", T_SAFE, "IMPLEMENTED_TESTED", "Review registers"],
    ["POST", "/api/{register}/{id}/review", "(new)", "review", "team/resource", "safety.review", T_SAFE, "IMPLEMENTED_TESTED", "Review registers other than SWMS"],
    ["POST", "/api/{register}/{id}/status", "(new)", "set_status", "team/resource", "safety.set_status", T_SAFE, "IMPLEMENTED_TESTED", "Hazards and incidents: open, in progress, closed with note"],
    ["GET", "/api/{register}/{id}/history", "(new)", "history", "team/resource", "safety.history", T_SAFE, "IMPLEMENTED_TESTED", "Event log"],
    ["GET", "/api/{register}/{id}/pdf", "(new)", "pdf", "team/resource", "safety.pdf", T_SAFE, "IMPLEMENTED_TESTED", "Branded PDF from the saved record"],
    ["POST", "/api/{register}/{id}/files", "(new)", "upload_file", "team/resource", "safety.upload_file", T_SAFE, "IMPLEMENTED_TESTED", ""],
    ["GET", "/api/{register}/{id}/files/{file_id}", "(new)", "download_file", "team/resource", "safety.download_file", T_SAFE, "IMPLEMENTED_TESTED", "Only through a record the caller may see"],
    ["DELETE", "/api/{register}/{id}/files/{file_id}", "(new)", "remove_file", "team/resource", "safety.remove_file", T_SAFE, "IMPLEMENTED_TESTED", ""],
    ["GET", "/api/ssra", "(new)", "ssra register", "team/resource", "safety (ssra)", T_SAFE, "IMPLEMENTED_TESTED", "SSRA has its own register with the full route set"],
    ["POST", "/api/swms/{id}/new-version", "(new)", "new_version", "team/resource", "safety.new_version", T_SAFE, "IMPLEMENTED_TESTED", "D-031"],
    ["GET", "/api/swms/{id}/versions", "(new)", "versions", "team/resource", "safety.versions", T_SAFE, "IMPLEMENTED_TESTED", ""],
    ["GET", "/api/swms/{id}/assignments", "(new)", "get_assignments", "team/resource", "safety.get_assignments", T_SAFE, "IMPLEMENTED_TESTED", ""],
    ["PUT", "/api/swms/{id}/assignments", "(new)", "put_assignments", "team/resource", "safety.put_assignments", T_SAFE, "IMPLEMENTED_TESTED", "Roles, people, companies"],
    ["POST", "/api/swms/{id}/acknowledge", "(new)", "acknowledge", "authenticated-own", "safety.acknowledge", T_SAFE, "IMPLEMENTED_TESTED", "D-031 per version"],
    ["GET", "/api/swms/{id}/acknowledgements", "(new)", "acknowledgements", "team/resource", "safety.acknowledgements", T_SAFE, "IMPLEMENTED_TESTED", ""],
    ["GET", "/api/forms/templates/{template_id}/assignments", "(new)", "get_assignments", "team/resource", "forms.get_assignments", T_FORM, "IMPLEMENTED_TESTED", "D-027"],
    ["PUT", "/api/forms/templates/{template_id}/assignments", "(new)", "put_assignments", "team/resource", "forms.put_assignments", T_FORM, "IMPLEMENTED_TESTED", "D-027 roles, people, companies; targets checked against the organisation"],
    ["GET", "/api/forms/assignments/effective", "(new)", "effective_assignments", "team/resource", "forms.effective_assignments", T_FORM, "IMPLEMENTED_TESTED", "Explains why a person sees each form"],
    ["GET", "/api/forms/field-types", "(new)", "field_types", "authenticated-own", "forms.field_types", T_FORM, "IMPLEMENTED_TESTED", "One field-family contract for office and phone"],
    ["GET", "/api/forms/templates/export", "(new)", "export_templates", "team/resource", "forms.export_templates", T_FORM, "IMPLEMENTED_TESTED", "Definitions only, never answers"],
    ["POST", "/api/forms/templates/{template_id}/publish", "(new)", "publish_template", "team/resource", "forms.publish_template", T_FORM, "IMPLEMENTED_TESTED", "D-024"],
    ["POST", "/api/forms/templates/{template_id}/archive", "(new)", "archive_template", "team/resource", "forms.archive_template", T_FORM, "IMPLEMENTED_TESTED", ""],
    ["PATCH", "/api/forms/submissions/{submission_id}", "(new)", "update_draft", "authenticated-own", "forms.update_draft", T_FORM, "IMPLEMENTED_TESTED", "Draft save with expected_revision"],
    ["POST", "/api/forms/submissions/{submission_id}/submit", "(new)", "submit_draft", "authenticated-own", "forms.submit_draft", T_FORM, "IMPLEMENTED_TESTED", "D-025"],
    ["POST", "/api/forms/submissions/{submission_id}/files", "(new)", "upload_submission_file", "authenticated-own", "forms.upload_submission_file", T_FORM, "IMPLEMENTED_TESTED", ""],
    ["GET", "/api/forms/submissions/{submission_id}/files/{file_id}", "(new)", "download_submission_file", "team/resource", "forms.download_submission_file", T_FORM, "IMPLEMENTED_TESTED", ""],
    ["DELETE", "/api/forms/submissions/{submission_id}/files/{file_id}", "(new)", "remove_submission_file", "authenticated-own", "forms.remove_submission_file", T_FORM, "IMPLEMENTED_TESTED", "Draft only"],
    ["GET", "/api/document-library/folders/{folder_id}", "(new)", "get_folder", "team/resource", "documents.get_folder", T_DOC, "IMPLEMENTED_TESTED", "Folder with breadcrumb path"],
    ["GET", "/api/document-library/files/{file_id}", "(new)", "get_file", "team/resource", "documents.get_file", T_DOC, "IMPLEMENTED_TESTED", "File with revision history"],
    ["GET", "/api/setup/status", "(new)", "setup_status", "public-scoped", "org.setup_status", T_ORG, "IMPLEMENTED_TESTED", "D-016"],
    ["POST", "/api/setup", "(new)", "run_setup", "public-scoped", "org.run_setup", T_ORG, "IMPLEMENTED_TESTED", "D-016 first-run, key protected, once"],
    ["POST", "/api/org/account-holder", "(new)", "transfer_account_holder", "admin-owner", "org.transfer_account_holder", T_ORG, "IMPLEMENTED_TESTED", "D-017"],
    ["DELETE", "/api/org/logo", "(new)", "remove_logo", "admin-owner", "org.remove_logo", T_ORG, "IMPLEMENTED_TESTED", ""],
    ["GET", "/api/public/branding", "(new)", "public_branding", "public-scoped", "org.public_branding", T_ORG, "IMPLEMENTED_TESTED", "D-019"],
    ["GET", "/api/health", "(new)", "health", "public-scoped", "org.health", T_AUTH, "IMPLEMENTED_TESTED", "New readiness endpoint"],
    ["GET", "/api/permissions/schema", "(new)", "permissions_schema", "authenticated-own", "org.permissions_schema", T_RST, "IMPLEMENTED_TESTED", "New: catalogue for the matrix UI"],
]

FEATURES = {
    "CORE-01": ("PARTIAL", "tests/test_roles_sessions_tenancy.py (tenant isolation)", "Organisation/user/company/role scope and file ownership built. Worker and site scope arrive with those modules."),
    "AUTH-01": ("PARTIAL", "tests/test_auth.py", "Built: login, lockout, invitation, reset, password change, forced change, sessions, expiry, idle timeout, session admin. Outstanding: mobile PIN, admin-console PIN, Simpro sign-in, emailing links (held in outbox)."),
    "PERM-01": ("PARTIAL", "tests/test_permissions_unit.py; tests/test_users.py; tests/test_roles_sessions_tenancy.py", "Built: custom roles, action matrix, per-user overrides, presets, audit, grant guards. Outstanding: role form allowlist, payroll named grants, phone module allocation."),
    "ORG-01": ("PARTIAL", "tests/test_org_setup_files.py; e2e/walkthrough.py", "Built: name, time zone, contact details, validated portal and staff URLs with no host default, logo upload/replace/remove shown in shell and sign-in, companies, account holder. Outstanding: logo and branding in PDFs and email templates (arrive with those features), insurance certificates."),
    "DOC-01": ("PARTIAL", "tests/test_documents.py; e2e/walkthrough.py", "Built: folders (nest, move, rename, delete when empty), upload with type allowlist and content check, hash/size/type, private authorised download, inline preview for PDF and images, rename, move, expiry, tags, archive/restore, delete, revision history, missing-content state with repair or record-as-lost, counts, name/tag search. Outstanding: extracted-text search and AI retry, categories, generated per-worker and per-supplier folders, sharing (historical/optional), reorganise tool."),
    "FORM-01": ("PARTIAL", "tests/test_forms_engine.py; tests/test_forms_api.py; e2e/walkthrough.py", "Built: all 20 field families with a documented value shape and server validation, template editor with preview, immutable published versions, staged draft/submit, photos/attachments/signatures stored separately and linked, idempotent retry, read-only record view from the schema snapshot, definition-only import/export. branded PDF from the saved version with embedded photos and signatures and honest missing-file notes. Outstanding: authorised corrections after submit, scoped PDF share tokens, site/job/asset pickers and Navixy vehicle list (need later stages), AI draft."),
    "FORM-02": ("PARTIAL", "tests/test_forms_api.py; e2e/walkthrough.py", "Built: assignment to roles, named people and companies; open-to-everyone default; enforced on list, open, access-check and submit; effective preview with reasons. Outstanding: positions and asset types (need workers and fleet), the double-switch certification gate (needs certifications; per-template switch is stored and defaults off)."),
    "SWMS-01": ("PARTIAL", "tests/test_safety.py; e2e/walkthrough.py", "Built: capture on the SWMS form (job steps table, PPE, emergency, legislation, source file), draft/submit/approve/changes/reject, assignments to roles/people/companies, per-version acknowledgements, new versions with supersede, version chain, PDF, archive. Outstanding: AI draft, paste/scan/docx import, asset-type assignment, attendance sheet, recycle bin and version diff."),
    "PRE-01": ("PARTIAL", "tests/test_safety.py; e2e/walkthrough.py", "Built: pre-start register with crew sign-on, hazards, vehicle (manual until Navixy), meter, GPS, signature, review cycle, PDF, projection of pre-start form templates. Outstanding: Navixy vehicle list and worker ids (stages 4-6), bulk URL/PDF/ZIP import (IMP-01)."),
    "DIARY-01": ("PARTIAL", "tests/test_safety.py", "Built: daily diary with raw notes, crew, activities, issues, photos, review, PDF. Outstanding: AI structuring of raw notes (AI stage)."),
    "INC-01": ("PARTIAL", "tests/test_safety.py; e2e/walkthrough.py", "Built: incidents and hazards registers shown as one surface with a type switch, categories, severity, people involved, evidence photos, follow-up actions, open/in progress/closed with resolution note, history that keeps original answers, category counts, bulk archive, PDF. Outstanding: root-cause catalogue, customer-service issues, bulk PDF export."),
    "INSP-01": ("PARTIAL", "tests/test_safety.py", "Built: inspection register with pass/fail/NA checklist where a fail needs a note, failures surfaced on list, detail and PDF, corrective actions, signature, GPS, review. Custom checklists come from form templates of the inspection category."),
    "RISK-01": ("PARTIAL", "tests/test_safety.py", "Built: risk assessment and SSRA as separate registers; scores computed only from valid 1-5 answers and always shown with the rule. Outstanding: editable master risk catalogue (REF-01)."),
    "NAV-01": ("PARTIAL", "Browser walkthrough (login, users, roles, sessions, profile)", "Shell with permission-filtered menu for built screens only. Preferences, app directory and version display outstanding."),
}

T_WORK = "tests/test_workers.py"
DONE.update({
    ("GET", "/api/workers"): ("team/resource", "workers.list_workers", T_WORK, "IMPLEMENTED_TESTED", "Own record only without workers.team_view"),
    ("POST", "/api/workers"): ("team/resource", "workers.create_worker", T_WORK, "IMPLEMENTED_TESTED", "D-035 manual creation allowed until the Simpro sync exists"),
    ("GET", "/api/workers/directory"): ("authenticated-own", "workers.directory", T_WORK, "IMPLEMENTED_TESTED", "Names only"),
    ("GET", "/api/workers/{worker_id}"): ("team/resource", "workers.get_worker", T_WORK, "IMPLEMENTED_TESTED", "D-036 privacy tiers"),
    ("PATCH", "/api/workers/{worker_id}"): ("team/resource", "workers.update_worker", T_WORK, "IMPLEMENTED_TESTED", "Protected fields need hr_employees.edit"),
    ("DELETE", "/api/workers/{worker_id}"): ("team/resource", "workers.delete_worker", T_WORK, "IMPLEMENTED_TESTED", "Soft delete; login unlinked, not removed"),
    ("POST", "/api/workers/{worker_id}/restore"): ("team/resource", "workers.restore_worker", T_WORK, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/workers/{worker_id}/link-user"): ("team/resource", "workers.link_user", T_WORK, "IMPLEMENTED_TESTED", "One login per worker (unique index)"),
    ("POST", "/api/workers/{worker_id}/provision-user"): ("team/resource", "workers.provision_user", T_WORK, "IMPLEMENTED_TESTED", "Invitation held, link shown once (D-012)"),
    ("POST", "/api/workers/{worker_id}/photo"): ("team/resource", "workers.upload_photo", T_WORK, "IMPLEMENTED_TESTED", "Content sniffed; office or the worker themself"),
    ("DELETE", "/api/workers/{worker_id}/photo"): ("team/resource", "workers.delete_photo", T_WORK, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/workers/{worker_id}/photo/{gridfs_id}"): ("team/resource", "workers.get_photo", T_WORK, "IMPLEMENTED_TESTED", "Path is /photo (no storage id in the URL)"),
    ("GET", "/api/workers/certifications/all"): ("team/resource", "workers.all_certifications", T_WORK, "IMPLEMENTED_TESTED", "Summary counts use the same filters as the rows"),
    ("GET", "/api/workers/certifications/search"): ("team/resource", "workers.all_certifications", T_WORK, "IMPLEMENTED_TESTED", "Folded into /certifications/all?q="),
    ("PATCH", "/api/workers/certifications/{cert_id}"): ("team/resource", "workers.update_certification", T_WORK, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/workers/certifications/{cert_id}"): ("team/resource", "workers.delete_certification", T_WORK, "IMPLEMENTED_TESTED", "Earlier certificate becomes current again"),
    ("POST", "/api/workers/certifications/{cert_id}/archive"): ("team/resource", "workers.archive_certification", T_WORK, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/workers/certifications/{cert_id}/restore"): ("team/resource", "workers.restore_certification", T_WORK, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/workers/{worker_id}/certifications"): ("team/resource", "workers.list_certifications", T_WORK, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/workers/{worker_id}/certifications"): ("team/resource", "workers.add_certification", T_WORK, "IMPLEMENTED_TESTED", "Kind normalised to a known slug"),
    ("POST", "/api/workers/{worker_id}/certifications/{cert_id}/upload"): ("team/resource", "workers.upload_evidence", T_WORK, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/workers/{worker_id}/certifications/upload"): ("team/resource", "workers.add_certification + upload_evidence", T_WORK, "IMPLEMENTED_TESTED", "Two steps: create, then attach the file"),
    ("GET", "/api/workers/{worker_id}/certifications/{cert_id}/file"): ("team/resource", "workers.evidence_file", T_WORK, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/certifications/expiry-count"): ("team/resource", "workers.expiry_count", T_WORK, "IMPLEMENTED_TESTED", "Same population as the register default"),
})

NEW_ROWS += [
    ["GET", "/api/workers/me", "workers", "", "authenticated-own", "workers.my_record", T_WORK, "IMPLEMENTED_TESTED", "New: own record and certificates"],
    ["PATCH", "/api/workers/me", "workers", "", "authenticated-own", "workers.edit_my_record", T_WORK, "IMPLEMENTED_TESTED", "New: self-service fields only"],
    ["POST", "/api/workers/{worker_id}/archive", "workers", "", "team/resource", "workers.archive_worker", T_WORK, "IMPLEMENTED_TESTED", "New: archive distinct from inactive"],
    ["POST", "/api/workers/{worker_id}/unarchive", "workers", "", "team/resource", "workers.unarchive_worker", T_WORK, "IMPLEMENTED_TESTED", "New"],
    ["POST", "/api/workers/{worker_id}/reveal", "workers", "", "team/resource", "workers.reveal_protected", T_WORK, "IMPLEMENTED_TESTED", "New: audited reveal of encrypted fields (D-036)"],
    ["GET", "/api/workers/{worker_id}/history", "workers", "", "team/resource", "workers.worker_history", T_WORK, "IMPLEMENTED_TESTED", "New: change history"],
    ["DELETE", "/api/workers/{worker_id}/link-user", "workers", "", "team/resource", "workers.unlink_user", T_WORK, "IMPLEMENTED_TESTED", "New"],
    ["POST", "/api/workers/certifications/{cert_id}/renew", "workers", "", "team/resource", "workers.renew_certification", T_WORK, "IMPLEMENTED_TESTED", "New: renewal keeps the earlier certificate (D-037)"],
    ["GET", "/api/certifications/settings", "certifications", "", "team/resource", "workers.get_cert_settings", T_WORK, "IMPLEMENTED_TESTED", "New"],
    ["PUT", "/api/certifications/settings", "certifications", "", "admin-owner", "workers.put_cert_settings", T_WORK, "IMPLEMENTED_TESTED", "New: warning window, expected kinds, organisation gate switch"],
    ["GET", "/api/certification-kinds", "certifications", "", "team/resource", "workers.list_kinds", T_WORK, "IMPLEMENTED_TESTED", "New: per-organisation vocabulary seeded from the reference list"],
    ["POST", "/api/certification-kinds", "certifications", "", "team/resource", "workers.create_kind", T_WORK, "IMPLEMENTED_TESTED", "New"],
    ["PATCH", "/api/certification-kinds/{kind_id}", "certifications", "", "team/resource", "workers.update_kind", T_WORK, "IMPLEMENTED_TESTED", "New: slug never changes"],
    ["GET", "/api/forms/pickers/people", "forms", "", "team/resource", "forms.pick_people", T_WORK, "IMPLEMENTED_TESTED", "New: logins for form assignment"],
]

FEATURES.update({
    "WORK-01": ("PARTIAL", "tests/test_workers.py; e2e workers step", "Built: roster (active/inactive/archived), details, photo, login link and provisioning, self-service edits, change history, duplicate warning. Outstanding: Simpro import (stage 5), ID card/QR, mobile PIN, worker documents."),
    "WORK-02": ("PARTIAL", "tests/test_workers.py", "Built: birth date, TFN and USI encrypted at rest, masked in listings, permissioned audited reveal, clear failure when the key is missing or wrong. Outstanding: HR documents and the HR import/link tools."),
    "CERT-01": ("PARTIAL", "tests/test_workers.py; e2e workers step", "Built: kinds with alias normalisation, issue/expiry, evidence files, valid/expiring/expired/unknown/missing, renewal that keeps earlier evidence, register whose totals match its filters, own view. Outstanding: reminders (stage 5), bulk actions, renewal links (4c)."),
    "FORM-02": ("PARTIAL", "tests/test_forms_api.py; tests/test_workers.py", "Built: role/user/company assignment with effective preview; certification double-switch gate enforced on start and on submit, including expired, unknown-expiry and missing certificates and unlinked logins. Outstanding: positions and asset-type assignment."),
})

T_SITE = "tests/test_sites.py"
DONE.update({
    ("GET", "/api/sites"): ("team/resource", "sites.list_sites", T_SITE, "IMPLEMENTED_TESTED", "Paginated; live on-site counts"),
    ("POST", "/api/sites"): ("team/resource", "sites.create_site", T_SITE, "IMPLEMENTED_TESTED", "sites.edit (not admin-only)"),
    ("GET", "/api/sites/admin"): ("team/resource", "sites.list_sites", T_SITE, "IMPLEMENTED_TESTED", "One sites register; the separate admin list is folded in"),
    ("POST", "/api/sites/admin"): ("team/resource", "sites.create_site", T_SITE, "IMPLEMENTED_TESTED", "Folded into POST /api/sites"),
    ("PATCH", "/api/sites/admin/{sid}"): ("team/resource", "sites.update_site", T_SITE, "IMPLEMENTED_TESTED", "Folded into PATCH /api/sites/{site_id}"),
    ("DELETE", "/api/sites/admin/{sid}"): ("team/resource", "sites.delete_site", T_SITE, "IMPLEMENTED_TESTED", "Folded into DELETE /api/sites/{site_id}"),
    ("PATCH", "/api/sites/{site_id}"): ("team/resource", "sites.update_site", T_SITE, "IMPLEMENTED_TESTED", "Includes sign-on questions and visitor declaration"),
    ("GET", "/api/sites/recycle-bin"): ("team/resource", "sites.recycle_bin", T_SITE, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/sites/{site_id}/restore"): ("team/resource", "sites.restore_site", T_SITE, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/sites/{site_id}/active-signons"): ("team/resource", "sites.active_signons", T_SITE, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/sites/{site_id}/active-signons/{signon_id}"): ("team/resource", "sites.office_sign_off", T_SITE, "IMPLEMENTED_TESTED", "D-044 closes the visit; history is kept, not deleted"),
    ("GET", "/api/sites/{site_id}/qr-signage.pdf"): ("team/resource", "sites.qr_signage", T_SITE, "IMPLEMENTED_TESTED", "Needs sites.edit: the code is the key to the site"),
    ("GET", "/api/sites/{site_id}/scan-pdf"): ("team/resource", "sites.qr_signage", T_SITE, "IMPLEMENTED_TESTED", "Same printable sign"),
    ("GET", "/api/sites/{site_id}/signon-log"): ("team/resource", "sites.signon_log", T_SITE, "IMPLEMENTED_TESTED", "sites.view; 500-row cap with truncated flag"),
    ("POST", "/api/sites/{site_id}/signon-log/export"): ("team/resource", "sites.export_log", T_SITE, "IMPLEMENTED_TESTED", "CSV; audited; formula cells neutralised"),
    ("POST", "/api/sites/{site_id}/signoff"): ("authenticated-own", "sites.sign_off", T_SITE, "IMPLEMENTED_TESTED", "Idempotent"),
    ("POST", "/api/sites/{site_id}/signon-v127"): ("authenticated-own", "sites.worker_sign_on", T_SITE, "IMPLEMENTED_TESTED", "Signed-in sign-on is by QR token: POST /api/scan/site/{scan_token}/sign-on"),
    ("POST", "/api/mobile/sites/{site_id}/sign-on"): ("authenticated-own", "sites.worker_sign_on", T_SITE, "IMPLEMENTED_TESTED", "Same handler as the QR sign-on"),
    ("GET", "/api/scan/site/{scan_token}"): ("public-scoped", "sites.scan_site", T_SITE, "IMPLEMENTED_TESTED", "Name, address, organisation name, questions, declaration only"),
    ("POST", "/api/scan/site/{scan_token}/sign-on"): ("authenticated-own", "sites.worker_sign_on", T_SITE, "IMPLEMENTED_TESTED", "Unique open visit per person per site"),
    ("POST", "/api/scan/site/{scan_token}/sign-on-visitor"): ("public-scoped", "sites.visitor_sign_in", T_SITE, "IMPLEMENTED_TESTED", "One visitor sign-in route (D-042)"),
    ("GET", "/api/public/site/{scan_token}/form"): ("public-scoped", "sites.visitor_form", T_SITE, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/public/visitor/site/{scan_token}/signin"): ("public-scoped", "sites.visitor_sign_in", T_SITE, "IMPLEMENTED_TESTED", "Declaration required; 10 an hour per address per site; idempotent"),
    ("POST", "/api/public/visitor/{visitor_id}/sign-out"): ("public-scoped", "sites.visitor_sign_out", T_SITE, "IMPLEMENTED_TESTED", "Needs the site's QR token; first sign-out time stands"),
    ("GET", "/api/sign-on/{site_id}"): ("public-scoped", "(not carried over)", T_SITE, "HISTORICAL_COMPATIBILITY", "D-043 the QR carries the revocable token, not the permanent site id"),
    ("GET", "/api/admin/visitors"): ("team/resource", "sites.list_visitors", T_SITE, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/admin/visitors/category-counts"): ("team/resource", "sites.visitor_counts", T_SITE, "IMPLEMENTED_TESTED", "On site, today (local day), total"),
    ("GET", "/api/admin/visitors/{visitor_id}"): ("team/resource", "sites.get_visitor", T_SITE, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/admin/visitors/{visitor_id}"): ("team/resource", "sites.delete_visitor", T_SITE, "IMPLEMENTED_TESTED", "Soft delete; idempotent"),
    ("POST", "/api/admin/visitors/bulk-delete"): ("team/resource", "sites.bulk_delete_visitors", T_SITE, "IMPLEMENTED_TESTED", "Skipped rows reported with a reason"),
    ("POST", "/api/admin/visitors/{visitor_id}/archive"): ("team/resource", "sites.archive_visitor", T_SITE, "IMPLEMENTED_TESTED", "Refused while still on site"),
    ("POST", "/api/admin/visitors/{visitor_id}/unarchive"): ("team/resource", "sites.unarchive_visitor", T_SITE, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/admin/visitors/{visitor_id}/force-signout"): ("team/resource", "sites.force_sign_out", T_SITE, "IMPLEMENTED_TESTED", "Record only; nobody is notified"),
})
NEW_ROWS += [
    ["GET", "/api/sites/{site_id}", "sites", "", "team/resource", "sites.get_site", T_SITE, "IMPLEMENTED_TESTED", "New: site detail"],
    ["DELETE", "/api/sites/{site_id}", "sites", "", "team/resource", "sites.delete_site", T_SITE, "IMPLEMENTED_TESTED", "New: soft delete that signs out anyone still on site"],
    ["POST", "/api/sites/{site_id}/rotate-token", "sites", "", "team/resource", "sites.rotate_token", T_SITE, "IMPLEMENTED_TESTED", "New: retire printed codes (D-043)"],
    ["GET", "/api/sites/{site_id}/qr.png", "sites", "", "team/resource", "sites.qr_png", T_SITE, "IMPLEMENTED_TESTED", "New"],
    ["GET", "/api/sites/my-attendance", "sites", "", "authenticated-own", "sites.my_attendance", T_SITE, "IMPLEMENTED_TESTED", "New: where I am signed on"],
    ["GET", "/api/scan/site/{scan_token}/me", "sites", "", "authenticated-own", "sites.scan_site_me", T_SITE, "IMPLEMENTED_TESTED", "New: my state at this site"],
    ["POST", "/api/scan/site/{scan_token}/sign-off", "sites", "", "authenticated-own", "sites.worker_sign_off", T_SITE, "IMPLEMENTED_TESTED", "New"],
    ["GET", "/api/public/visitor/{visitor_id}", "sites", "", "public-scoped", "sites.visitor_status", T_SITE, "IMPLEMENTED_TESTED", "New: lets the visitor's page survive a refresh"],
    ["PATCH", "/api/admin/visitors/{visitor_id}", "sites", "", "team/resource", "sites.correct_visitor", T_SITE, "IMPLEMENTED_TESTED", "New: reasoned correction with audit"],
    ["GET", "/api/admin/visitors/export.csv", "sites", "", "team/resource", "sites.export_visitors", T_SITE, "IMPLEMENTED_TESTED", "New"],
]
FEATURES.update({
    "SITE-01": ("PARTIAL", "tests/test_sites.py; e2e sites and visitors step", "Built: sites register, QR code and printable sign, sign-on questions, worker and visitor sign-in/out (idempotent), live occupancy, history, office sign-out, visitor register with corrections, archive, delete and CSV export; QR token reaches one site only. Outstanding: Simpro site import (stage 5), bulk archive by criteria, GPS distance checks, migration mapping for legacy sign-in collections (no legacy data in a rebuild)."),
})

T_SUP = "tests/test_suppliers_renewals.py"
DONE.update({
    ("GET", "/api/contractors"): ("team/resource", "suppliers.list_contractors", T_SUP, "IMPLEMENTED_TESTED", "D-047 one directory; contractors are flagged suppliers"),
    ("POST", "/api/contractors"): ("team/resource", "suppliers.create_supplier", T_SUP, "IMPLEMENTED_TESTED", "POST /api/suppliers with is_contractor"),
    ("GET", "/api/contractors/{cid}"): ("team/resource", "suppliers.get_contractor", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("PATCH", "/api/contractors/{cid}"): ("team/resource", "suppliers.update_supplier", T_SUP, "IMPLEMENTED_TESTED", "PATCH /api/suppliers/{id}"),
    ("DELETE", "/api/contractors/{cid}"): ("team/resource", "suppliers.delete", T_SUP, "IMPLEMENTED_TESTED", "DELETE /api/suppliers/{id}; soft"),
    ("POST", "/api/contractors/{cid}/documents"): ("team/resource", "suppliers.upload_document", T_SUP, "IMPLEMENTED_TESTED", "POST /api/suppliers/{id}/documents"),
    ("DELETE", "/api/contractors/{cid}/documents/{doc_id}"): ("team/resource", "suppliers.delete_document", T_SUP, "IMPLEMENTED_TESTED", "Earlier document becomes current again"),
    ("GET", "/api/suppliers/meta"): ("team/resource", "suppliers.list_suppliers", T_SUP, "IMPLEMENTED_TESTED", "Local details live on the supplier row; one list call"),
    ("GET", "/api/suppliers/{simpro_supplier_id}/meta"): ("team/resource", "suppliers.get_one", T_SUP, "IMPLEMENTED_TESTED", "Keyed by the portal's own id"),
    ("PATCH", "/api/suppliers/{simpro_supplier_id}/meta"): ("team/resource", "suppliers.update_supplier", T_SUP, "IMPLEMENTED_TESTED", "Hand edits to imported rows are remembered (locally_edited)"),
    ("GET", "/api/suppliers/panel-counts"): ("team/resource", "suppliers.panel_counts", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/suppliers/{supplier_id}/members"): ("team/resource", "suppliers.list_members", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/suppliers/{supplier_id}/members"): ("team/resource", "suppliers.add_member", T_SUP, "IMPLEMENTED_TESTED", "One main contact"),
    ("PATCH", "/api/suppliers/members/{member_id}"): ("team/resource", "suppliers.edit_member", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/suppliers/members/{member_id}"): ("team/resource", "suppliers.delete_member", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/suppliers/{supplier_id}/notes"): ("team/resource", "suppliers.list_notes", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/suppliers/{supplier_id}/notes"): ("team/resource", "suppliers.add_note", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("PATCH", "/api/suppliers/notes/{note_id}"): ("team/resource", "suppliers.edit_note", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/suppliers/notes/{note_id}"): ("team/resource", "suppliers.delete_note", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/suppliers/{supplier_id}/tasks"): ("team/resource", "suppliers.list_tasks", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/suppliers/{supplier_id}/tasks"): ("team/resource", "suppliers.add_task", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("PATCH", "/api/suppliers/tasks/{task_id}"): ("team/resource", "suppliers.edit_task", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/suppliers/tasks/{task_id}"): ("team/resource", "suppliers.delete_task", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/suppliers/{simpro_supplier_id}/send-renewal"): ("team/resource", "renewals.create_renewal", T_SUP, "IMPLEMENTED_TESTED", "A renewal request is a scoped link; the email is held in the outbox"),
    ("GET", "/api/renewals"): ("team/resource", "renewals.list_renewals", T_SUP, "IMPLEMENTED_TESTED", "Counts by status, including expired"),
    ("POST", "/api/renewals"): ("team/resource", "renewals.create_renewal", T_SUP, "IMPLEMENTED_TESTED", "Supplier or worker; link shown once; token stored as a hash"),
    ("POST", "/api/renewals/bulk"): ("team/resource", "renewals.bulk_create", T_SUP, "IMPLEMENTED_TESTED", "Skips suppliers with an open request covering the same documents"),
    ("POST", "/api/renewals/bulk-delete"): ("team/resource", "renewals.bulk_delete", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("PATCH", "/api/renewals/{rid}"): ("team/resource", "renewals.update_renewal", T_SUP, "IMPLEMENTED_TESTED", "Open requests only; link unchanged"),
    ("DELETE", "/api/renewals/{rid}"): ("team/resource", "renewals.delete_renewal", T_SUP, "IMPLEMENTED_TESTED", "Soft delete and revoke"),
    ("POST", "/api/renewals/{rid}/revoke"): ("team/resource", "renewals.revoke", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/renewals/doc-types"): ("team/resource", "suppliers.list_doc_types", T_SUP, "IMPLEMENTED_TESTED", "Seeded with a default set (D-049)"),
    ("POST", "/api/renewals/doc-types"): ("team/resource", "suppliers.create_doc_type", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("PATCH", "/api/renewals/doc-types/{type_id}"): ("team/resource", "suppliers.update_doc_type", T_SUP, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/renewals/doc-types/{type_id}"): ("team/resource", "suppliers.retire_doc_type", T_SUP, "IMPLEMENTED_TESTED", "Retires; held documents keep the type"),
    ("GET", "/api/public/renewals/{token}"): ("public-scoped", "renewals.public_view", T_SUP, "IMPLEMENTED_TESTED", "Only this request: no ids, no other records"),
    ("POST", "/api/public/renewals/{token}/submit"): ("public-scoped", "renewals.public_submit", T_SUP, "IMPLEMENTED_TESTED", "Idempotent; closes the link to further uploads"),
    ("GET", "/api/files/renewals/{token}/{name}"): ("public-scoped", "(not carried over)", T_SUP, "HISTORICAL_COMPATIBILITY", "The public page never serves files back; the office opens uploads with its own session"),
})
NEW_ROWS += [
    ["GET", "/api/suppliers", "suppliers", "", "team/resource", "suppliers.list_suppliers", T_SUP, "IMPLEMENTED_TESTED", "New: paginated directory with compliance summary"],
    ["POST", "/api/suppliers", "suppliers", "", "team/resource", "suppliers.create_supplier", T_SUP, "IMPLEMENTED_TESTED", "New: ABN checksum; duplicate warning"],
    ["GET", "/api/suppliers/{supplier_id}", "suppliers", "", "team/resource", "suppliers.get_one", T_SUP, "IMPLEMENTED_TESTED", "New"],
    ["PATCH", "/api/suppliers/{supplier_id}", "suppliers", "", "team/resource", "suppliers.update_supplier", T_SUP, "IMPLEMENTED_TESTED", "New"],
    ["DELETE", "/api/suppliers/{supplier_id}", "suppliers", "", "team/resource", "suppliers.delete", T_SUP, "IMPLEMENTED_TESTED", "New: soft delete"],
    ["POST", "/api/suppliers/{supplier_id}/archive", "suppliers", "", "team/resource", "suppliers.archive", T_SUP, "IMPLEMENTED_TESTED", "New"],
    ["POST", "/api/suppliers/{supplier_id}/unarchive", "suppliers", "", "team/resource", "suppliers.unarchive", T_SUP, "IMPLEMENTED_TESTED", "New"],
    ["POST", "/api/suppliers/{supplier_id}/restore", "suppliers", "", "team/resource", "suppliers.restore", T_SUP, "IMPLEMENTED_TESTED", "New"],
    ["GET", "/api/suppliers/{supplier_id}/documents", "suppliers", "", "team/resource", "suppliers.list_documents", T_SUP, "IMPLEMENTED_TESTED", "New: current and history"],
    ["GET", "/api/suppliers/{supplier_id}/documents/{doc_id}/file", "suppliers", "", "team/resource", "suppliers.document_file", T_SUP, "IMPLEMENTED_TESTED", "New"],
    ["POST", "/api/suppliers/{supplier_id}/link-user", "suppliers", "", "admin-owner", "suppliers.link_login", T_SUP, "IMPLEMENTED_TESTED", "New: external representative scope (D-048)"],
    ["DELETE", "/api/suppliers/{supplier_id}/link-user/{user_id}", "suppliers", "", "admin-owner", "suppliers.unlink_login", T_SUP, "IMPLEMENTED_TESTED", "New"],
    ["GET", "/api/renewals/{renewal_id}", "renewals", "", "team/resource", "renewals.get_renewal", T_SUP, "IMPLEMENTED_TESTED", "New: uploads and earlier rounds"],
    ["POST", "/api/renewals/{renewal_id}/review", "renewals", "", "team/resource", "renewals.review", T_SUP, "IMPLEMENTED_TESTED", "New: accept or reject each file; accepted files become current documents or certificate renewals"],
    ["POST", "/api/renewals/{renewal_id}/reissue", "renewals", "", "team/resource", "renewals.reissue", T_SUP, "IMPLEMENTED_TESTED", "New: fresh link, previous one dies"],
    ["GET", "/api/renewals/{renewal_id}/uploads/{upload_id}/file", "renewals", "", "team/resource", "renewals.upload_file", T_SUP, "IMPLEMENTED_TESTED", "New"],
    ["POST", "/api/public/renewals/{token}/upload", "renewals", "", "public-scoped", "renewals.public_upload", T_SUP, "IMPLEMENTED_TESTED", "New: one file per requested document; type and size checked"],
]
FEATURES.update({
    "SUP-01": ("PARTIAL", "tests/test_suppliers_renewals.py; e2e suppliers and renewals step", "Built: one supplier/contractor directory with search, status tabs, ABN check, local notes, tasks and members, compliance documents with expiry states and history, missing required documents, external representative scope. Outstanding: Simpro vendor import and address lookup (stage 5), supplier QR induction (4d), document folders."),
    "REN-01": ("IMPLEMENTED_TESTED", "tests/test_suppliers_renewals.py; e2e suppliers and renewals step", "Scoped expiring revocable links for suppliers and workers; validated uploads; office accept/reject per file with reasons; reissue rounds; history and audit kept; delivery state shown (held until email is connected in stage 5)."),
    "CERT-01": ("PARTIAL", "tests/test_workers.py; tests/test_suppliers_renewals.py; e2e workers step", "Built: kinds with alias normalisation, issue/expiry, evidence files, valid/expiring/expired/unknown/missing, renewal that keeps earlier evidence, register whose totals match its filters, own view, renewal links for workers. Outstanding: reminders (stage 5), bulk actions."),
})

T_IND = "tests/test_inductions_jobs.py"
DONE.update({
    ("GET", "/api/inductions/types"): ("team/resource", "inductions.list_types", T_IND, "IMPLEMENTED_TESTED", "Induction types are certificate kinds in the induction category (D-052)"),
    ("POST", "/api/inductions/types"): ("team/resource", "inductions.create_type", T_IND, "IMPLEMENTED_TESTED", "inductions.edit with team view, not admin-only"),
    ("PATCH", "/api/inductions/types/{tid}"): ("team/resource", "inductions.rename_type", T_IND, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/inductions/types/{tid}"): ("team/resource", "inductions.retire_type", T_IND, "IMPLEMENTED_TESTED", "Retires; recorded completions keep the type"),
    ("GET", "/api/workers/inductions/matrix"): ("team/resource", "inductions.matrix", T_IND, "IMPLEMENTED_TESTED", "Own row only without inductions.team_view"),
    ("PUT", "/api/workers/inductions/cell"): ("team/resource", "inductions.set_cell", T_IND, "IMPLEMENTED_TESTED", "Exactly one of date, does-not-expire, not-applicable, clear"),
    ("GET", "/api/workers/inductions/export.xlsx"): ("team/resource", "inductions.export_matrix", T_IND, "IMPLEMENTED_TESTED", "CSV at /export.csv (opens in Excel); the access sheet is not carried over"),
    ("POST", "/api/workers/{worker_id}/inductions"): ("team/resource", "inductions.set_cell", T_IND, "IMPLEMENTED_TESTED", "Folded into the cell route and the certification routes"),
    ("GET", "/api/workers/{worker_id}/inductions/{induction_id}"): ("team/resource", "workers.list_certifications", T_IND, "IMPLEMENTED_TESTED", "An induction is a certification row"),
    ("PATCH", "/api/workers/{worker_id}/inductions/{induction_id}"): ("team/resource", "workers.update_certification", T_IND, "IMPLEMENTED_TESTED", "PATCH /api/workers/certifications/{cert_id}"),
    ("DELETE", "/api/workers/{worker_id}/inductions/{induction_id}"): ("team/resource", "inductions.set_cell", T_IND, "IMPLEMENTED_TESTED", "clear=true"),
    ("POST", "/api/workers/{worker_id}/inductions/{induction_id}/file"): ("team/resource", "workers.upload_evidence", T_IND, "IMPLEMENTED_TESTED", "Certificate evidence upload"),
    ("GET", "/api/completed-training/"): ("team/resource", "inductions.list_training", T_IND, "IMPLEMENTED_TESTED", "Typed records, own-only without team access (D-053)"),
    ("POST", "/api/completed-training/"): ("team/resource", "inductions.add_training", T_IND, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/completed-training/{uid}"): ("team/resource", "inductions.get_training", T_IND, "IMPLEMENTED_TESTED", ""),
    ("PATCH", "/api/completed-training/{uid}"): ("team/resource", "inductions.edit_training", T_IND, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/completed-training/{uid}"): ("team/resource", "inductions.delete_training", T_IND, "IMPLEMENTED_TESTED", "Soft delete"),
    ("GET", "/api/workers/{worker_id}/hr-documents"): ("admin-owner", "inductions.list_hr", T_IND, "IMPLEMENTED_TESTED", "hr_employees.view"),
    ("POST", "/api/workers/{worker_id}/hr-documents"): ("admin-owner", "inductions.upload_hr", T_IND, "IMPLEMENTED_TESTED", "hr_employees.edit"),
    ("PATCH", "/api/workers/{worker_id}/hr-documents/{doc_id}"): ("admin-owner", "inductions.edit_hr", T_IND, "IMPLEMENTED_TESTED", "Title and notes only"),
    ("DELETE", "/api/workers/{worker_id}/hr-documents/{doc_id}"): ("admin-owner", "inductions.hr_delete", T_IND, "IMPLEMENTED_TESTED", "Soft delete, audited"),
    ("POST", "/api/workers/{worker_id}/hr-documents/{doc_id}/archive"): ("admin-owner", "inductions.hr_archive", T_IND, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/workers/{worker_id}/hr-documents/{doc_id}/restore"): ("admin-owner", "inductions.hr_restore", T_IND, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/workers/{worker_id}/hr-documents/{doc_id}/file"): ("admin-owner", "inductions.hr_file", T_IND, "IMPLEMENTED_TESTED", "hr_employees.reveal_pii; every open audited (D-054)"),
    ("GET", "/api/daily-jobs/today"): ("admin-owner", "jobs.list_jobs", T_IND, "IMPLEMENTED_TESTED", "GET /api/daily-jobs?date="),
    ("POST", "/api/daily-jobs/bulk-create"): ("admin-owner", "jobs.bulk_create", T_IND, "IMPLEMENTED_TESTED", "trial_run saves nothing; override cancels the earlier job"),
    ("POST", "/api/mobile/daily-jobs"): ("admin-owner", "jobs.create_job", T_IND, "IMPLEMENTED_TESTED", "POST /api/daily-jobs"),
    ("GET", "/api/mobile/daily-jobs/admin/assignments"): ("admin-owner", "jobs.list_jobs", T_IND, "IMPLEMENTED_TESTED", "Organisation timezone, not a fixed Sydney day"),
    ("GET", "/api/mobile/daily-jobs/today"): ("authenticated-own", "jobs.my_job_today", T_IND, "IMPLEMENTED_TESTED", "GET /api/daily-jobs/mine/today; no earlier-day fallback"),
    ("POST", "/api/mobile/daily-jobs/{assignment_id}/accept"): ("authenticated-own", "jobs.accept_job", T_IND, "IMPLEMENTED_TESTED", "Assignee only; idempotent"),
    ("POST", "/api/mobile/daily-jobs/{assignment_id}/decline"): ("authenticated-own", "jobs.decline_job", T_IND, "IMPLEMENTED_TESTED", "Reason required"),
    ("GET", "/api/daily-jobs/admin/sites"): ("admin-owner", "sites.list_sites", T_IND, "IMPLEMENTED_TESTED", "The issue screen reads the sites register"),
    ("GET", "/api/mobile/daily-jobs/admin/sites"): ("admin-owner", "sites.list_sites", T_IND, "IMPLEMENTED_TESTED", "Same"),
    ("GET", "/api/daily-jobs/admin/workers"): ("admin-owner", "workers.list_workers", T_IND, "IMPLEMENTED_TESTED", "The issue screen reads the worker roster"),
    ("GET", "/api/mobile/daily-jobs/admin/workers"): ("admin-owner", "workers.list_workers", T_IND, "IMPLEMENTED_TESTED", "Same"),
})
NEW_ROWS += [
    ["POST", "/api/inductions/types/{tid}/restore", "inductions", "", "team/resource", "inductions.restore_type", T_IND, "IMPLEMENTED_TESTED", "New"],
    ["POST", "/api/completed-training/{uid}/file", "training", "", "team/resource", "inductions.training_evidence", T_IND, "IMPLEMENTED_TESTED", "New: certificate for a course"],
    ["GET", "/api/completed-training/{uid}/file", "training", "", "team/resource", "inductions.training_file", T_IND, "IMPLEMENTED_TESTED", "New"],
    ["GET", "/api/daily-jobs/mine", "daily jobs", "", "authenticated-own", "jobs.my_jobs", T_IND, "IMPLEMENTED_TESTED", "New: upcoming and recent"],
    ["GET", "/api/daily-jobs/{job_id}", "daily jobs", "", "authenticated-own", "jobs.get_job", T_IND, "IMPLEMENTED_TESTED", "New: admin or the assignee"],
    ["PATCH", "/api/daily-jobs/{job_id}", "daily jobs", "", "admin-owner", "jobs.update_job", T_IND, "IMPLEMENTED_TESTED", "New: open jobs only"],
    ["POST", "/api/daily-jobs/{job_id}/cancel", "daily jobs", "", "admin-owner", "jobs.cancel_job", T_IND, "IMPLEMENTED_TESTED", "New"],
    ["POST", "/api/daily-jobs/{job_id}/complete", "daily jobs", "", "authenticated-own", "jobs.complete_job", T_IND, "IMPLEMENTED_TESTED", "New: assignee marks done"],
]
FEATURES.update({
    "IND-01": ("PARTIAL", "tests/test_inductions_jobs.py; e2e inductions and jobs step", "Built: induction types (add, rename, retire), worker-by-induction matrix with date, does-not-expire, not-applicable and clear, evidence, own row on the phone, CSV export, completed training with evidence, audit on every change. Outstanding: spreadsheet import wizard, printable matrix PDF, column clean-up tools (not needed without legacy data)."),
    "JOB-01": ("PARTIAL", "tests/test_inductions_jobs.py; e2e inductions and jobs step", "Built: issue to one or many workers with site, start time, truck, crew, instructions and linked forms; trial run; explicit replace; one active job per worker per day; accept, decline with reason, mark done; My work and Home card; job picker in forms; SMS prepared and held. Outstanding: PDF/SMS job import with confirmation, job site sign-on link, delivery once SMS is connected (stage 5)."),
    "WORK-02": ("PARTIAL", "tests/test_workers.py; tests/test_inductions_jobs.py", "Built: birth date, TFN and USI encrypted at rest, masked in listings, permissioned audited reveal; HR documents listed to HR viewers, opened only with the reveal permission, every open audited, archive and delete. Outstanding: HR spreadsheet import and link tools (the baseline's standalone HR roster is not recreated)."),
})

T_COM = "tests/test_comms.py"
DONE.update({
    ("GET", "/api/integrations"): ("team/resource", "comms.list_integrations", T_COM, "IMPLEMENTED_TESTED", "Catalogue with status, masked secret presence, key status"),
    ("GET", "/api/integrations/{kind}"): ("team/resource", "comms.get_integration", T_COM, "IMPLEMENTED_TESTED", "Secrets reported as set/not set only"),
    ("PUT", "/api/integrations/{kind}"): ("team/resource", "comms.save_integration", T_COM, "IMPLEMENTED_TESTED", "integrations.edit; omitted/blank/mask keeps a secret; clear is explicit (D-056)"),
    ("DELETE", "/api/integrations/microsoft365"): ("team/resource", "comms.disconnect", T_COM, "IMPLEMENTED_TESTED", "DELETE /api/integrations/{kind} for every provider"),
    ("POST", "/api/integrations/microsoft365/test-connection"): ("team/resource", "comms.test_connection", T_COM, "IMPLEMENTED_TESTED", "Token only; no self-test email is sent (D-057). Mock transport only"),
    ("POST", "/api/integrations/navixy/test-connection"): ("team/resource", "comms.test_connection", T_COM, "IMPLEMENTED_TESTED", "POST /v2/user/auth on the configured region. Mock transport only"),
    ("POST", "/api/integrations/navixy/get-hash"): ("team/resource", "comms.test_connection", T_COM, "IMPLEMENTED_TESTED", "The test obtains and stores the sign-in key encrypted"),
    ("POST", "/api/integrations/smartfill/test-connection"): ("team/resource", "comms.test_connection", T_COM, "IMPLEMENTED_TESTED", "Tank:Level with the parameters envelope. Mock transport only"),
    ("POST", "/api/integrations/textmagic/test-connection"): ("team/resource", "comms.test_connection", T_COM, "IMPLEMENTED_TESTED", "GET /user; no text is sent. Mock transport only"),
    ("POST", "/api/integrations/admin/migrate-integration-secrets"): ("admin-owner", "(not carried over)", T_COM, "HISTORICAL_COMPATIBILITY", "A rebuild never stored a secret in plain text, so there is nothing to migrate"),
    ("PATCH", "/api/admin/comms-safe-mode"): ("admin-owner", "comms.set_safe_mode", T_COM, "IMPLEMENTED_TESTED", "Explicit comms_safe_mode grant; server lock wins"),
    ("GET", "/api/admin/comms-safe-mode/status"): ("authenticated-own", "comms.safe_mode_status", T_COM, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/admin/comms-safe-mode/who-can-toggle"): ("team/resource", "comms.who_can_toggle", T_COM, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/admin/comms-outbox-blocked"): ("team/resource", "comms.list_outbox", T_COM, "IMPLEMENTED_TESTED", "GET /api/email/outbox?status=held; each row carries its hold reason"),
    ("DELETE", "/api/admin/comms-outbox-blocked"): ("team/resource", "comms.bulk_delete", T_COM, "IMPLEMENTED_TESTED", "POST /api/email/outbox/bulk-delete with status=held"),
    ("GET", "/api/email/outbox"): ("team/resource", "comms.list_outbox", T_COM, "IMPLEMENTED_TESTED", "All with notifications.view, otherwise own; email and SMS together"),
    ("GET", "/api/email/outbox/{email_id}"): ("team/resource", "comms.get_message", T_COM, "IMPLEMENTED_TESTED", "Attempt history"),
    ("POST", "/api/email/outbox/{email_id}/retry"): ("team/resource", "comms.retry_message", T_COM, "IMPLEMENTED_TESTED", "Never re-sends a sent message; no retry without stored text"),
    ("POST", "/api/email/outbox/{email_id}/cancel"): ("team/resource", "comms.cancel_message", T_COM, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/email/outbox/{email_id}"): ("team/resource", "comms.delete_message", T_COM, "IMPLEMENTED_TESTED", "Soft delete; own or with notifications.delete"),
    ("POST", "/api/email/outbox/bulk-delete"): ("team/resource", "comms.bulk_delete", T_COM, "IMPLEMENTED_TESTED", "Queued is never deleted by filter"),
    ("GET", "/api/notifications"): ("authenticated-own", "comms.list_notifications", T_COM, "IMPLEMENTED_TESTED", "Signals computed per entitlement"),
    ("POST", "/api/notifications/{notification_id}/read"): ("authenticated-own", "comms.mark_read", T_COM, "IMPLEMENTED_TESTED", "Idempotent; only an item the caller can see"),
    ("POST", "/api/notifications/mark-all-read"): ("authenticated-own", "comms.mark_all_read", T_COM, "IMPLEMENTED_TESTED", ""),
})
NEW_ROWS += [
    ["DELETE", "/api/integrations/{kind}", "integrations", "", "team/resource", "comms.disconnect", T_COM, "IMPLEMENTED_TESTED", "New: remove saved details for any provider"],
    ["POST", "/api/integrations/{kind}/test-connection", "integrations", "", "team/resource", "comms.test_connection", T_COM, "IMPLEMENTED_TESTED", "New: one test route for every provider, including Simpro and the simulator"],
]
FEATURES.update({
    "INT-01": ("PARTIAL", "tests/test_comms.py; e2e connections step", "Built: one catalogue for Simpro, Navixy, SmartFill, Microsoft 365 and TextMagic; encrypted secrets never returned, kept unless cleared, fail closed without the key; explicit non-destructive test per provider; labelled simulator; status kept separate from sync and delivery. NOT VERIFIED against any real provider (mock transport only). Outstanding: data imports, schedulers, Dropbox, key rotation."),
    "INT-05": ("PARTIAL", "tests/test_comms.py", "Built: Microsoft 365 app-only token and sendMail, TextMagic headers and message send, both only through the guarded boundary with safe mode, outbox evidence and no double send. NOT VERIFIED against the real services (mock transport only); no real message has ever been sent by this build. Outstanding: attachments, cost preview before an explicit send."),
    "COM-01": ("PARTIAL", "tests/test_comms.py; e2e connections step", "Built: single send boundary; safe mode on by default with a server lock and an explicit-grant switch; automatic callers never send; duplicate keys; daily text limit; outbox with history, retry (never twice), cancel, delete; one-time links sent in memory and never stored; notification bell with per-person read state. Outstanding: compose/send-report screens, attachments, scheduled reminders, real delivery (unverified)."),
})

T_IMP = "tests/test_imports.py"
DONE.update({
    ("GET", "/api/integrations/simpro/companies"): ("team/resource", "imports.companies", T_IMP, "IMPLEMENTED_TESTED", "From the last connection test"),
    ("POST", "/api/integrations/simpro/connect"): ("team/resource", "comms.save_integration", T_IMP, "IMPLEMENTED_TESTED", "PUT /api/integrations/simpro"),
    ("POST", "/api/integrations/simpro/test-connection"): ("team/resource", "comms.test_connection", T_IMP, "IMPLEMENTED_TESTED", "Mock transport only"),
    ("GET", "/api/integrations/simpro/last-synced"): ("team/resource", "imports.last_synced", T_IMP, "IMPLEMENTED_TESTED", "Committed imports only; a preview never counts"),
    ("GET", "/api/integrations/simpro/workers/last-sync"): ("team/resource", "imports.last_synced", T_IMP, "IMPLEMENTED_TESTED", "Same route"),
    ("POST", "/api/integrations/simpro/workers/refresh"): ("team/resource", "imports.sync", T_IMP, "IMPLEMENTED_TESTED", "POST /api/integrations/simpro/sync resource=workers (D-063)"),
    ("POST", "/api/workers/sync-from-simpro"): ("team/resource", "imports.sync", T_IMP, "IMPLEMENTED_TESTED", "Same route"),
    ("POST", "/api/integrations/simpro/sync-sites"): ("team/resource", "imports.sync", T_IMP, "IMPLEMENTED_TESTED", "resource=sites"),
    ("POST", "/api/integrations/simpro/sync-suppliers"): ("team/resource", "imports.sync", T_IMP, "IMPLEMENTED_TESTED", "resource=suppliers"),
    ("POST", "/api/integrations/simpro/suppliers/sync"): ("team/resource", "imports.sync", T_IMP, "IMPLEMENTED_TESTED", "resource=suppliers"),
    ("GET", "/api/integrations/simpro/suppliers"): ("team/resource", "suppliers.list_suppliers", T_IMP, "IMPLEMENTED_TESTED", "Imported vendors are supplier rows; no separate cache"),
    ("GET", "/api/integrations/simpro/suppliers/cached"): ("team/resource", "suppliers.list_suppliers", T_IMP, "IMPLEMENTED_TESTED", "Same"),
    ("POST", "/api/contractors/import-from-simpro"): ("team/resource", "suppliers.update_supplier", T_IMP, "IMPLEMENTED_TESTED", "Imported vendors arrive as suppliers; ticking contractor promotes one"),
    ("POST", "/api/workers/certifications/scan-reminders"): ("team/resource", "workers.scan_reminders", "tests/test_comms.py", "IMPLEMENTED_TESTED", "Preview by default; one reminder per certificate, expiry date and state (D-065)"),
    ("POST", "/api/workers/certifications/{cert_id}/send-reminder"): ("team/resource", "workers.send_reminder", "tests/test_comms.py", "IMPLEMENTED_TESTED", "Through the comms boundary"),
})
NEW_ROWS += [
    ["POST", "/api/integrations/simpro/sync", "integrations", "", "team/resource", "imports.sync", T_IMP, "IMPLEMENTED_TESTED", "New: one import route with dry run, per-company results and review items"],
    ["GET", "/api/integrations/simpro/runs", "integrations", "", "team/resource", "imports.runs", T_IMP, "IMPLEMENTED_TESTED", "New: import history"],
    ["GET", "/api/integrations/simpro/review", "integrations", "", "team/resource", "imports.review_queue", T_IMP, "IMPLEMENTED_TESTED", "New: possible duplicates awaiting a person"],
    ["POST", "/api/integrations/simpro/review/{item_id}/resolve", "integrations", "", "team/resource", "imports.resolve", T_IMP, "IMPLEMENTED_TESTED", "New: link, create or ignore"],
    ["POST", "/api/suppliers/documents/scan-reminders", "suppliers", "", "team/resource", "suppliers.scan_document_reminders", "tests/test_comms.py", "IMPLEMENTED_TESTED", "New: insurance and licence reminders"],
]
FEATURES.update({
    "INT-03": ("PARTIAL", "tests/test_imports.py; e2e connections step", "Built: multi-company import of workers, sites and suppliers by company + Simpro id; pagination; bounded retry on rate limits; per-company failure reporting; dry run; hand edits kept; review queue for look-alikes (link, create, ignore); simulator dataset labelled as simulator. NOT VERIFIED against a real Simpro (mock transport only; column names follow the public API documentation and must be confirmed with real credentials). Outstanding: jobs and customers, delta cursor, attachments and ZIP import, position-to-role mapping, scheduled sync."),
    "WORK-01": ("PARTIAL", "tests/test_workers.py; tests/test_imports.py; e2e workers step", "Built: roster (active/inactive/archived), details, photo, login link and provisioning, self-service edits, change history, duplicate warning, Simpro import by source key with review queue (unverified against a real Simpro). Outstanding: ID card/QR, mobile PIN, worker documents from Simpro."),
})

T_FLEET = "tests/test_fleet.py"
_A = "team/resource"
DONE.update({
    ("GET", "/api/assets"): (_A, "fleet.list_assets", T_FLEET, "IMPLEMENTED_TESTED", "Server-side filters and paging"),
    ("POST", "/api/assets"): (_A, "fleet.create_asset", T_FLEET, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/assets/{asset_id}"): (_A, "fleet.get_one", T_FLEET, "IMPLEMENTED_TESTED", ""),
    ("PUT", "/api/assets/{asset_id}"): (_A, "fleet.update_asset", T_FLEET, "IMPLEMENTED_TESTED", "Meters are changed through the meter routes so history is kept"),
    ("DELETE", "/api/assets/{asset_id}"): (_A, "fleet.delete_asset", T_FLEET, "IMPLEMENTED_TESTED", "Soft delete; a sync does not bring it back"),
    ("GET", "/api/assets/{asset_id}/qr.png"): (_A, "fleet.qr_png", T_FLEET, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/assets/{asset_id}/label.pdf"): (_A, "fleet.label_pdf", T_FLEET, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/assets/labels/bulk"): (_A, "fleet.labels_bulk", T_FLEET, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/assets/scan/{scan_token}"): (_A, "fleet.scan", T_FLEET, "IMPLEMENTED_TESTED", "Signed-in people of the same organisation only (D-069)"),
    ("GET", "/api/forms/assets/lookup"): (_A, "fleet.scan", T_FLEET, "IMPLEMENTED_TESTED", "GET /api/assets/scan/{scan_token}"),
    ("GET", "/api/forms/assets/picker"): (_A, "fleet.vehicle_picker", T_FLEET, "IMPLEMENTED_TESTED", "GET /api/forms/fleet/vehicles"),
    ("POST", "/api/assets/{asset_id}/meter"): (_A, "fleet.update_meter", T_FLEET, "IMPLEMENTED_TESTED", "A lower reading is refused with an explanation"),
    ("POST", "/api/assets/{asset_id}/meter/reset"): (_A, "fleet.reset_meter", T_FLEET, "IMPLEMENTED_TESTED", "Admin only, reason kept"),
    ("POST", "/api/assets/{asset_id}/meter-history"): (_A, "fleet.update_meter", T_FLEET, "IMPLEMENTED_TESTED", "POST /api/assets/{asset_id}/meter with a reading_date"),
    ("GET", "/api/assets/{asset_id}/meter-trends"): (_A, "fleet.meter_history", T_FLEET, "IMPLEMENTED_TESTED", "GET /api/assets/{asset_id}/meter-history (a list, no chart yet)"),
    ("POST", "/api/assets/{asset_id}/photos"): (_A, "fleet.add_document", T_FLEET, "IMPLEMENTED_TESTED", "POST /api/assets/{asset_id}/documents with kind=photo"),
    ("DELETE", "/api/assets/{asset_id}/photos/{photo_id}"): (_A, "fleet.delete_document", T_FLEET, "IMPLEMENTED_TESTED", "DELETE /api/assets/{asset_id}/documents/{doc_id}"),
    ("GET", "/api/assets/{asset_id}/photo/{gridfs_id}"): (_A, "fleet.download_document", T_FLEET, "IMPLEMENTED_TESTED", "GET /api/assets/{asset_id}/documents/{doc_id}/download"),
    ("GET", "/api/fleet/register"): (_A, "fleet.list_assets", T_FLEET, "IMPLEMENTED_TESTED", "GET /api/assets"),
    ("GET", "/api/fleet/categories"): (_A, "fleet.categories", T_FLEET, "IMPLEMENTED_TESTED", "GET /api/assets/categories"),
    ("GET", "/api/fleet/search"): (_A, "fleet.list_assets", T_FLEET, "IMPLEMENTED_TESTED", "GET /api/assets?q= (assets only; cross-register search is stage 9)"),
    ("GET", "/api/forms/fleet/vehicles"): (_A, "fleet.vehicle_picker", T_FLEET, "IMPLEMENTED_TESTED", "Reads the register, so a Navixy outage does not break forms; typing a vehicle in is always allowed"),
    ("POST", "/api/forms/fleet/vehicle-overrides"): (_A, "forms.submit", T_FLEET, "IMPLEMENTED_TESTED", "A typed vehicle is stored in the answer with source=manual; no separate route"),
    ("GET", "/api/integrations/navixy/vehicles"): (_A, "fleet.navixy_sync", T_FLEET, "IMPLEMENTED_TESTED", "POST /api/fleet/navixy/sync (preview) lists the trackers; mock transport only"),
    ("GET", "/api/integrations/navixy/tags"): (_A, "fleet.navixy_status", T_FLEET, "IMPLEMENTED_TESTED", "GET /api/fleet/navixy/status"),
    ("GET", "/api/fleet/navixy/tags"): (_A, "fleet.navixy_status", T_FLEET, "IMPLEMENTED_TESTED", "GET /api/fleet/navixy/status; PUT /api/fleet/navixy/tags chooses them"),
    ("POST", "/api/integrations/navixy/get-hash"): (_A, "navixy._Session", T_FLEET, "IMPLEMENTED_TESTED", "The sign-in key is fetched and refreshed by the adapter; never returned to a browser"),
    ("POST", "/api/integrations/navixy/test-connection"): (_A, "comms.test_connection", "tests/test_comms.py", "IMPLEMENTED_TESTED", "Mock transport only"),
    ("POST", "/api/assets/navixy/sync-counters"): (_A, "fleet.navixy_sync", T_FLEET, "IMPLEMENTED_TESTED", "Meters arrive with POST /api/fleet/navixy/sync; counter route unverified against real Navixy"),
    ("GET", "/api/equipment"): (_A, "fleet.list_assets", T_FLEET, "IMPLEMENTED_TESTED", "GET /api/assets?kind=equipment (D-068)"),
    ("POST", "/api/equipment"): (_A, "fleet.create_asset", T_FLEET, "IMPLEMENTED_TESTED", "POST /api/assets kind=equipment"),
    ("GET", "/api/equipment/{eid}"): (_A, "fleet.get_one", T_FLEET, "IMPLEMENTED_TESTED", "GET /api/assets/{asset_id}"),
    ("PATCH", "/api/equipment/{eid}"): (_A, "fleet.update_asset", T_FLEET, "IMPLEMENTED_TESTED", "PUT /api/assets/{asset_id}"),
    ("DELETE", "/api/equipment/{eid}"): (_A, "fleet.delete_asset", T_FLEET, "IMPLEMENTED_TESTED", "DELETE /api/assets/{asset_id}"),
    ("GET", "/api/equipment/categories"): (_A, "fleet.list_categories", T_FLEET, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/equipment/categories"): (_A, "fleet.create_category", T_FLEET, "IMPLEMENTED_TESTED", ""),
    ("PATCH", "/api/equipment/categories/{cid}"): (_A, "fleet.rename_category", T_FLEET, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/equipment/categories/{cid}"): (_A, "fleet.delete_category", T_FLEET, "IMPLEMENTED_TESTED", "Refused while it still holds equipment"),
    ("GET", "/api/equipment/summary"): (_A, "fleet.equipment_summary", T_FLEET, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/equipment/{eid}/certs"): (_A, "fleet.add_document", T_FLEET, "IMPLEMENTED_TESTED", "POST /api/assets/{asset_id}/documents kind=certificate"),
    ("GET", "/api/equipment/{eid}/certs/{cert_id}"): (_A, "fleet.download_document", T_FLEET, "IMPLEMENTED_TESTED", "Asset document download"),
    ("DELETE", "/api/equipment/{eid}/certs/{cert_id}"): (_A, "fleet.delete_document", T_FLEET, "IMPLEMENTED_TESTED", "Asset document delete"),
    ("POST", "/api/equipment/{eid}/documents"): (_A, "fleet.add_document", T_FLEET, "IMPLEMENTED_TESTED", "POST /api/assets/{asset_id}/documents"),
    ("GET", "/api/equipment/{eid}/documents/{doc_id}"): (_A, "fleet.download_document", T_FLEET, "IMPLEMENTED_TESTED", "Asset document download"),
    ("DELETE", "/api/equipment/{eid}/documents/{doc_id}"): (_A, "fleet.delete_document", T_FLEET, "IMPLEMENTED_TESTED", "Asset document delete"),
})
NEW_ROWS += [
    ["GET", "/api/assets/categories", "fleet", "", _A, "fleet.categories", T_FLEET, "IMPLEMENTED_TESTED", "New path for the filter counts"],
    ["POST", "/api/assets/{asset_id}/rotate-qr", "fleet", "", _A, "fleet.rotate_qr", T_FLEET, "IMPLEMENTED_TESTED", "New: retire printed labels"],
    ["POST", "/api/assets/{asset_id}/meter/dismiss-review", "fleet", "", _A, "fleet.dismiss_meter_review", T_FLEET, "IMPLEMENTED_TESTED", "New: a lower provider reading was looked at"],
    ["GET", "/api/assets/{asset_id}/meter-history", "fleet", "", _A, "fleet.meter_history", T_FLEET, "IMPLEMENTED_TESTED", "New path"],
    ["POST", "/api/assets/{asset_id}/documents", "fleet", "", _A, "fleet.add_document", T_FLEET, "IMPLEMENTED_TESTED", "New: one route for photos, documents and certificates"],
    ["GET", "/api/assets/{asset_id}/documents/{doc_id}/download", "fleet", "", _A, "fleet.download_document", T_FLEET, "IMPLEMENTED_TESTED", "New"],
    ["DELETE", "/api/assets/{asset_id}/documents/{doc_id}", "fleet", "", _A, "fleet.delete_document", T_FLEET, "IMPLEMENTED_TESTED", "New"],
    ["GET", "/api/fleet/navixy/status", "fleet", "", _A, "fleet.navixy_status", T_FLEET, "IMPLEMENTED_TESTED", "New: connection state, tags, last committed sync"],
    ["POST", "/api/fleet/navixy/sync", "fleet", "", _A, "fleet.navixy_sync", T_FLEET, "IMPLEMENTED_TESTED", "New: preview or bring in the tracker list"],
    ["PUT", "/api/fleet/navixy/tags", "fleet", "", _A, "fleet.navixy_tags", T_FLEET, "IMPLEMENTED_TESTED", "New: choose which Navixy tags feed the register"],
]
FEATURES.update({
    "FLEET-01": ("PARTIAL", "tests/test_fleet.py; e2e fleet step", "Built: register for vehicles, plant and trailers with filters and paging; local identity separate from the Navixy tracker id; Navixy tag selection; last position and last-heard time; odometer and hour meter kept apart with history; QR labels (PNG, PDF, bulk, rotate). NOT VERIFIED against a real Navixy account. Outstanding: trips and map, the three Navixy dashboards, NFC/UHF pairing, public (signed-out) scan page."),
    "INT-02": ("PARTIAL", "tests/test_fleet.py", "Built: region address, sign-in key stored encrypted and refreshed once when Navixy says it expired, tracker list, tags, states, odometer and hour counters, outage changes nothing, vanished trackers flagged not deleted, simulator labelled. NOT VERIFIED against a real account (mock transport only; the counter route in particular must be confirmed). Outstanding: automatic polling, Navixy groups (tags are used), vehicle records (registration, VIN)."),
    "EQUIP-01": ("PARTIAL", "tests/test_fleet.py; e2e fleet step", "Built: category catalogue, equipment register (as assets of kind equipment), status, where kept, inspection dates with overdue and due-soon states, certificates and documents with expiry. Outstanding: the legacy plant-maintenance catalogue and its re-import."),
})

T_SVC = "tests/test_service.py"
DONE.update({
    ("GET", "/api/assets/service/inbox"): (_A, "service.inbox", T_SVC, "IMPLEMENTED_TESTED", "Overdue, due soon and cannot-tell, worst first"),
    ("GET", "/api/assets/service/summary"): (_A, "service.summary", T_SVC, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/assets/service/scan-reminders"): (_A, "service.scan_reminders", T_SVC, "IMPLEMENTED_TESTED", "Preview by default; one per due point; through the comms boundary"),
    ("GET", "/api/assets/{asset_id}/schedules"): (_A, "service.list_schedules", T_SVC, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/assets/{asset_id}/schedules"): (_A, "service.create_schedule", T_SVC, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/assets/{asset_id}/schedules/{sid}"): (_A, "service.get_schedule", T_SVC, "IMPLEMENTED_TESTED", ""),
    ("PUT", "/api/assets/{asset_id}/schedules/{sid}"): (_A, "service.update_schedule", T_SVC, "IMPLEMENTED_TESTED", "Past records are not rewritten"),
    ("DELETE", "/api/assets/{asset_id}/schedules/{sid}"): (_A, "service.delete_schedule", T_SVC, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/assets/{asset_id}/records"): (_A, "service.list_records", T_SVC, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/assets/{asset_id}/records"): (_A, "service.create_record", T_SVC, "IMPLEMENTED_TESTED", "Restarts the linked schedule; moves the meter forward only"),
    ("GET", "/api/assets/{asset_id}/records/{rid}"): (_A, "service.get_record", T_SVC, "IMPLEMENTED_TESTED", ""),
    ("PUT", "/api/assets/{asset_id}/records/{rid}"): (_A, "service.update_record", T_SVC, "IMPLEMENTED_TESTED", ""),
    ("DELETE", "/api/assets/{asset_id}/records/{rid}"): (_A, "service.delete_record", T_SVC, "IMPLEMENTED_TESTED", "Needs the delete right"),
    ("POST", "/api/fleet/assets/{asset_id}/services"): (_A, "service.create_record", T_SVC, "IMPLEMENTED_TESTED", "POST /api/assets/{asset_id}/records"),
    ("GET", "/api/fleet/assets/{asset_id}"): (_A, "fleet.get_one", T_SVC, "IMPLEMENTED_TESTED", "GET /api/assets/{asset_id} plus its /schedules and /records"),
    ("GET", "/api/fleet/assets/{asset_id}/next-service"): (_A, "service.list_schedules", T_SVC, "IMPLEMENTED_TESTED", "Each schedule carries its status and due point; nothing is cached"),
    ("GET", "/api/fleet/service-status-rollup"): (_A, "fleet.list_assets", T_SVC, "IMPLEMENTED_TESTED", "The register list carries each asset's worst service status"),
    ("GET", "/api/fleet/service-schedule-presets"): (_A, "service.presets", T_SVC, "IMPLEMENTED_TESTED", "Common intervals; the baseline's exact preset values were not in the pack"),
    ("GET", "/api/plant/{plant_id}/maintenance"): (_A, "service.list_records", T_SVC, "IMPLEMENTED_TESTED", "GET /api/assets/{asset_id}/records"),
    ("GET", "/api/plant-maintenance/{uid}"): (_A, "service.get_record", T_SVC, "IMPLEMENTED_TESTED", "Service record"),
    ("PATCH", "/api/plant-maintenance/{uid}"): (_A, "service.update_record", T_SVC, "IMPLEMENTED_TESTED", "Service record"),
    ("DELETE", "/api/plant-maintenance/{uid}"): (_A, "service.delete_record", T_SVC, "IMPLEMENTED_TESTED", "Service record"),
})
FEATURES.update({
    "SERV-01": ("PARTIAL", "tests/test_service.py; tests/test_fleet.py; e2e fleet step", "Built: meter history; schedules by days, kilometres and hours (whichever first) with warning margins; status ok, due soon, overdue or cannot-tell; service, repair and inspection records with meters, cost and a checklist; recording a service restarts its schedule; schedule edits never rewrite history; fleet-wide due list; reminders with preview and dedupe. Outstanding: attachments on a schedule or record, the printable service check sheet (PDF and templates), technician picker, assigning a schedule to a person from the screen."),
})

T_FUEL = "tests/test_fuel.py"
_FU = "/api/fleet/fuel"
DONE.update({
    ("POST", _FU + "/import-csv"): (_A, "fuel.import_csv", T_FUEL, "IMPLEMENTED_TESTED", "Preview by default; explicit timezone recorded; admin only"),
    ("POST", _FU + "/sync-smartfill"): (_A, "fuel.sync_smartfill", T_FUEL, "IMPLEMENTED_TESTED", "Preview by default; shared call limiter; mock transport only"),
    ("GET", _FU + "/smartfill-status"): (_A, "fuel.smartfill_status", T_FUEL, "IMPLEMENTED_TESTED", "Says manual only: nothing imports by itself"),
    ("GET", _FU + "/batches"): (_A, "fuel.batches", T_FUEL, "IMPLEMENTED_TESTED", ""),
    ("GET", _FU + "/batches/{batch_id}"): (_A, "fuel.batch_detail", T_FUEL, "IMPLEMENTED_TESTED", ""),
    ("GET", _FU + "/transactions"): (_A, "fuel.transactions", T_FUEL, "IMPLEMENTED_TESTED", ""),
    ("GET", _FU + "/transactions/{txn_id}"): (_A, "fuel.transaction", T_FUEL, "IMPLEMENTED_TESTED", ""),
    ("POST", _FU + "/transactions/{txn_id}/match"): (_A, "fuel.match", T_FUEL, "IMPLEMENTED_TESTED", "Previous value, reviewer and reason audited"),
    ("GET", "/api/fleet/assets/{asset_id}/fuel"): (_A, "fuel.transactions", T_FUEL, "IMPLEMENTED_TESTED", "GET /api/fleet/fuel/transactions?asset_id="),
    ("GET", _FU + "/cards"): (_A, "fuel.cards", T_FUEL, "IMPLEMENTED_TESTED", ""),
    ("PATCH", _FU + "/cards/{card_number}"): (_A, "fuel.assign_card", T_FUEL, "IMPLEMENTED_TESTED", "Earlier fills re-matched only when asked"),
    ("POST", _FU + "/cards/{card_number}/assign"): (_A, "fuel.assign_card", T_FUEL, "IMPLEMENTED_TESTED", "PATCH /api/fleet/fuel/cards/{card_number}"),
    ("GET", _FU + "/cards/{card_number}/transactions"): (_A, "fuel.transactions", T_FUEL, "IMPLEMENTED_TESTED", "GET /api/fleet/fuel/transactions?card_number="),
    ("GET", _FU + "/weekly-diesel"): (_A, "fuel.weekly_diesel", T_FUEL, "IMPLEMENTED_TESTED", "This week, waiting weeks and the ledger of fixed rates"),
    ("POST", _FU + "/weekly-diesel/refresh"): (_A, "fuel.refresh_weekly", T_FUEL, "IMPLEMENTED_TESTED", "Business rule B04; fixture 2.0300 reproduced; FuelCheck mocked"),
    ("PUT", _FU + "/weekly-diesel"): (_A, "fuel.enter_week", T_FUEL, "IMPLEMENTED_TESTED", "Different meaning from the baseline: an admin types in the seven source prices for a week with no rate (D-072)"),
    ("GET", _FU + "/price-settings"): (_A, "fuel.price_settings", T_FUEL, "IMPLEMENTED_TESTED", "Weekly mode only; optional start date"),
    ("PUT", _FU + "/price-settings"): (_A, "fuel.set_price_settings", T_FUEL, "IMPLEMENTED_TESTED", "No fixed or provisional price mode (D-071)"),
    ("GET", _FU + "/reports"): (_A, "fuel.reports", T_FUEL, "IMPLEMENTED_TESTED", "Per vehicle by default; day, driver, card"),
    ("GET", _FU + "/reports/export"): (_A, "fuel.reports_export", T_FUEL, "IMPLEMENTED_TESTED", "CSV, formula-safe"),
    ("POST", _FU + "/reports/email"): (_A, "fuel.email_report", T_FUEL, "IMPLEMENTED_TESTED", "Plain text through the comms boundary; no attachments"),
    ("GET", _FU + "/stats"): (_A, "fuel.stats", T_FUEL, "IMPLEMENTED_TESTED", ""),
    ("GET", _FU + "/anomalies"): (_A, "fuel.anomalies", T_FUEL, "IMPLEMENTED_TESTED", "One row per fill with every reason"),
    ("GET", _FU + "/anomalies/matching-ids"): (_A, "fuel.matching_ids", T_FUEL, "IMPLEMENTED_TESTED", ""),
    ("POST", _FU + "/anomalies/{txn_id}/resolve"): (_A, "fuel.resolve", T_FUEL, "IMPLEMENTED_TESTED", ""),
    ("POST", _FU + "/anomalies/{txn_id}/dismiss"): (_A, "fuel.dismiss", T_FUEL, "IMPLEMENTED_TESTED", "A reason is required"),
    ("POST", _FU + "/anomalies/{txn_id}/reopen"): (_A, "fuel.reopen", T_FUEL, "IMPLEMENTED_TESTED", ""),
    ("POST", _FU + "/anomalies/bulk-resolve"): (_A, "fuel.bulk_resolve", T_FUEL, "IMPLEMENTED_TESTED", "Changed, unchanged and not-found counts"),
    ("POST", _FU + "/anomalies/bulk-dismiss"): (_A, "fuel.bulk_dismiss", T_FUEL, "IMPLEMENTED_TESTED", ""),
    ("POST", _FU + "/anomalies/bulk-reopen"): (_A, "fuel.bulk_reopen", T_FUEL, "IMPLEMENTED_TESTED", ""),
    ("POST", _FU + "/anomalies/bulk-attribute"): (_A, "fuel.bulk_attribute", T_FUEL, "IMPLEMENTED_TESTED", ""),
    ("POST", "/api/integrations/smartfill/test-connection"): (_A, "comms.test_connection", T_FUEL, "IMPLEMENTED_TESTED", "Mock transport only"),
})
FEATURES.update({
    "FUEL-01": ("PARTIAL", "tests/test_fuel.py; e2e fuel step", "Built: CSV import with preview, stated timezone, row-level errors and batch history; SmartFill import through the adapter; upsert on the provider's transaction id (gaps filled, conflicts counted, never duplicated); attribution labelled direct (registration), card, manual or unmatched; card assignment with optional re-match; unmatched fuel stays in totals. SmartFill is NOT VERIFIED against the real service. Outstanding: card date windows and worker cards, deleting an import batch, scheduled import."),
    "FUEL-02": ("PARTIAL", "tests/test_fuel.py; e2e fuel step", "Built and tested against the pack's fixture (200..206 cents gives 2.0300; 10 L = 20.30, 25 L = 50.75, total 71.05): Monday to Sunday weeks in the organisation's timezone, seven source days required, half-up rounding, atomic write-once rate, per-fill cost written once, pending state with unpriced litres disclosed, no fallback price, diesel only, admin entry of the seven prices by hand. NOT VERIFIED: the live FuelCheck request (mocked; its availability and terms must be checked before switching it on)."),
    "FUEL-03": ("PARTIAL", "tests/test_fuel.py; e2e fuel step", "Built: per-vehicle default, by day (fill date), driver and card; totals with an explicit not-complete marker; supplier amounts shown separately; CSV download; email as plain text through the outbox. Outstanding: PDF and attachments on the email, drill-down from a report row, per-asset fuel panel on the asset page."),
    "FUEL-04": ("PARTIAL", "tests/test_fuel.py; e2e fuel step", "Built: seven plain-language reasons with evidence judged at the time of the fill; one row per fill; no duplicate flags on re-import; resolve, dismiss (reason required), reopen, match to a vehicle; bulk actions with counts; select all matching; a corrected reason closes itself with a note; nothing is sent. Outstanding: filters by driver and site, hiding and restoring dismissed items."),
    "INT-04": ("PARTIAL", "tests/test_fuel.py", "Built: the 'parameters' envelope, list or table answers, paging guard (no progress stops; page cap), durable shared call limiter (6 a minute, 60 an hour, 600 a day), simulator labelled and kept out of real totals. NOT VERIFIED against FMT Data (mock transport only; column names in a real answer must be confirmed). Outstanding: scheduled sync, tank levels, Retry-After handling, counting the connection test against the limiter."),
})

T_LEAVE = "tests/test_leave.py"
DONE.update({
    ("GET", "/api/me/leave"): ("self", "leave.my_leave", T_LEAVE, "IMPLEMENTED_TESTED", "Balances are not shown yet (they belong to payroll)"),
    ("POST", "/api/me/leave"): ("self", "leave.request_leave", T_LEAVE, "IMPLEMENTED_TESTED", "Working days x hours a day unless hours are given; overlap refused"),
    ("POST", "/api/me/leave/{leave_id}/cancel"): ("self", "leave.cancel_mine", T_LEAVE, "IMPLEMENTED_TESTED", "Leave already used by a pay run is flagged for payroll, not undone"),
    ("POST", "/api/me/leave/{leave_id}/certificate"): ("self", "leave.upload_certificate", T_LEAVE, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/leave"): (_A, "leave.list_leave", T_LEAVE, "IMPLEMENTED_TESTED", "Needs team access"),
    ("GET", "/api/leave/{leave_id}"): (_A, "leave.get_leave", T_LEAVE, "IMPLEMENTED_TESTED", "Own request, or team access"),
    ("POST", "/api/leave/{leave_id}/decision"): (_A, "leave.decide", T_LEAVE, "IMPLEMENTED_TESTED", "Never your own; a reason is needed to refuse; no email is sent (D-076)"),
    ("GET", "/api/leave/{leave_id}/certificate"): (_A, "leave.certificate", T_LEAVE, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/leave/on-date"): (_A, "leave.on_date", T_LEAVE, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/leave/summary"): (_A, "leave.summary", T_LEAVE, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/leave/settings"): (_A, "leave.get_settings", T_LEAVE, "IMPLEMENTED_TESTED", "Hours a day, back-dating limit, working days"),
    ("PUT", "/api/leave/settings"): (_A, "leave.put_settings", T_LEAVE, "IMPLEMENTED_TESTED", "Admin only"),
})
NEW_ROWS += [
    ["POST", "/api/leave", "leave", "", _A, "leave.enter_leave", T_LEAVE, "IMPLEMENTED_TESTED", "New: the office enters a request for a worker"],
    ["POST", "/api/leave/{leave_id}/cancel", "leave", "", _A, "leave.cancel", T_LEAVE, "IMPLEMENTED_TESTED", "New: the office cancels a request"],
]

FEATURES.update({
    "LEAVE-01": ("PARTIAL", "tests/test_leave.py; e2e leave step", "Built: own request with hours from working days, overlap refusal, evidence upload, own cancel; office list, entry on a worker's behalf, decision by someone else with a reason to refuse, who is away on a date, summary, settings; cancelling leave a pay run has used is flagged for payroll. Outstanding: leave balances, allocation by a pay run (stage 7c), leave by email, a warning when a daily job is given to someone on leave."),
})

T_TS = "tests/test_timesheets.py"
DONE.update({
    ("PUT", "/api/users/{user_id}/payroll-access"): ("payroll owner", "timesheets.set_payroll_access", T_TS, "IMPLEMENTED_TESTED", "Owner only; administrators only; none, view or edit"),
    ("GET", "/api/payroll/workbench/access/list"): ("payroll owner", "timesheets.payroll_access_list", T_TS, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/me/payroll/timesheets"): ("self", "timesheets.my_timesheets", T_TS, "IMPLEMENTED_TESTED", "One explicit seven-day week"),
    ("PUT", "/api/me/payroll/timesheets/{day}"): ("self", "timesheets.save_my_day", T_TS, "IMPLEMENTED_TESTED", "Draft only; hours worked out on the server; expected revision"),
    ("PUT", "/api/me/payroll/timesheets/{day}/segments"): ("self", "timesheets.save_my_day", T_TS, "IMPLEMENTED_TESTED", "One editor: a day is a list of shifts with site and kind of work (D-078)"),
    ("POST", "/api/me/payroll/submit"): ("self", "timesheets.submit_my_week", T_TS, "IMPLEMENTED_TESTED", "Empty days are left out"),
    ("GET", "/api/me/payroll/time-catalog"): ("self", "timesheets.my_catalog", T_TS, "IMPLEMENTED_TESTED", "Fixed categories; no client or job catalogue yet"),
    ("GET", "/api/payroll/time-catalog"): ("payroll", "timesheets.catalog", T_TS, "IMPLEMENTED_TESTED", ""),
    ("GET", "/api/payroll/timesheets"): ("payroll", "timesheets.list_timesheets", T_TS, "IMPLEMENTED_TESTED", "Payroll access only"),
    ("POST", "/api/payroll/timesheets"): ("payroll", "timesheets.enter_day", T_TS, "IMPLEMENTED_TESTED", "One record per worker and date"),
    ("PATCH", "/api/payroll/timesheets/{entry_id}"): ("payroll", "timesheets.edit_day", T_TS, "IMPLEMENTED_TESTED", "Changing an approved day puts it back to waiting; locked days refuse"),
    ("POST", "/api/payroll/timesheets/{entry_id}/review"): ("payroll", "timesheets.edit_day", T_TS, "IMPLEMENTED_TESTED", "PATCH, then approve or send back"),
    ("DELETE", "/api/payroll/timesheets/{entry_id}"): ("payroll", "timesheets.delete_day", T_TS, "IMPLEMENTED_TESTED", "Never an approved or paid day"),
    ("POST", "/api/payroll/timesheets/approve"): ("payroll", "timesheets.approve", T_TS, "IMPLEMENTED_TESTED", "Own days are skipped"),
    ("POST", "/api/payroll/timesheets/reject"): ("payroll", "timesheets.reject", T_TS, "IMPLEMENTED_TESTED", "A reason is required"),
    ("POST", "/api/payroll/timesheets/prefill"): ("payroll", "timesheets.prefill", T_TS, "IMPLEMENTED_TESTED", "Drafts from site sign-ons; never overwrites"),
    ("GET", "/api/payroll/summary"): ("payroll", "timesheets.summary", T_TS, "IMPLEMENTED_TESTED", ""),
})
FEATURES.update({
    "PAY-01": ("PARTIAL", "tests/test_timesheets.py; tests/test_permissions_unit.py; e2e timesheets step", "Built: payroll opens only for the configured owner or a named view or edit grant held by an administrator; a role or override never opens it (also tested by writing an override straight into the database); the owner cannot be removed; changes are audited and take effect on the next request. Outstanding: the baseline's separate payroll invitation step."),
    "TIME-01": ("PARTIAL", "tests/test_timesheets.py; tests/test_payroll_engine.py; e2e timesheets step", "Built: one record per worker and date; shifts with site and kind of work; hours by category worked out on the server by the pack's allocation rules (all six worked examples reproduced); draft, sent, approved, sent back, locked; optimistic revision; office entry, correction, approval, sending back with a reason, drafts from site sign-ons; nobody approves their own days. Outstanding: client and job catalogue from Simpro, recent clients, unmatched-client review, the phone layout (stage 8)."),
})


def main() -> None:
    path = HERE / "endpoint_coverage.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    header, body = rows[0], rows[1:]
    seen = set()
    for row in body:
        key = (row[0], row[1])
        seen.add(key)
        if key in DONE:
            row[4], row[5], row[6], row[7], row[8] = DONE[key]
    missing = sorted(set(DONE) - seen)
    assert not missing, f"Not in baseline inventory: {missing}"
    for row in body:
        if (row[0], row[1]) in OPTIONAL:
            row[4], row[5], row[6], row[7], row[8] = OPTIONAL[(row[0], row[1])]
    body = [r for r in body if (r[0], r[1]) not in {(n[0], n[1]) for n in NEW_ROWS}] + NEW_ROWS
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows([header, *body])

    path = HERE / "feature_coverage.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    for row in rows[1:]:
        if row[0] in FEATURES:
            row[3], row[4], row[5] = FEATURES[row[0]]
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)

    done = sum(1 for r in body if r[7].startswith("IMPLEMENTED"))
    print(f"{done} operations implemented of {len(body)} listed; {sum(1 for r in body if r[7]=='NOT_STARTED')} not started")


if __name__ == "__main__":
    main()
