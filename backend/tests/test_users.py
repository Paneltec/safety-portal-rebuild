from sqlalchemy import select

from app.models import AuditLog, User

from .conftest import PASSWORD, bearer


def test_list_users_needs_permission(world, client):
    assert client.get("/api/users", headers=world.auth("worker1")).status_code == 403
    assert client.get("/api/users", headers=world.auth("auditor1")).status_code == 403
    assert client.get("/api/users", headers=world.auth("super1")).status_code == 403
    assert client.get("/api/users", headers=world.auth("hseq1")).status_code == 200
    assert client.get("/api/users", headers=world.auth("admin1")).status_code == 200


def test_list_users_paginates_filters_and_counts(world, client):
    h = world.auth("admin1")
    page = client.get("/api/users?limit=2&offset=0", headers=h).json()
    assert page["total"] == 6 and len(page["items"]) == 2 and page["limit"] == 2
    names = [u["name"] for u in client.get("/api/users?limit=200", headers=h).json()["items"]]
    assert names == sorted(names, key=str.lower)  # deterministic ordering
    assert client.get("/api/users?q=okafor", headers=h).json()["total"] == 1
    assert client.get("/api/users?role_id=admin", headers=h).json()["total"] == 2
    assert client.get("/api/users?status=disabled", headers=h).json()["total"] == 0
    assert client.get("/api/users?limit=500", headers=h).status_code == 422


def test_create_user_rules(world, client, db):
    admin, hseq = world.auth("admin1"), world.auth("hseq1")
    assert client.post("/api/users", headers=world.auth("worker1"), json={"email": "x@one.example", "name": "X"}).status_code == 403
    assert client.post("/api/users", headers=admin, json={"email": "worker1@one.example", "name": "Dup"}).status_code == 409
    assert client.post("/api/users", headers=admin, json={"email": "x@one.example", "name": "X", "role_id": "nope"}).status_code == 404
    # A non-admin cannot create an admin, or grant a role holding access they lack.
    assert client.post("/api/users", headers=hseq, json={"email": "x@one.example", "name": "X", "role_id": "admin"}).status_code == 403
    assert client.post("/api/users", headers=hseq, json={"email": "x@one.example", "name": "X", "role_id": "auditor"}).status_code == 403
    r = client.post("/api/users", headers=hseq, json={"email": "x@one.example", "name": "X", "role_id": "worker"})
    assert r.status_code == 201 and r.json()["status"] == "invited" and r.json()["worker_id"] is None
    assert db.scalars(select(AuditLog).where(AuditLog.action == "user.created")).one().actor_user_id == world.id("hseq1")


def test_update_user_and_role_guards(world, client):
    admin, hseq = world.auth("admin1"), world.auth("hseq1")
    worker = world.id("worker1")
    assert client.patch(f"/api/users/{worker}", headers=admin, json={}).status_code == 400
    r = client.patch(f"/api/users/{worker}", headers=admin, json={"name": "Tom O", "role_id": "supervisor"})
    assert r.status_code == 200 and r.json()["role_id"] == "supervisor" and r.json()["name"] == "Tom O"
    assert client.patch(f"/api/users/{worker}", headers=admin, json={"email": "admin1@one.example"}).status_code == 400
    assert client.patch(f"/api/users/{worker}", headers=admin, json={"role_id": "missing"}).status_code == 404
    # hseq cannot promote to admin, demote an admin, or change their own role.
    assert client.patch(f"/api/users/{worker}", headers=hseq, json={"role_id": "admin"}).status_code == 403
    assert client.patch(f"/api/users/{world.id('admin1')}", headers=hseq, json={"role_id": "worker"}).status_code == 403
    assert client.patch(f"/api/users/{world.id('hseq1')}", headers=hseq, json={"role_id": "worker"}).status_code == 403
    # The legacy `role` field works like role_id.
    assert client.patch(f"/api/users/{worker}", headers=admin, json={"role": "worker"}).json()["role_id"] == "worker"


def test_last_admin_cannot_be_removed(world, client, db):
    admin2 = world.auth("admin2")
    me = world.id("admin2")
    assert client.patch(f"/api/users/{me}", headers=admin2, json={"role_id": "worker"}).status_code == 400
    assert client.patch(f"/api/users/{me}", headers=admin2, json={"status": "disabled"}).status_code == 400
    assert client.delete(f"/api/users/{me}", headers=admin2).status_code == 400
    # With a second admin present, the first can be demoted.
    other = User(org_id=world.org2.id, email="second@two.example", name="Second", role_id="admin", status="active", password_hash="x")
    db.add(other)
    db.commit()
    assert client.patch(f"/api/users/{other.id}", headers=admin2, json={"role_id": "worker"}).status_code == 200
    assert client.patch(f"/api/users/{me}", headers=admin2, json={"role_id": "worker"}).status_code == 400


def test_disable_signs_the_user_out(world, client):
    token = world.login("worker1")
    r = client.patch(f"/api/users/{world.id('worker1')}", headers=world.auth("admin1"), json={"status": "disabled"})
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401
    assert client.post("/api/auth/login", json={"email": "worker1@one.example", "password": PASSWORD}).status_code == 401


