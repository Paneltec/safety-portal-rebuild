"""Workers register, privacy tiers, login links and certifications (stage 4a)."""
from datetime import date, timedelta

from sqlalchemy import select

from app.models import AuditLog, Certification, OutboxMessage, User, Worker

W = "/api/workers"
TODAY = date.today()


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def new_worker(client, headers, **body):
    payload = {"first_name": "Mia", "last_name": "Tanaka", **body}
    response = client.post(W, json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------- register ----------

def test_create_list_filter_and_status_counts(world, client):
    admin = world.auth("admin1")
    mia = new_worker(client, admin, email="Mia@One.Example", position="Traffic controller", mobile="0400 000 001")
    assert mia["email"] == "mia@one.example" and mia["status"] == "active" and mia["source"] is None
    ben = new_worker(client, admin, first_name="Ben", last_name="Abbott", active=False)
    world.add_worker("Other", "Org", org=world.org2)

    listing = client.get(W, headers=admin).json()
    assert [w["name"] for w in listing["items"]] == ["Mia Tanaka"]
    assert listing["counts"] == {"active": 1, "inactive": 1, "archived": 0}
    assert [w["id"] for w in client.get(f"{W}?status=inactive", headers=admin).json()["items"]] == [ben["id"]]
    assert client.get(f"{W}?status=all&q=traffic", headers=admin).json()["total"] == 1

    # Archive is distinct from inactive, and reversible.
    assert client.post(f"{W}/{mia['id']}/archive", headers=admin).json()["status"] == "archived"
    assert client.get(W, headers=admin).json()["counts"] == {"active": 0, "inactive": 1, "archived": 1}
    assert client.post(f"{W}/{mia['id']}/unarchive", headers=admin).json()["status"] == "active"


def test_validation_and_permissions(world, client):
    admin, worker, supervisor = world.auth("admin1"), world.auth("worker1"), world.auth("super1")
    assert client.post(W, json={"first_name": " "}, headers=admin).status_code == 422
    assert client.post(W, json={"first_name": "A", "email": "nope"}, headers=admin).status_code == 422
    assert client.post(W, json={"first_name": "A", "company_id": "missing"}, headers=admin).status_code == 404
    assert client.post(W, json={"first_name": "A"}, headers=worker).status_code == 403
    assert client.post(W, json={"first_name": "A"}, headers=supervisor).status_code == 403  # view only
    assert client.get(W).status_code == 401


def test_same_name_or_email_is_flagged_for_review_never_merged(world, client):
    admin = world.auth("admin1")
    first = new_worker(client, admin, email="mia@one.example")
    second = new_worker(client, admin, email="mia@one.example")
    assert second["id"] != first["id"]
    assert [(d["id"], d["match"]) for d in second["possible_duplicates"]] == [(first["id"], "email")]


def test_tenant_isolation(world, client):
    admin1, admin2 = world.auth("admin1"), world.auth("admin2")
    mia = new_worker(client, admin1)
    for call in (
        lambda: client.get(f"{W}/{mia['id']}", headers=admin2),
        lambda: client.patch(f"{W}/{mia['id']}", json={"position": "x"}, headers=admin2),
        lambda: client.delete(f"{W}/{mia['id']}", headers=admin2),
        lambda: client.post(f"{W}/{mia['id']}/reveal", headers=admin2),
        lambda: client.get(f"{W}/{mia['id']}/certifications", headers=admin2),
    ):
        assert call().status_code == 404
    assert client.get(W, headers=admin2).json()["total"] == 0


def test_without_team_view_a_person_sees_only_their_own_record(world, client):
    admin, worker = world.auth("admin1"), world.auth("worker1")
    other = new_worker(client, admin)
    assert client.get(W, headers=worker).json()["items"] == []  # no linked record yet
    mine = world.add_worker("Tom", "Okafor", link="worker1", email="tom@one.example")
    listing = client.get(W, headers=worker).json()
    assert [w["id"] for w in listing["items"]] == [mine.id] and listing["items"][0]["is_me"] is True
    assert client.get(f"{W}/{other['id']}", headers=worker).status_code == 404
    assert client.get(f"{W}/{mine.id}", headers=worker).status_code == 200
    # Names for pickers are still available, without contact details.
    names = client.get(f"{W}/directory", headers=worker).json()["items"]
    assert {n["label"] for n in names} == {"Mia Tanaka", "Tom Okafor"} and "email" not in names[0]


# ---------- privacy tiers ----------

def test_personal_details_are_limited_and_protected_values_are_encrypted_and_masked(world, client, db):
    admin, supervisor, hseq = world.auth("admin1"), world.auth("super1"), world.auth("hseq1")
    mia = new_worker(
        client, admin, email="mia@one.example", street_address="1 Test St", suburb="Invermay",
        emergency_contact_name="Ken Tanaka", emergency_contact_phone="0400 000 009",
        birth_date="1990-04-12", tax_file_number="123 456 782", usi_number="ABC123XYZ9",
    )
    # Stored encrypted: the values appear nowhere in the row as plain text.
    row = db.get(Worker, mia["id"])
    assert row.protected and "123456782" not in row.protected and "1990" not in row.protected
    assert "123456782" not in str(row.details) and row.details["protected_hints"]["tax_file_number"] == "•••••• 782"

    # Admin: personal details and masked hints, never the full value in a normal read.
    seen = client.get(f"{W}/{mia['id']}", headers=admin).json()
    assert seen["street_address"] == "1 Test St" and seen["emergency_contact_name"] == "Ken Tanaka"
    tfn = next(p for p in seen["protected"] if p["field"] == "tax_file_number")
    assert tfn == {"field": "tax_file_number", "label": "Tax file number", "recorded": True, "masked": "•••••• 782"}
    assert "123456782" not in str(seen) and "1990-04-12" not in str(seen)

    # Supervisor (team view, no edit, no HR): work contact only.
    sup = client.get(f"{W}/{mia['id']}", headers=supervisor).json()
    assert sup["email"] == "mia@one.example" and sup["can_see_personal"] is False
    assert "street_address" not in sup and "emergency_contact_name" not in sup and "protected" not in sup

    # HSEQ lead can edit workers and sees masked hints, but cannot change or reveal protected values.
    lead = client.get(f"{W}/{mia['id']}", headers=hseq).json()
    assert lead["street_address"] == "1 Test St" and lead["can_reveal_protected"] is False
    assert client.patch(f"{W}/{mia['id']}", json={"tax_file_number": "987654321"}, headers=hseq).status_code == 403
    assert client.post(f"{W}/{mia['id']}/reveal", headers=hseq).status_code == 403
    assert client.patch(f"{W}/{mia['id']}", json={"position": "Leading hand"}, headers=hseq).json()["position"] == "Leading hand"

    # Reveal is permissioned and audited (field names only).
    revealed = client.post(f"{W}/{mia['id']}/reveal", headers=admin).json()["fields"]
    assert {f["field"]: f["value"] for f in revealed} == {
        "birth_date": "1990-04-12", "tax_file_number": "123456782", "usi_number": "ABC123XYZ9"}
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "worker.protected_revealed")).one()
    assert entry.detail == {"fields": ["birth_date", "tax_file_number", "usi_number"]} and entry.actor_user_id == world.id("admin1")
    updates = db.scalars(select(AuditLog).where(AuditLog.action.in_(("worker.created", "worker.updated")))).all()
    assert "123456782" not in str([u.detail for u in updates])

    # Clearing a protected value removes it and its hint.
    assert client.patch(f"{W}/{mia['id']}", json={"tax_file_number": None}, headers=admin).status_code == 200
    again = client.get(f"{W}/{mia['id']}", headers=admin).json()
    assert next(p for p in again["protected"] if p["field"] == "tax_file_number")["recorded"] is False
    assert client.patch(f"{W}/{mia['id']}", json={"tax_file_number": "12"}, headers=admin).status_code == 422


