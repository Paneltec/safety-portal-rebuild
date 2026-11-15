"""Record build status in the two coverage ledgers. Re-runnable; add entries as stages land."""
import csv
from pathlib import Path

HERE = Path(__file__).parent
T_AUTH, T_USERS, T_RST = "tests/test_auth.py", "tests/test_users.py", "tests/test_roles_sessions_tenancy.py"
T_ORG = "tests/test_org_setup_files.py"
T_DOC = "tests/test_documents.py"
T_FORM = "tests/test_forms_api.py; tests/test_forms_engine.py"

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

NEW_ROWS = [
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
    "NAV-01": ("PARTIAL", "Browser walkthrough (login, users, roles, sessions, profile)", "Shell with permission-filtered menu for built screens only. Preferences, app directory and version display outstanding."),
}


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
