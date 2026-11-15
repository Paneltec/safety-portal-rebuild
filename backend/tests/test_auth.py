from datetime import timedelta

from sqlalchemy import select

from app.models import AuditLog, AuthToken, OutboxMessage, User, UserSession, utcnow
from app.security import hash_token

from .conftest import PASSWORD, bearer


def test_login_returns_token_and_user(world, client, db):
    r = client.post("/api/auth/login", json={"email": "ADMIN1@one.example", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "admin1@one.example"
    assert body["user"]["role"] == "admin"
    assert body["user"]["org_id"] == world.org1.id
    assert "password_hash" not in body["user"]
    # Only a hash of the token is stored.
    session = db.scalars(select(UserSession)).one()
    assert session.token_hash == hash_token(body["access_token"])
    assert body["access_token"] not in session.token_hash


def test_wrong_password_and_unknown_email_give_same_answer(world, client):
    a = client.post("/api/auth/login", json={"email": "admin1@one.example", "password": "nope-nope-1"})
    b = client.post("/api/auth/login", json={"email": "nobody@one.example", "password": "nope-nope-1"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_lockout_after_five_failures_then_admin_unlock(world, client):
    for _ in range(4):
        assert client.post("/api/auth/login", json={"email": "worker1@one.example", "password": "bad-bad-11"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "worker1@one.example", "password": "bad-bad-11"}).status_code == 423
    # Even the right password is refused while locked.
    assert client.post("/api/auth/login", json={"email": "worker1@one.example", "password": PASSWORD}).status_code == 423
    # A non-admin cannot unlock.
    assert client.post(f"/api/users/{world.id('worker1')}/unlock", headers=world.auth("hseq1")).status_code == 403
    assert client.post(f"/api/users/{world.id('worker1')}/unlock", headers=world.auth("admin1")).status_code == 200
    assert client.post("/api/auth/login", json={"email": "worker1@one.example", "password": PASSWORD}).status_code == 200


def test_disabled_and_invited_accounts_cannot_sign_in(world, client, db):
    world.users["worker1"].status = "disabled"
    world.users["super1"].status = "invited"
    db.commit()
    r = client.post("/api/auth/login", json={"email": "worker1@one.example", "password": PASSWORD})
    assert r.status_code == 401 and "disabled" in r.json()["detail"]
    r = client.post("/api/auth/login", json={"email": "super1@one.example", "password": PASSWORD})
    assert r.status_code == 403


def test_requests_without_or_with_bad_token_are_401(client, world):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers=bearer("made-up")).status_code == 401
    assert client.get("/api/users", headers=bearer("made-up")).status_code == 401


def test_me_reports_effective_permissions(world, client):
    me = client.get("/api/auth/me", headers=world.auth("worker1")).json()
    assert me["org_name"] == "Org One"
    perms = me["effective_permissions"]
    assert perms["pre_starts"]["edit"] is True
    assert perms["users"]["view"] is False
    assert perms["payroll"]["view"] is False


def test_logout_revokes_the_session(world, client):
    token = world.login("worker1")
    assert client.post("/api/auth/logout", headers=bearer(token)).json() == {"ok": True}
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_refresh_rotates_the_token(world, client):
    token = world.login("worker1")
    r = client.post("/api/auth/refresh", headers=bearer(token))
    assert r.status_code == 200
    new = r.json()["access_token"]
    assert new != token
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(new)).status_code == 200


def test_expired_and_idle_sessions_are_refused(world, client, db):
    token = world.login("worker1")
    session = db.scalars(select(UserSession)).one()
    session.last_seen_at = utcnow() - timedelta(hours=5)
    db.commit()
    r = client.get("/api/auth/me", headers=bearer(token))
    assert r.status_code == 401 and "inactivity" in r.json()["detail"]

    token = world.login("worker1", remember_me=True)
    session = db.scalars(select(UserSession).where(UserSession.remember_me.is_(True))).one()
    # Remember-me sessions are not subject to the idle timeout...
    session.last_seen_at = utcnow() - timedelta(days=3)
    db.commit()
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 200
    # ...but still end at their expiry.
    session.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_change_password(world, client):
    first = world.login("worker1")
    second = world.login("worker1")
    h = bearer(first)
    assert client.post("/api/auth/change-password", headers=h, json={"current_password": "wrong-one-1", "new_password": "Another-Pass-2"}).status_code == 401
    assert client.post("/api/auth/change-password", headers=h, json={"current_password": PASSWORD, "new_password": PASSWORD}).status_code == 400
    assert client.post("/api/auth/change-password", headers=h, json={"current_password": PASSWORD, "new_password": "short"}).status_code == 400
    r = client.post("/api/auth/change-password", headers=h, json={"current_password": PASSWORD, "new_password": "Another-Pass-2"})
    assert r.status_code == 200
    new = r.json()["access_token"]
    # Old token and every other session stop working; the new token works.
    assert client.get("/api/auth/me", headers=bearer(first)).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(second)).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(new)).status_code == 200
    assert client.post("/api/auth/login", json={"email": "worker1@one.example", "password": PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "worker1@one.example", "password": "Another-Pass-2"}).status_code == 200