def test_protected_values_fail_clearly_when_the_key_is_missing_or_wrong(world, client, monkeypatch):
    from cryptography.fernet import Fernet

    from app.config import get_settings

    admin = world.auth("admin1")
    mia = new_worker(client, admin, tax_file_number="123456782")
    monkeypatch.setattr(get_settings(), "encryption_key", Fernet.generate_key().decode())
    wrong = client.post(f"{W}/{mia['id']}/reveal", headers=admin)
    assert wrong.status_code == 503 and "does not match" in wrong.json()["detail"]
    # Ordinary reads still work: listings never need the key.
    assert client.get(f"{W}/{mia['id']}", headers=admin).status_code == 200
    monkeypatch.setattr(get_settings(), "encryption_key", "")
    missing = client.post(W, json={"first_name": "No", "last_name": "Key", "usi_number": "X1"}, headers=admin)
    assert missing.status_code == 503 and "ENCRYPTION_KEY is not set" in missing.json()["detail"]
    assert client.post(W, json={"first_name": "No", "last_name": "Secrets"}, headers=admin).status_code == 201


def test_self_service_edits_only_the_permitted_fields(world, client):
    worker = world.auth("worker1")
    assert client.get(f"{W}/me", headers=worker).status_code == 404  # not linked yet
    mine = world.add_worker("Tom", "Okafor", link="worker1", position="Labourer")
    updated = client.patch(f"{W}/me", headers=worker, json={
        "preferred_name": "Tommy", "mobile": "0400 111 222", "emergency_contact_name": "Ada Okafor",
        "position": "Managing Director", "active": False, "tax_file_number": "123456782",
    }).json()
    assert updated["preferred_name"] == "Tommy" and updated["mobile"] == "0400 111 222"
    assert updated["emergency_contact_name"] == "Ada Okafor"
    assert updated["position"] == "Labourer" and updated["active"] is True  # office-only fields ignored
    assert all(p["recorded"] is False for p in updated["protected"])
    # The office route is still closed to them.
    assert client.patch(f"{W}/{mine.id}", json={"position": "Boss"}, headers=worker).status_code == 403
    assert client.post(f"{W}/{mine.id}/reveal", headers=worker).status_code == 403


