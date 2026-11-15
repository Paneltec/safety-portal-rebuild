import hashlib

from sqlalchemy import select

from app.models import Organisation, StoredFile, User

from .conftest import PASSWORD, bearer

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SETUP = {"setup_key": "test-setup-key", "org_name": "Fresh Org", "name": "Stephen", "email": "Holder@fresh.example",
         "password": "Holder-Pass-77", "confirm_password": "Holder-Pass-77"}


# ---------- first-run setup and the account holder ----------

def test_setup_creates_the_account_holder_once(client, db):
    assert client.get("/api/setup/status").json() == {"needs_setup": True, "setup_key_configured": True}
    assert client.post("/api/setup", json={**SETUP, "setup_key": "wrong"}).status_code == 403
    assert client.post("/api/setup", json={**SETUP, "confirm_password": "nope"}).status_code == 400
    assert client.post("/api/setup", json={**SETUP, "password": "short", "confirm_password": "short"}).status_code == 400
    r = client.post("/api/setup", json=SETUP)
    assert r.status_code == 201
    user = r.json()["user"]
    assert user["role_id"] == "admin" and user["is_account_holder"] is True and user["email"] == "holder@fresh.example"
    me = client.get("/api/auth/me", headers=bearer(r.json()["access_token"])).json()
    assert me["org_name"] == "Fresh Org"
    # The account holder is also the payroll owner; nobody else is.
    assert me["effective_permissions"]["payroll"]["view"] is True
    org = client.get("/api/org", headers=bearer(r.json()["access_token"])).json()
    assert org["account_holder"]["name"] == "Stephen" and org["payroll_owner_user_id"] == user["id"]
    # Setup can never run a second time.
    assert client.get("/api/setup/status").json()["needs_setup"] is False
    assert client.post("/api/setup", json={**SETUP, "email": "other@fresh.example"}).status_code == 409
    assert db.scalar(select(Organisation.name)) == "Fresh Org"


def test_setup_is_refused_on_an_existing_install(world, client):
    assert client.post("/api/setup", json=SETUP).status_code == 409


def test_setup_needs_a_configured_key(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "setup_key", "")
    assert client.get("/api/setup/status").json()["setup_key_configured"] is False
    assert client.post("/api/setup", json=SETUP).status_code == 503


def test_account_holder_is_protected_from_other_admins(world, client):
    admin, holder_id = world.auth("admin1"), world.id("owner1")
    assert client.get(f"/api/users/{holder_id}", headers=admin).json()["is_account_holder"] is True
    assert client.patch(f"/api/users/{holder_id}", headers=admin, json={"name": "Renamed"}).status_code == 403
    assert client.patch(f"/api/users/{holder_id}", headers=admin, json={"status": "disabled"}).status_code == 403
    assert client.delete(f"/api/users/{holder_id}", headers=admin).status_code == 400
    assert client.post(f"/api/users/{holder_id}/set-password", headers=admin, json={"password": "Takeover-Pass-1"}).status_code == 403
    assert client.post(f"/api/users/{holder_id}/reset-password", headers=admin).status_code == 403
    assert client.post(f"/api/users/{holder_id}/force-signout", headers=admin).status_code == 403
    assert client.put(f"/api/users/{holder_id}/permissions", headers=admin, json={"overrides": {"swms": {"view": False}}}).status_code == 403
    r = client.post("/api/users/bulk-assign-role", headers=admin, json={"user_ids": [holder_id], "role_id": "worker"}).json()
    assert r["updated"] == 0 and r["detail"]["skipped"][0]["reason"] == "account holder"
    # The holder can edit their own details but cannot stop being an admin.
    owner = world.auth("owner1")
    assert client.patch(f"/api/users/{holder_id}", headers=owner, json={"name": "Olive O"}).status_code == 200
    assert client.patch(f"/api/users/{holder_id}", headers=owner, json={"role_id": "worker"}).status_code == 400
    assert client.post("/api/auth/login", json={"email": "owner@one.example", "password": PASSWORD}).status_code == 200


def test_transfer_account_holder(world, client):
    owner, admin = world.auth("owner1"), world.auth("admin1")
    target = world.id("admin1")
    assert client.post("/api/org/account-holder", headers=admin, json={"user_id": target}).status_code == 403
    assert client.post("/api/org/account-holder", headers=owner, json={"user_id": world.id("worker1")}).status_code == 400
    assert client.post("/api/org/account-holder", headers=owner, json={"user_id": world.id("admin2")}).status_code == 404
    r = client.post("/api/org/account-holder", headers=owner, json={"user_id": target})
    assert r.status_code == 200 and r.json()["owner_user_id"] == target
    # Payroll ownership does not move with the account.
    assert r.json()["payroll_owner_user_id"] == world.id("owner1")
    assert client.get("/api/auth/me", headers=admin).json()["effective_permissions"]["payroll"]["view"] is False
    assert client.patch(f"/api/users/{world.id('owner1')}", headers=admin, json={"name": "Former Holder"}).status_code == 200


# ---------- organisation settings ----------

