from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.db import get_engine
from app.models import Base

from .conftest import bearer


# ---------- role catalogue ----------

def test_system_roles_are_seeded_and_read_only(world, client):
    admin = world.auth("admin1")
    roles = client.get("/api/admin/roles", headers=admin).json()
    assert roles["count"] == 8
    by_id = {r["role_id"]: r for r in roles["roles"]}
    assert by_id["admin"]["user_count"] == 2 and by_id["worker"]["user_count"] == 1
    assert client.patch("/api/admin/roles/worker", headers=admin, json={"name": "Labourer"}).status_code == 400
    assert client.delete("/api/admin/roles/worker", headers=admin).status_code == 400
    assert client.get("/api/admin/roles", headers=world.auth("worker1")).status_code == 403


def test_custom_role_lifecycle(world, client):
    admin = world.auth("admin1")
    worker = world.id("worker1")
    r = client.post("/api/admin/roles", headers=admin, json={"name": "Traffic Controller", "permission_tokens": ["pre_starts.view", "pre_starts.edit", "sites.view"]})
    assert r.status_code == 201 and r.json()["role_id"] == "traffic_controller" and r.json()["is_system"] is False
    assert client.post("/api/admin/roles", headers=admin, json={"name": "traffic controller"}).status_code == 409
    assert client.post("/api/admin/roles", headers=admin, json={"name": "Bad", "permission_tokens": ["payroll.view"]}).status_code == 422
    assert client.post("/api/admin/roles", headers=admin, json={"name": "Bad", "permission_tokens": ["swms.fly"]}).status_code == 422

    assert client.patch(f"/api/users/{worker}", headers=admin, json={"role_id": "traffic_controller"}).status_code == 200
    me = client.get("/api/auth/me", headers=world.auth("worker1")).json()
    assert me["role"] == "worker" and me["role_id"] == "traffic_controller"
    assert me["effective_permissions"]["sites"]["view"] is True and me["effective_permissions"]["hazards"]["view"] is False

    # A per-user override survives a later change to the role's defaults.
    client.put(f"/api/users/{worker}/permissions", headers=admin, json={"overrides": {"sites": {"view": False}}})
    patched = client.patch("/api/admin/roles/traffic_controller", headers=admin, json={"permission_tokens": ["pre_starts.view", "sites.view", "hazards.view"]})
    assert patched.status_code == 200
    eff = client.get("/api/auth/me", headers=world.auth("worker1")).json()["effective_permissions"]
    assert eff["hazards"]["view"] is True  # role change is effective immediately
    assert eff["pre_starts"]["edit"] is False
    assert eff["sites"]["view"] is False  # the explicit denial is preserved

    assert client.get("/api/admin/roles/traffic_controller/assignees-count", headers=admin).json()["count"] == 1
    assert client.delete("/api/admin/roles/traffic_controller", headers=admin).status_code == 409
    audit = client.get("/api/admin/roles/traffic_controller/audit", headers=admin).json()
    assert [e["action"] for e in audit["entries"]] == ["role.updated", "role.created"]
    assert audit["entries"][0]["detail"]["tokens"] == {"added": ["hazards.view"], "removed": ["pre_starts.edit"]}

    # Deactivating a role removes its access without deleting anyone.
    client.patch("/api/admin/roles/traffic_controller", headers=admin, json={"is_active": False})
    assert client.get("/api/auth/me", headers=world.auth("worker1")).json()["effective_permissions"]["hazards"]["view"] is False
    assert client.patch(f"/api/users/{world.id('super1')}", headers=admin, json={"role_id": "traffic_controller"}).status_code == 400

    client.patch(f"/api/users/{worker}", headers=admin, json={"role_id": "worker"})
    assert client.delete("/api/admin/roles/traffic_controller", headers=admin).json() == {"deleted": True, "role_id": "traffic_controller", "soft": True}
    assert client.get("/api/admin/roles/traffic_controller", headers=admin).status_code == 404