def test_history_lists_changes_for_people_with_audit_access(world, client):
    admin, worker = world.auth("admin1"), world.auth("worker1")
    mia = new_worker(client, admin)
    client.patch(f"{W}/{mia['id']}", json={"position": "Leading hand", "suburb": "Mowbray"}, headers=admin)
    history = client.get(f"{W}/{mia['id']}/history", headers=admin).json()["items"]
    assert [h["action"] for h in history] == ["worker.updated", "worker.created"]
    assert history[0]["detail"] == {"fields": ["position", "suburb"]} and history[0]["actor"] == "Alex Rowe"
    world.add_worker("Tom", "Okafor", link="worker1")
    assert client.get(f"{W}/{mia['id']}/history", headers=worker).status_code == 403


# ---------- logins ----------

def test_link_unlink_and_one_login_per_worker(world, client, db):
    admin = world.auth("admin1")
    mia, ben = new_worker(client, admin), new_worker(client, admin, first_name="Ben", last_name="Abbott")
    linked = client.post(f"{W}/{mia['id']}/link-user", json={"user_id": world.id("worker1")}, headers=admin).json()
    assert linked["linked_user"]["id"] == world.id("worker1")
    # The same login cannot be attached to a second worker, nor a second login to the same worker.
    assert client.post(f"{W}/{ben['id']}/link-user", json={"user_id": world.id("worker1")}, headers=admin).status_code == 409
    assert client.post(f"{W}/{mia['id']}/link-user", json={"user_id": world.id("super1")}, headers=admin).status_code == 409
    # Another organisation's login is not found.
    assert client.post(f"{W}/{ben['id']}/link-user", json={"user_id": world.id("worker2")}, headers=admin).status_code == 404
    assert client.post(f"{W}/{ben['id']}/link-user", json={"user_id": world.id("super1")}, headers=world.auth("super1")).status_code == 403
    assert client.get(f"{W}?linked=yes", headers=admin).json()["total"] == 1
    assert client.delete(f"{W}/{mia['id']}/link-user", headers=admin).json()["linked_user"] is None

    # Deleting a worker unlinks the login but keeps it; restore brings the worker back.
    client.post(f"{W}/{mia['id']}/link-user", json={"user_id": world.id("worker1")}, headers=admin)
    assert client.delete(f"{W}/{mia['id']}", headers=admin).json() == {"ok": True}
    db.expire_all()
    assert db.get(User, world.id("worker1")).worker_id is None and db.get(User, world.id("worker1")).deleted_at is None
    assert client.get(f"{W}/{mia['id']}", headers=admin).status_code == 404
    assert client.post(f"{W}/{mia['id']}/restore", headers=admin).json()["status"] == "active"


