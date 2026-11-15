"""Record build status in the two coverage ledgers. Re-runnable; add entries as stages land."""
import csv
from pathlib import Path

HERE = Path(__file__).parent
T_AUTH, T_USERS, T_RST = "tests/test_auth.py", "tests/test_users.py", "tests/test_roles_sessions_tenancy.py"
T_ORG = "tests/test_org_setup_files.py"

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
}

NEW_ROWS = [
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