def test_soft_then_hard_delete(world, client):
    admin = world.auth("admin1")
    worker = world.id("worker1")
    assert client.delete(f"/api/users/{worker}?hard=true", headers=admin).status_code == 400
    assert client.delete(f"/api/users/{worker}", headers=world.auth("auditor1")).status_code == 403
    r = client.delete(f"/api/users/{worker}", headers=admin)
    assert r.status_code == 200 and r.json()["deleted_at"]
    assert client.get("/api/users", headers=admin).json()["total"] == 5
    assert client.get("/api/users?include_deleted=true", headers=admin).json()["total"] == 6
    assert client.patch(f"/api/users/{worker}", headers=admin, json={"name": "Zed"}).status_code == 404
    assert client.delete(f"/api/users/{worker}?hard=true", headers=admin).json() == {"ok": True, "hard_deleted": True}
    assert client.get(f"/api/users/{worker}", headers=admin).status_code == 404


def test_force_signout_and_admin_set_password(world, client):
    admin = world.auth("admin1")
    worker = world.id("worker1")
    token = world.login("worker1")
    r = client.post(f"/api/users/{worker}/force-signout", headers=admin)
    assert r.json()["revoked_sessions"] == 1
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401

    assert client.post(f"/api/users/{worker}/set-password", headers=world.auth("hseq1"), json={"password": "Temp-Pass-123"}).status_code == 403
    assert client.post(f"/api/users/{worker}/set-password", headers=admin, json={"password": "short"}).status_code == 422
    assert client.post(f"/api/users/{worker}/set-password", headers=admin, json={"password": "Temp-Pass-123"}).status_code == 200
    login = client.post("/api/auth/login", json={"email": "worker1@one.example", "password": "Temp-Pass-123"})
    assert login.status_code == 200 and login.json()["user"]["must_change_password"] is True


def test_access_status(world, client):
    admin = world.auth("admin1")
    new_id = client.post("/api/users", headers=admin, json={"email": "n@one.example", "name": "N"}).json()["id"]
    assert client.get(f"/api/users/{new_id}/access-status", headers=admin).json()["state"] == "not_invited"
    client.post(f"/api/users/{new_id}/invite", headers=admin)
    status = client.get(f"/api/users/{new_id}/access-status", headers=admin).json()
    assert status["state"] == "invite_pending" and status["invite_expires_at"]
    assert client.get(f"/api/users/{world.id('worker1')}/access-status", headers=admin).json()["state"] == "active"
    assert client.get(f"/api/users/{new_id}/access-status", headers=world.auth("hseq1")).status_code == 403


def test_overrides_put_and_reset(world, client):
    admin = world.auth("admin1")
    worker = world.id("worker1")
    base = client.get(f"/api/users/{worker}/permissions", headers=admin).json()
    assert base["overrides"] == {} and base["effective"]["pre_starts"]["edit"] is True
    assert base["payroll_access"] == {"is_owner": False, "can_manage": False, "grants": []}

    r = client.put(f"/api/users/{worker}/permissions", headers=admin,
                   json={"overrides": {"pre_starts": {"edit": False}, "vehicles": {"view": True}},
                         "reasons": {"pre_starts.edit": "Suspended pending retraining"}})
    assert r.status_code == 200
    body = r.json()
    assert body["effective"]["pre_starts"]["edit"] is False and body["effective"]["vehicles"]["view"] is True
    assert body["reasons"]["pre_starts.edit"] == "Suspended pending retraining"
    # The worker's own session sees the change on the next request.
    assert client.get("/api/auth/me", headers=world.auth("worker1")).json()["effective_permissions"]["vehicles"]["view"] is True

    for bad in ({"swms": {"edit": "false"}}, {"payroll": {"view": True}}, {"vehicles": {"email": True}}, {"nope": {"view": True}}):
        assert client.put(f"/api/users/{worker}/permissions", headers=admin, json={"overrides": bad}).status_code == 422

    reset = client.post(f"/api/users/{worker}/permissions/reset", headers=admin).json()
    assert reset["overrides"] == {} and reset["effective"]["pre_starts"]["edit"] is True


def test_non_admin_cannot_grant_what_they_lack_or_edit_admins_or_self(world, client):
    hseq = world.auth("hseq1")
    worker = world.id("worker1")
    # hseq_lead has no 'ai' access, so cannot hand it out.
    assert client.put(f"/api/users/{worker}/permissions", headers=hseq, json={"overrides": {"ai": {"use": True}}}).status_code == 403
    # ...but can grant something they hold, and can always deny.
    assert client.put(f"/api/users/{worker}/permissions", headers=hseq, json={"overrides": {"vehicles": {"view": True}, "swms": {"view": False}}}).status_code == 200
    assert client.put(f"/api/users/{world.id('admin1')}/permissions", headers=hseq, json={"overrides": {"swms": {"view": False}}}).status_code == 403
    assert client.put(f"/api/users/{world.id('hseq1')}/permissions", headers=hseq, json={"overrides": {"swms": {"view": False}}}).status_code == 403
    # Only an admin may touch comms safe mode.
    assert client.put(f"/api/users/{worker}/permissions", headers=hseq, json={"overrides": {"comms_safe_mode": {"edit": True}}}).status_code == 422
    ok = client.put(f"/api/users/{world.id('hseq1')}/permissions", headers=world.auth("admin1"), json={"overrides": {"comms_safe_mode": {"edit": True}}})
    assert ok.status_code == 200 and ok.json()["effective"]["comms_safe_mode"]["edit"] is True