def test_provision_login_creates_invited_user_and_holds_the_message(world, client, db):
    admin, hseq = world.auth("admin1"), world.auth("hseq1")
    mia = new_worker(client, admin)
    assert client.post(f"{W}/{mia['id']}/provision-user", json={}, headers=admin).status_code == 422  # no email
    client.patch(f"{W}/{mia['id']}", json={"email": "mia@one.example"}, headers=admin)
    # A non-admin cannot hand out the admin role.
    assert client.post(f"{W}/{mia['id']}/provision-user", json={"role_id": "admin"}, headers=hseq).status_code == 403
    made = client.post(f"{W}/{mia['id']}/provision-user", json={"role_id": "worker"}, headers=admin)
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["delivery"] == "held" and body["link"].startswith("https://portal.test/accept-invite?token=")
    user = db.get(User, body["user_id"])
    assert (user.email, user.status, user.role_id, user.worker_id, user.name) == ("mia@one.example", "invited", "worker", mia["id"], "Mia Tanaka")
    message = db.scalars(select(OutboxMessage).where(OutboxMessage.recipient == "mia@one.example")).one()
    assert message.status == "held"
    assert client.post(f"{W}/{mia['id']}/provision-user", json={}, headers=admin).status_code == 409
    # An email that already has a login is linked, not duplicated.
    ben = new_worker(client, admin, first_name="Ben", last_name="Abbott", email="worker1@one.example")
    clash = client.post(f"{W}/{ben['id']}/provision-user", json={}, headers=admin)
    assert clash.status_code == 409 and "Link that login" in clash.json()["detail"]


def test_photo_upload_checks_content_and_permission(world, client):
    from io import BytesIO

    from PIL import Image

    admin, supervisor = world.auth("admin1"), world.auth("super1")
    mia = new_worker(client, admin)
    buf = BytesIO()
    Image.new("RGB", (40, 40), (27, 75, 143)).save(buf, "PNG")
    assert client.post(f"{W}/{mia['id']}/photo", files={"file": ("me.png", b"%PDF-1.4 not an image", "image/png")}, headers=admin).status_code in (400, 415, 422)
    assert client.post(f"{W}/{mia['id']}/photo", files={"file": ("me.png", buf.getvalue(), "image/png")}, headers=supervisor).status_code == 403
    done = client.post(f"{W}/{mia['id']}/photo", files={"file": ("me.png", buf.getvalue(), "image/png")}, headers=admin).json()
    assert done["photo_url"].startswith(f"/api/workers/{mia['id']}/photo")
    shown = client.get(done["photo_url"], headers=supervisor)
    assert shown.status_code == 200 and shown.content == buf.getvalue()
    assert client.get(done["photo_url"], headers=world.auth("admin2")).status_code == 404
    assert client.delete(f"{W}/{mia['id']}/photo", headers=admin).json()["photo_url"] is None


# ---------- certifications ----------