def test_must_change_password_blocks_everything_else(world, client, db):
    world.users["hseq1"].must_change_password = True
    db.commit()
    token = world.login("hseq1")
    h = bearer(token)
    assert client.get("/api/auth/me", headers=h).json()["must_change_password"] is True
    r = client.get("/api/users", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "Password change required"
    r = client.post("/api/auth/change-password", headers=h, json={"current_password": PASSWORD, "new_password": "Fresh-Pass-33"})
    assert r.status_code == 200
    assert client.get("/api/users", headers=bearer(r.json()["access_token"])).status_code == 200


def test_update_profile(world, client):
    h = world.auth("worker1")
    assert client.post("/api/auth/update-profile", headers=h, json={}).status_code == 400
    assert client.post("/api/auth/update-profile", headers=h, json={"name": "  "}).status_code == 400
    assert client.post("/api/auth/update-profile", headers=h, json={"email": "admin1@one.example"}).status_code == 400
    r = client.post("/api/auth/update-profile", headers=h, json={"name": "Thomas Okafor"})
    assert r.status_code == 200 and r.json()["user"]["name"] == "Thomas Okafor"


def test_forgot_password_never_reveals_accounts_and_sends_nothing(world, client, db):
    assert client.post("/api/auth/forgot-password", json={"email": "worker1@one.example"}).json() == {"ok": True}
    assert client.post("/api/auth/forgot-password", json={"email": "nobody@one.example"}).json() == {"ok": True}
    messages = db.scalars(select(OutboxMessage)).all()
    assert len(messages) == 1
    assert messages[0].status == "held" and messages[0].recipient == "worker1@one.example"


def test_invite_flow_is_single_use(world, client, db):
    admin = world.auth("admin1")
    created = client.post("/api/users", headers=admin, json={"email": "New.Person@one.example", "name": "New Person", "role_id": "worker"})
    assert created.status_code == 201
    user_id = created.json()["id"]
    assert created.json()["status"] == "invited"

    r = client.post(f"/api/users/{user_id}/invite", headers=admin)
    assert r.status_code == 201
    link = r.json()["link"]
    assert link.startswith("https://portal.test/accept-invite?token=")
    assert r.json()["delivery"] == "held"
    token = link.split("token=")[1]
    # The raw token is not stored anywhere.
    stored = db.scalars(select(AuthToken)).one()
    assert stored.token_hash == hash_token(token)

    v = client.post("/api/auth/invite/validate", json={"token": token})
    assert v.status_code == 200 and v.json()["user_email"] == "new.person@one.example" and v.json()["org_name"] == "Org One"
    # An invite token cannot be used as a reset token.
    assert client.post("/api/auth/reset/validate", json={"token": token}).status_code == 400

    bad = client.post("/api/auth/invite/redeem", json={"token": token, "password": "Brand-New-77", "confirm_password": "different"})
    assert bad.status_code == 400
    ok = client.post("/api/auth/invite/redeem", json={"token": token, "password": "Brand-New-77", "confirm_password": "Brand-New-77"})
    assert ok.status_code == 200
    assert client.get("/api/auth/me", headers=bearer(ok.json()["access_token"])).json()["status"] == "active"
    # Second use fails.
    again = client.post("/api/auth/invite/redeem", json={"token": token, "password": "Brand-New-78", "confirm_password": "Brand-New-78"})
    assert again.status_code == 400
    assert client.post("/api/auth/login", json={"email": "new.person@one.example", "password": "Brand-New-77"}).status_code == 200


def test_new_invite_revokes_the_previous_one_and_links_expire(world, client, db):
    admin = world.auth("admin1")
    user_id = client.post("/api/users", headers=admin, json={"email": "p@one.example", "name": "P"}).json()["id"]
    first = client.post(f"/api/users/{user_id}/invite", headers=admin).json()["link"].split("token=")[1]
    second = client.post(f"/api/users/{user_id}/invite", headers=admin).json()["link"].split("token=")[1]
    assert client.post("/api/auth/invite/validate", json={"token": first}).status_code == 400
    assert client.post("/api/auth/invite/validate", json={"token": second}).status_code == 200
    row = db.scalars(select(AuthToken).where(AuthToken.token_hash == hash_token(second))).one()
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    r = client.post("/api/auth/invite/validate", json={"token": second})
    assert r.status_code == 400 and "expired" in r.json()["detail"]


def test_admin_reset_link_flow(world, client):
    old = world.login("worker1")
    r = client.post(f"/api/users/{world.id('worker1')}/reset-password", headers=world.auth("admin1"))
    assert r.status_code == 200
    token = r.json()["link"].split("token=")[1]
    assert "/reset-password?token=" in r.json()["link"]
    done = client.post("/api/auth/reset/redeem", json={"token": token, "password": "Reset-Pass-55", "confirm_password": "Reset-Pass-55"})
    assert done.status_code == 200
    assert client.get("/api/auth/me", headers=bearer(old)).status_code == 401
    assert client.post("/api/auth/login", json={"email": "worker1@one.example", "password": "Reset-Pass-55"}).status_code == 200
    # Only admins can create links.
    assert client.post(f"/api/users/{world.id('worker1')}/reset-password", headers=world.auth("hseq1")).status_code == 403


def test_public_signup_is_retired(client):
    assert client.post("/api/auth/signup", json={"email": "a@b.example", "password": "whatever-1", "name": "A"}).status_code == 410


def test_audit_log_never_contains_secrets(world, client, db):
    token = world.login("admin1")
    client.post("/api/auth/change-password", headers=bearer(token), json={"current_password": PASSWORD, "new_password": "Audit-Check-88"})
    client.post(f"/api/users/{world.id('worker1')}/set-password", headers=world.auth("owner1"), json={"password": "Temp-Pass-123"})
    dump = " ".join(str(row.detail) for row in db.scalars(select(AuditLog)).all())
    for secret in (PASSWORD, "Audit-Check-88", "Temp-Pass-123", token):
        assert secret not in dump
    assert db.scalars(select(User).where(User.email == "admin1@one.example")).one().password_hash != "Audit-Check-88"


def test_health_and_root(client):
    assert client.get("/api/health").json()["database"] == "ok"
    assert "version" in client.get("/api/").json()


def test_link_uses_browser_origin_when_no_public_url_is_configured(world, client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "public_base_url", "")
    headers = {**world.auth("admin1"), "Origin": "https://portal.example.net"}
    link = client.post(f"/api/users/{world.id('worker1')}/reset-password", headers=headers).json()["link"]
    assert link.startswith("https://portal.example.net/reset-password?token=")
    # A malformed Origin is ignored rather than trusted.
    headers["Origin"] = "https://evil.example/path"
    link = client.post(f"/api/users/{world.id('worker1')}/reset-password", headers=headers).json()["link"]
    assert link.startswith("http://testserver/reset-password?token=")