def test_org_settings_admin_only_and_validated(world, client):
    admin = world.auth("admin1")
    assert client.patch("/api/org", headers=world.auth("hseq1"), json={"name": "X"}).status_code == 403
    assert client.patch("/api/org", headers=admin, json={}).status_code == 400
    assert client.patch("/api/org", headers=admin, json={"timezone": "Mars/Olympus"}).status_code == 422
    for bad in ("javascript:alert(1)", "ftp://x.example", "https://user:pw@x.example", "not a url"):
        assert client.patch("/api/org", headers=admin, json={"public_portal_url": bad}).status_code == 422
    assert client.patch("/api/org", headers=admin, json={"abn": "12AB"}).status_code == 422
    r = client.patch("/api/org", headers=admin, json={
        "name": "Org One Pty Ltd", "timezone": "Australia/Hobart", "portal_url": "https://portal.one.example/",
        "staff_login_url": "https://portal.one.example/login", "abn": "12 345 678 901", "suburb": "Launceston",
        "contact_email": "office@one.example", "website": "https://one.example"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Org One Pty Ltd" and body["public_portal_url"] == "https://portal.one.example"
    assert body["suburb"] == "Launceston" and body["website_url"] == "https://one.example"
    # No host-specific default is ever seeded.
    assert client.get("/api/org", headers=world.auth("admin2")).json()["public_portal_url"] is None
    assert client.get("/api/org", headers=world.auth("worker1")).json()["name"] == "Org One Pty Ltd"
    # Clearing a field works and is distinct from leaving it out.
    assert client.patch("/api/org", headers=admin, json={"suburb": ""}).json()["suburb"] is None


def test_companies_replace_keeps_ids_and_blocks_removal_in_use(world, client, db):
    admin = world.auth("admin1")
    assert client.put("/api/org/companies", headers=world.auth("hseq1"), json={"companies": []}).status_code == 403
    r = client.put("/api/org/companies", headers=admin, json={"companies": [{"name": "Paneltec Civil", "code": "pan"}, {"name": "Viatec Traffic"}]})
    assert r.status_code == 200
    companies = {c["code"]: c for c in r.json()["companies"]}
    assert set(companies) == {"PAN", "VIATEC_TRAFFIC"}
    assert client.put("/api/org/companies", headers=admin, json={"companies": [{"name": "A", "code": "x"}, {"name": "B", "code": "X"}]}).status_code == 409
    pan = companies["PAN"]["id"]
    assert client.patch(f"/api/users/{world.id('worker1')}", headers=admin, json={"company_id": pan}).status_code == 200
    # Rename keeps the id; removing a division with people is refused.
    renamed = client.put("/api/org/companies", headers=admin, json={"companies": [{"id": pan, "name": "Paneltec Civil Pty Ltd", "code": "PAN"}]}).json()
    assert renamed["companies"] == [{"id": pan, "name": "Paneltec Civil Pty Ltd", "code": "PAN"}]
    assert client.put("/api/org/companies", headers=admin, json={"companies": []}).status_code == 409
    assert client.get("/api/org/companies", headers=world.auth("admin2")).json() == {"companies": []}
    assert client.patch(f"/api/users/{world.id('worker2')}", headers=world.auth("admin2"), json={"company_id": pan}).status_code == 404


# ---------- logo and file storage ----------

def test_logo_upload_download_and_rules(world, client, db):
    admin = world.auth("admin1")
    assert client.post("/api/org/logo/upload", headers=world.auth("hseq1"), files={"file": ("logo.png", PNG, "image/png")}).status_code == 403
    # Content is checked, not just the label: a script named .png is refused.
    assert client.post("/api/org/logo/upload", headers=admin, files={"file": ("logo.png", b"<script>alert(1)</script>", "image/png")}).status_code == 415
    assert client.post("/api/org/logo/upload", headers=admin, files={"file": ("logo.svg", b"<svg/>", "image/svg+xml")}).status_code == 415
    assert client.post("/api/org/logo/upload", headers=admin, files={"file": ("logo.png", b"", "image/png")}).status_code == 400
    assert client.post("/api/org/logo/upload", headers=admin, files={"file": ("big.png", PNG + b"\x00" * (2 * 1024 * 1024), "image/png")}).status_code == 413

    r = client.post("/api/org/logo/upload", headers=admin, files={"file": ("../../evil name.png", PNG, "image/png")})
    assert r.status_code == 200
    url = r.json()["logo_url"]
    assert r.json()["logo_filename"] == "evil name.png"  # no path survives
    stored = db.scalars(select(StoredFile)).one()
    assert stored.sha256 == hashlib.sha256(PNG).hexdigest() and stored.size_bytes == len(PNG)
    # Downloaded bytes equal uploaded bytes; no session needed for branding.
    got = client.get(url)
    assert got.status_code == 200 and got.content == PNG and got.headers["content-type"] == "image/png"
    assert client.get("/api/org", headers=admin).json()["logo_url"] == url
    assert client.get("/api/org/logo/not-a-file").status_code == 404

    # Lost bytes are reported as missing, never served as an empty file.
    from app.files import _path_for

    _path_for(stored.org_id, stored.sha256).unlink()
    assert client.get(url).status_code == 410

    assert client.delete("/api/org/logo", headers=admin).json() == {"ok": True}
    assert client.get("/api/org", headers=admin).json()["logo_url"] is None


def test_public_branding_only_for_single_organisation_installs(world, client, db):
    assert client.get("/api/public/branding").json() == {"name": "Safety Portal", "logo_url": None}
    db.query(User).filter(User.org_id == world.org2.id).delete()
    from app.models import Role

    db.query(Role).filter(Role.org_id == world.org2.id).delete()
    db.delete(db.get(Organisation, world.org2.id))
    db.commit()
    assert client.get("/api/public/branding").json() == {"name": "Org One", "logo_url": None}