def add_cert(client, headers, worker_id, **body):
    response = client.post(f"{W}/{worker_id}/certifications", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_kinds_are_seeded_normalised_and_extendable(world, client):
    admin = world.auth("admin1")
    kinds = client.get("/api/certification-kinds", headers=admin).json()["items"]
    assert len(kinds) == 18 and {"white_card", "first_aid", "traffic_control"} <= {k["slug"] for k in kinds}
    mia = new_worker(client, admin)
    # A known alias resolves to its slug.
    cert = add_cert(client, admin, mia["id"], kind="HLTAID011", expiry_date=iso(400))
    assert cert["kind"] == {"slug": "first_aid", "label": "First Aid"}
    assert client.post(f"{W}/{mia['id']}/certifications", json={"kind": "Unicycle licence"}, headers=admin).status_code == 422

    made = client.post("/api/certification-kinds", json={"label": "Chainsaw Operation", "aliases": ["Chainsaw", "AHCMOM213"]}, headers=admin)
    assert made.status_code == 201 and made.json()["slug"] == "chainsaw_operation" and made.json()["is_system"] is False
    assert client.post("/api/certification-kinds", json={"label": "chainsaw"}, headers=admin).status_code == 409
    assert add_cert(client, admin, mia["id"], kind="ahcmom213", no_expiry=True)["kind"]["slug"] == "chainsaw_operation"
    # Retiring a type hides it from the list and from new entries; old certificates keep their label.
    kind_id = made.json()["id"]
    assert client.patch(f"/api/certification-kinds/{kind_id}", json={"active": False}, headers=admin).json()["active"] is False
    assert "chainsaw_operation" not in {k["slug"] for k in client.get("/api/certification-kinds", headers=admin).json()["items"]}
    held = client.get(f"{W}/{mia['id']}/certifications", headers=admin).json()["items"]
    assert "Chainsaw Operation" in {c["kind"]["label"] for c in held}
    assert client.post("/api/certification-kinds", json={"label": "Nope"}, headers=world.auth("worker1")).status_code == 403
    # The other organisation has its own copy.
    assert len(client.get("/api/certification-kinds", headers=world.auth("admin2")).json()["items"]) == 18


def test_statuses_unknown_expiry_is_not_valid_and_totals_match_the_register(world, client):
    admin = world.auth("admin1")
    mia = new_worker(client, admin)
    ben = new_worker(client, admin, first_name="Ben", last_name="Abbott")
    add_cert(client, admin, mia["id"], kind="white_card", no_expiry=True)                    # valid
    add_cert(client, admin, mia["id"], kind="first_aid", expiry_date=iso(10))                # expiring
    add_cert(client, admin, mia["id"], kind="cpr", expiry_date=iso(-3))                      # expired
    add_cert(client, admin, mia["id"], kind="traffic_control")                               # unknown
    add_cert(client, admin, ben["id"], kind="first_aid", expiry_date=iso(200))               # valid
    add_cert(client, admin, ben["id"], kind="cpr", expiry_date=iso(30))                      # expiring (edge of window)
    add_cert(client, admin, ben["id"], kind="working_at_heights", expiry_date=iso(0))        # expiring today, not yet expired

    register = client.get(f"{W}/certifications/all", headers=admin).json()
    assert register["summary"] == {"valid": 2, "expiring": 3, "expired": 1, "unknown": 1} and register["total"] == 7
    assert [r["status"] for r in register["items"]][:2] == ["expired", "expiring"]  # most urgent first
    for state, n in register["summary"].items():
        assert client.get(f"{W}/certifications/all?status={state}", headers=admin).json()["total"] == n
    unknown = client.get(f"{W}/certifications/all?status=unknown", headers=admin).json()["items"][0]
    assert unknown["status_label"] == "Expiry not recorded" and unknown["days_left"] is None
    badge = client.get("/api/certifications/expiry-count", headers=admin).json()
    assert badge == {"expired": 1, "expiring_soon": 3, "unknown": 1, "window_days": 30}
    roster = {w["name"]: w["certifications"] for w in client.get(W, headers=admin).json()["items"]}
    assert roster["Mia Tanaka"] == {"valid": 1, "expiring": 1, "expired": 1, "unknown": 1, "missing": 0}

    # Filters narrow rows and totals together.
    cpr = client.get(f"{W}/certifications/all?kind=cpr", headers=admin).json()
    assert cpr["total"] == 2 and cpr["summary"] == {"valid": 0, "expiring": 1, "expired": 1, "unknown": 0}
    assert client.get(f"{W}/certifications/all?q=abbott", headers=admin).json()["total"] == 3
    assert client.get(f"{W}/certifications/all?q=first aid", headers=admin).json()["total"] == 2
    # An inactive worker leaves the default register and the badge together.
    client.patch(f"{W}/{mia['id']}", json={"active": False}, headers=admin)
    assert client.get(f"{W}/certifications/all", headers=admin).json()["total"] == 3
    assert client.get("/api/certifications/expiry-count", headers=admin).json()["expired"] == 0
    assert client.get(f"{W}/certifications/all?include_inactive_workers=true", headers=admin).json()["total"] == 7


def test_date_rules_and_duplicates(world, client):
    admin = world.auth("admin1")
    mia = new_worker(client, admin)
    bad = {"kind": "first_aid", "issue_date": iso(0), "expiry_date": iso(-1)}
    assert client.post(f"{W}/{mia['id']}/certifications", json=bad, headers=admin).status_code == 422
    both = {"kind": "first_aid", "expiry_date": iso(5), "no_expiry": True}
    assert client.post(f"{W}/{mia['id']}/certifications", json=both, headers=admin).status_code == 422
    add_cert(client, admin, mia["id"], kind="first_aid", expiry_date=iso(100))
    again = client.post(f"{W}/{mia['id']}/certifications", json={"kind": "First Aid"}, headers=admin)
    assert again.status_code == 409 and "Renew" in again.json()["detail"]
    assert client.post(f"{W}/{mia['id']}/certifications", json={"kind": "cpr"}, headers=world.auth("super1")).status_code == 403


def test_renewal_keeps_the_previous_certificate_and_its_evidence(world, client, db):
    admin = world.auth("admin1")
    mia = new_worker(client, admin)
    old = add_cert(client, admin, mia["id"], kind="first_aid", number="FA-1", expiry_date=iso(-5))
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    up = client.post(f"{W}/{mia['id']}/certifications/{old['id']}/upload", files={"file": ("old.pdf", pdf, "application/pdf")}, headers=admin)
    assert up.status_code == 200 and up.json()["evidence"]["name"] == "old.pdf"
    exe = client.post(f"{W}/{mia['id']}/certifications/{old['id']}/upload", files={"file": ("x.pdf", b"MZ\x90\x00binary", "application/pdf")}, headers=admin)
    assert exe.status_code in (400, 415, 422)

    new = client.post(f"{W}/certifications/{old['id']}/renew", json={"expiry_date": iso(700)}, headers=admin)
    assert new.status_code == 201, new.text
    new = new.json()
    assert new["status"] == "valid" and new["supersedes_id"] == old["id"] and new["number"] == "FA-1" and new["evidence"] is None
    assert client.post(f"{W}/certifications/{old['id']}/renew", json={"expiry_date": iso(700)}, headers=admin).status_code == 409

    current = client.get(f"{W}/{mia['id']}/certifications", headers=admin).json()["items"]
    assert [c["id"] for c in current] == [new["id"]]
    history = client.get(f"{W}/{mia['id']}/certifications?history=true", headers=admin).json()["items"]
    assert {c["id"] for c in history} == {old["id"], new["id"]}
    kept = next(c for c in history if c["id"] == old["id"])
    assert kept["current"] is False and kept["evidence"]["name"] == "old.pdf"
    got = client.get(kept["evidence_url"], headers=admin)
    assert got.status_code == 200 and got.content == pdf
    assert client.get(kept["evidence_url"], headers=world.auth("admin2")).status_code == 404
    # The register counts only the current one.
    assert client.get(f"{W}/certifications/all", headers=admin).json()["summary"]["expired"] == 0

    # Deleting a mistaken renewal makes the earlier certificate current again.
    assert client.delete(f"{W}/certifications/{old['id']}", headers=admin).status_code == 409
    assert client.delete(f"{W}/certifications/{new['id']}", headers=admin).json() == {"ok": True}
    assert [c["id"] for c in client.get(f"{W}/{mia['id']}/certifications", headers=admin).json()["items"]] == [old["id"]]
    assert db.get(Certification, new["id"]).deleted_at is not None


def test_update_archive_and_scope_of_certificates(world, client):
    admin, worker, hseq = world.auth("admin1"), world.auth("worker1"), world.auth("hseq1")
    mia = new_worker(client, admin)
    mine = world.add_worker("Tom", "Okafor", link="worker1")
    theirs = add_cert(client, admin, mia["id"], kind="first_aid", expiry_date=iso(-1))
    own = add_cert(client, admin, mine.id, kind="white_card", no_expiry=True)

    fixed = client.patch(f"{W}/certifications/{theirs['id']}", json={"expiry_date": iso(90), "number": " FA-9 "}, headers=hseq).json()
    assert fixed["status"] == "valid" and fixed["number"] == "FA-9"
    assert client.patch(f"{W}/certifications/{theirs['id']}", json={"no_expiry": True}, headers=hseq).status_code == 422
    assert client.delete(f"{W}/certifications/{theirs['id']}", headers=hseq).status_code == 403  # no delete right

    # A worker sees only their own certificates, wherever they look.
    assert [c["id"] for c in client.get(f"{W}/certifications/all", headers=worker).json()["items"]] == [own["id"]]
    assert client.get(f"{W}/{mia['id']}/certifications", headers=worker).status_code == 404
    assert client.patch(f"{W}/certifications/{own['id']}", json={"number": "x"}, headers=worker).status_code == 403
    assert client.get("/api/certifications/expiry-count", headers=worker).json()["expired"] == 0
    me = client.get(f"{W}/me", headers=worker).json()
    assert [c["kind"]["slug"] for c in me["certifications"]] == ["white_card"]

    assert client.post(f"{W}/certifications/{theirs['id']}/archive", headers=hseq).json()["ok"] is True
    assert client.get(f"{W}/certifications/all?worker_id={mia['id']}", headers=admin).json()["total"] == 0
    assert client.get(f"{W}/certifications/all?worker_id={mia['id']}&include_archived=true", headers=admin).json()["total"] == 1
    assert client.post(f"{W}/certifications/{theirs['id']}/restore", headers=hseq).json()["ok"] is True
    assert client.get(f"{W}/certifications/all?worker_id={mia['id']}", headers=admin).json()["total"] == 1


def test_expected_certificates_show_as_missing(world, client):
    admin = world.auth("admin1")
    mia = new_worker(client, admin)
    assert client.put("/api/certifications/settings", json={"expected_kinds": ["nope"]}, headers=admin).status_code == 422
    assert client.put("/api/certifications/settings", json={"expected_kinds": ["white_card"]}, headers=world.auth("hseq1")).status_code == 403
    saved = client.put("/api/certifications/settings", json={"expiry_window_days": 60, "expected_kinds": ["white_card", "first_aid"]}, headers=admin).json()
    assert saved == {"expiry_window_days": 60, "expected_kinds": ["white_card", "first_aid"], "gate_enabled": False}
    add_cert(client, admin, mia["id"], kind="white_card", expiry_date=iso(45))
    detail = client.get(f"{W}/{mia['id']}", headers=admin).json()
    assert detail["missing_certifications"] == [{"slug": "first_aid", "label": "First Aid"}]
    listing = client.get(W, headers=admin).json()["items"][0]["certifications"]
    assert listing["missing"] == 1 and listing["expiring"] == 1  # 45 days is inside the 60 day window


# ---------- the certification gate on forms ----------

def _gated_form(client, admin, required):
    template = client.post("/api/forms/templates", headers=admin, json={
        "name": "Traffic set-up", "fields": [{"id": "note", "type": "text", "label": "Note"}],
        "certification_gate_enabled": True, "required_certifications": required,
    })
    assert template.status_code == 201, template.text
    tid = template.json()["id"]
    assert client.post(f"/api/forms/templates/{tid}/publish", headers=admin).status_code == 200
    return tid


def test_gate_needs_both_switches_and_checks_the_callers_own_certificates(world, client):
    admin, worker = world.auth("admin1"), world.auth("worker1")
    assert client.post("/api/forms/templates", headers=admin, json={
        "name": "Bad", "fields": [], "required_certifications": ["unicycle"]}).status_code == 422
    tid = _gated_form(client, admin, ["Traffic Control", "first_aid"])  # a label is normalised to its slug
    assert client.get(f"/api/forms/templates/{tid}", headers=admin).json()["required_certifications"] == ["traffic_control", "first_aid"]
    start = lambda: client.post(f"/api/forms/templates/{tid}/submissions", json={"fields": [{"id": "note", "value": "hi"}]}, headers=worker)  # noqa: E731

    # Form switch on, organisation switch off: nobody is locked out.
    assert start().status_code == 201
    assert client.put("/api/certifications/settings", json={"gate_enabled": True}, headers=admin).status_code == 200

    # No worker record: identity cannot be checked.
    blocked = start()
    assert blocked.status_code == 403 and blocked.json()["detail"]["certification_gate"][0]["problem"] == "no_worker"

    mine = world.add_worker("Tom", "Okafor", link="worker1")
    someone = world.add_worker("Mia", "Tanaka")
    add_cert(client, admin, someone.id, kind="traffic_control", expiry_date=iso(300))  # another person's does not count
    add_cert(client, admin, someone.id, kind="first_aid", expiry_date=iso(300))
    assert [p["problem"] for p in start().json()["detail"]["certification_gate"]] == ["missing", "missing"]

    tc = add_cert(client, admin, mine.id, kind="traffic_control", expiry_date=iso(-1))
    fa = add_cert(client, admin, mine.id, kind="first_aid")
    problems = start().json()["detail"]["certification_gate"]
    assert [(p["slug"], p["problem"]) for p in problems] == [("traffic_control", "expired"), ("first_aid", "unknown")]
    check = client.get(f"/api/forms/templates/{tid}/access-check", headers=worker).json()
    assert check["allowed"] is False and check["reason"] == "certification_gate" and len(check["certification_gate"]) == 2
    # The office preview of what this person sees explains the same thing.
    preview = client.get(f"/api/forms/assignments/effective?user_id={world.id('worker1')}", headers=admin).json()
    row = next(f for f in preview["forms"] if f["template_id"] == tid)
    assert row["blocked_by"].startswith("Certificates needed: Traffic Control: certificate has expired")
    people = client.get("/api/forms/pickers/people?q=tom", headers=admin).json()["items"]
    assert [p["id"] for p in people] == [world.id("worker1")]  # logins of this organisation only
    assert client.get("/api/forms/pickers/people", headers=worker).status_code == 403

    client.post(f"{W}/certifications/{tc['id']}/renew", json={"expiry_date": iso(5)}, headers=admin)  # expiring still counts
    client.patch(f"{W}/certifications/{fa['id']}", json={"expiry_date": iso(200)}, headers=admin)
    assert client.get(f"/api/forms/templates/{tid}/access-check", headers=worker).json()["allowed"] is True
    draft = client.post(f"/api/forms/templates/{tid}/submissions", json={"fields": [{"id": "note", "value": "hi"}], "status": "draft"}, headers=worker)
    assert draft.status_code == 201

    # The check runs again when the draft is sent.
    client.patch(f"{W}/certifications/{fa['id']}", json={"expiry_date": iso(-2)}, headers=admin)
    late = client.post(f"/api/forms/submissions/{draft.json()['id']}/submit", headers=worker)
    assert late.status_code == 403 and late.json()["detail"]["certification_gate"][0]["slug"] == "first_aid"

    # Turning the form's own switch off opens it again.
    client.patch(f"/api/forms/templates/{tid}", json={"certification_gate_enabled": False}, headers=admin)
    assert client.post(f"/api/forms/submissions/{draft.json()['id']}/submit", headers=worker).status_code == 200