def test_non_admin_role_editor_cannot_exceed_own_access(world, client):
    hseq = world.auth("hseq1")
    assert client.post("/api/admin/roles", headers=hseq, json={"name": "Sneaky", "permission_tokens": ["ai.use"]}).status_code == 403
    assert client.post("/api/admin/roles", headers=hseq, json={"name": "Fine", "permission_tokens": ["swms.view"]}).status_code == 201
    assert client.patch("/api/admin/roles/fine", headers=hseq, json={"permission_tokens": ["swms.view", "ai.use"]}).status_code == 403


def test_presets(world, client):
    admin = world.auth("admin1")
    listing = client.get("/api/permission-presets", headers=admin).json()
    assert len(listing["built_in"]) == 8 and listing["custom"] == []
    assert client.post("/api/permission-presets", headers=admin, json={"label": "  "}).status_code == 400
    assert client.post("/api/permission-presets", headers=admin, json={"label": "X", "permissions": {"payroll": {"view": True}}}).status_code == 422
    preset = client.post("/api/permission-presets", headers=admin, json={"label": "Read only", "permissions": {"swms": {"view": True, "edit": False}}}).json()
    assert client.put(f"/api/permission-presets/{preset['id']}", headers=admin, json={"label": "Read-only", "permissions": {"swms": {"view": True}}}).json()["label"] == "Read-only"
    assert client.put("/api/permission-presets/worker", headers=admin, json={"label": "X"}).status_code == 400
    assert client.delete("/api/permission-presets/worker", headers=admin).status_code == 400
    copy = client.post("/api/permission-presets/worker/duplicate", headers=admin)
    assert copy.status_code == 201 and copy.json()["label"] == "Worker (copy)" and "payroll" not in copy.json()["permissions"]
    assert client.delete(f"/api/permission-presets/{preset['id']}", headers=admin).json() == {"ok": True}
    assert client.delete(f"/api/permission-presets/{preset['id']}", headers=admin).status_code == 404
    assert client.post("/api/permission-presets", headers=world.auth("worker1"), json={"label": "X"}).status_code == 403


# ---------- sessions ----------

def test_active_sessions_admin(world, client):
    admin_token = world.login("admin1")
    admin = bearer(admin_token)
    worker_token = world.login("worker1")
    world.login("worker2")  # another organisation; must stay invisible
    assert client.get("/api/admin/active-sessions", headers=bearer(worker_token)).status_code == 403
    listing = client.get("/api/admin/active-sessions", headers=admin).json()
    assert listing["count"] == 2
    mine = next(s for s in listing["sessions"] if s["is_current"])
    theirs = next(s for s in listing["sessions"] if not s["is_current"])
    assert theirs["user_email"] == "worker1@one.example"
    assert "token" not in theirs and "token_hash" not in theirs

    assert client.delete(f"/api/admin/active-sessions/{mine['jti']}", headers=admin).status_code == 400
    assert client.delete(f"/api/admin/active-sessions/{theirs['jti']}", headers=admin).status_code == 204
    assert client.delete(f"/api/admin/active-sessions/{theirs['jti']}", headers=admin).status_code == 404
    assert client.get("/api/auth/me", headers=bearer(worker_token)).status_code == 401

    again = world.login("worker1")
    jti = next(s["jti"] for s in client.get("/api/admin/active-sessions", headers=admin).json()["sessions"] if not s["is_current"])
    r = client.post("/api/admin/active-sessions/bulk-revoke", headers=admin, json={"ids": [jti, mine["jti"], "missing"]}).json()
    assert r == {"requested": 3, "revoked": 1, "skipped_self": True, "not_found": ["missing"]}
    assert client.get("/api/auth/me", headers=bearer(again)).status_code == 401
    assert client.get("/api/auth/me", headers=admin).status_code == 200

    history = client.get(f"/api/admin/users/{world.id('worker1')}/session-history", headers=admin).json()
    assert history["count"] == 2 and {h["state"] for h in history["history"]} == {"revoked"}


# ---------- tenant isolation (SEC-01) ----------