def test_admin_has_no_payroll_access_but_configured_owner_does(world, client):
    assert client.get("/api/auth/me", headers=world.auth("admin1")).json()["effective_permissions"]["payroll"]["view"] is False
    owner = client.get("/api/auth/me", headers=world.auth("owner1")).json()
    assert owner["effective_permissions"]["payroll"]["view"] is True
    perms = client.get(f"/api/users/{world.id('owner1')}/permissions", headers=world.auth("admin1")).json()
    assert perms["payroll_access"]["is_owner"] is True


def test_apply_preset_matches_target_without_changing_role(world, client):
    admin = world.auth("admin1")
    worker = world.id("worker1")
    client.put(f"/api/users/{world.id('worker1')}/permissions", headers=admin, json={"overrides": {"comms_safe_mode": {"view": True}}})
    assert client.post(f"/api/users/{worker}/permissions/apply-preset", headers=admin, json={}).status_code == 400
    assert client.post(f"/api/users/{worker}/permissions/apply-preset", headers=admin, json={"preset_key": "nope"}).status_code == 404
    r = client.post(f"/api/users/{worker}/permissions/apply-preset", headers=admin, json={"preset_key": "supervisor"})
    assert r.status_code == 200 and r.json()["applied_preset"] == {"key": "supervisor", "label": "Supervisor", "is_builtin": True}
    user = client.get(f"/api/users/{worker}", headers=admin).json()
    assert user["role_id"] == "worker"
    supervisor = client.get(f"/api/users/{world.id('super1')}", headers=admin).json()["effective_permissions"]
    effective = dict(user["effective_permissions"])
    # Specially controlled cells survive a preset; everything else matches the supervisor.
    assert effective.pop("comms_safe_mode")["view"] is True
    supervisor.pop("comms_safe_mode")
    assert effective == supervisor

    preset = client.post("/api/permission-presets", headers=admin, json={"label": "Fleet viewer", "permissions": {"vehicles": {"view": True, "open": True}}}).json()
    r = client.post(f"/api/users/{worker}/permissions/apply-preset", headers=admin, json={"preset_id": preset["id"]})
    assert r.json()["effective"]["vehicles"]["view"] is True
    assert client.get(f"/api/permission-presets/{preset['id']}/assignees", headers=admin).json()["count"] == 1


def test_bulk_assign_role(world, client, db):
    admin = world.auth("admin1")
    ids = [world.id("worker1"), world.id("super1"), world.id("worker2"), "missing"]
    assert client.post("/api/users/bulk-assign-role", headers=admin, json={"user_ids": [], "role_id": "worker"}).status_code == 400
    assert client.post("/api/users/bulk-assign-role", headers=admin, json={"user_ids": ids, "role_id": "admin"}).status_code == 400
    world.users["super1"].role_locked = True
    db.commit()
    r = client.post("/api/users/bulk-assign-role", headers=admin, json={"user_ids": ids, "role_id": "auditor"}).json()
    assert r["updated"] == 1 and r["skipped"] == 3
    reasons = {s["user_id"]: s["reason"] for s in r["detail"]["skipped"]}
    assert reasons[world.id("worker2")] == "not found"  # another organisation's user is invisible
    assert reasons[world.id("super1")] == "role is locked"
    db.expire_all()
    assert db.get(User, world.id("worker2")).role_id == "worker"


def test_bulk_restrict(world, client):
    admin = world.auth("admin1")
    ids = [world.id("worker1"), world.id("super1"), world.id("worker2")]
    assert client.post("/api/permissions/bulk-restrict", headers=admin, json={"user_ids": ids, "resource": "nope", "action": "view"}).status_code == 400
    assert client.post("/api/permissions/bulk-restrict", headers=admin, json={"user_ids": ids, "resource": "swms", "action": "fly"}).status_code == 400
    r = client.post("/api/permissions/bulk-restrict", headers=admin, json={"user_ids": ids, "resource": "hazards", "action": "edit", "reason": "Review"}).json()
    assert r["updated"] == ids[:2] and r["missing_user_ids"] == [world.id("worker2")]
    assert client.get("/api/auth/me", headers=world.auth("worker1")).json()["effective_permissions"]["hazards"]["edit"] is False
    assert client.get("/api/auth/me", headers=world.auth("worker2")).json()["effective_permissions"]["hazards"]["edit"] is True