def test_other_organisations_records_look_missing(world, client):
    admin1 = world.auth("admin1")
    other_user = world.id("worker2")
    other_admin = world.id("admin2")
    for method, path, body in [
        ("get", f"/api/users/{other_user}", None),
        ("patch", f"/api/users/{other_user}", {"name": "Hacked"}),
        ("delete", f"/api/users/{other_user}", None),
        ("get", f"/api/users/{other_user}/permissions", None),
        ("put", f"/api/users/{other_user}/permissions", {"overrides": {"swms": {"view": False}}}),
        ("post", f"/api/users/{other_user}/permissions/reset", None),
        ("post", f"/api/users/{other_user}/force-signout", None),
        ("post", f"/api/users/{other_user}/set-password", {"password": "Sneaky-Pass-1"}),
        ("post", f"/api/users/{other_user}/reset-password", None),
        ("post", f"/api/users/{other_admin}/invite", None),
        ("get", f"/api/users/{other_user}/access-status", None),
        ("post", f"/api/users/{other_user}/unlock", None),
        ("get", f"/api/admin/users/{other_user}/session-history", None),
    ]:
        response = client.request(method, path, headers=admin1, json=body)
        assert response.status_code == 404, (method, path, response.status_code)
    # Same display names exist in both organisations; each list shows only its own.
    one = client.get("/api/users?q=alex", headers=admin1).json()
    two = client.get("/api/users?q=alex", headers=world.auth("admin2")).json()
    assert one["total"] == two["total"] == 1
    assert one["items"][0]["id"] == world.id("admin1") and two["items"][0]["id"] == world.id("admin2")


def test_role_catalogues_and_presets_are_per_organisation(world, client):
    admin1, admin2 = world.auth("admin1"), world.auth("admin2")
    client.post("/api/admin/roles", headers=admin1, json={"name": "Yard Hand", "permission_tokens": ["swms.view"]})
    assert client.get("/api/admin/roles/yard_hand", headers=admin2).status_code == 404
    assert client.patch(f"/api/users/{world.id('worker2')}", headers=admin2, json={"role_id": "yard_hand"}).status_code == 404
    # The same name can be created independently in the other organisation.
    assert client.post("/api/admin/roles", headers=admin2, json={"name": "Yard Hand"}).status_code == 201
    preset = client.post("/api/permission-presets", headers=admin1, json={"label": "P", "permissions": {"swms": {"view": True}}}).json()
    assert client.put(f"/api/permission-presets/{preset['id']}", headers=admin2, json={"label": "Q"}).status_code == 404
    assert client.post(f"/api/users/{world.id('worker2')}/permissions/apply-preset", headers=admin2, json={"preset_id": preset["id"]}).status_code == 404
    assert client.get("/api/org", headers=admin2).json()["name"] == "Org Two"


def test_request_body_cannot_choose_the_organisation(world, client, db):
    # Extra org_id / user_id fields in a body are ignored; scope comes only from the session.
    r = client.post("/api/users", headers=world.auth("admin1"), json={"email": "z@one.example", "name": "Z", "org_id": world.org2.id})
    assert r.status_code == 201 and r.json()["org_id"] == world.org1.id


# ---------- migrations ----------

def test_migrations_match_the_models():
    with get_engine().connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


# ---------- catalogue endpoints ----------

def test_permission_schema_and_companies(world, client, db):
    from app.models import Company

    db.add_all([Company(org_id=world.org1.id, name="Paneltec Civil", code="PAN"),
                Company(org_id=world.org2.id, name="Other Division", code="OTH")])
    db.commit()
    assert client.get("/api/permissions/schema").status_code == 401
    schema = client.get("/api/permissions/schema", headers=world.auth("worker1")).json()
    assert len(schema["actions"]) == 12
    assert schema["resources"]["payroll"]["managed_by"] == "payroll_grants"
    assert schema["resources"]["comms_safe_mode"]["managed_by"] == "admin_override"
    assert "email" not in schema["resources"]["vehicles"]["supported_actions"]
    companies = client.get("/api/org/companies", headers=world.auth("worker1")).json()["companies"]
    assert [c["code"] for c in companies] == ["PAN"]  # the other organisation's division is not listed
