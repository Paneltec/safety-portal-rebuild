"""Sites, QR sign-on and visitors (stage 4b)."""
from datetime import timedelta

from sqlalchemy import select

from app.models import AuditLog, Site, SiteAttendance, utcnow

S = "/api/sites"
V = "/api/admin/visitors"


def new_site(client, headers, **body):
    response = client.post(S, json={"name": "Depot", **body}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def token_of(site) -> str:
    return site["sign_on_url"].rsplit("/", 1)[1]


def visitor_in(client, token, **body):
    payload = {"name": "Vera Visitor", "induction_acknowledged": True, **body}
    return client.post(f"/api/public/visitor/site/{token}/signin", json=payload)


# ---------- register ----------

def test_site_crud_search_and_permissions(world, client):
    admin, hseq, auditor, worker = (world.auth(k) for k in ("admin1", "hseq1", "auditor1", "worker1"))
    depot = new_site(client, admin, address="12 Test Rd", suburb="Invermay", state="TAS", postcode="7248", kind="depot")
    assert depot["address_full"] == "12 Test Rd, Invermay, TAS, 7248" and depot["on_site"] == {"workers": 0, "visitors": 0}
    assert depot["sign_on_url"].startswith("https://portal.test/sign-on/") and len(token_of(depot)) >= 24
    job = new_site(client, hseq, name="Bridge Rd upgrade")
    assert client.post(S, json={"name": "x"}, headers=admin).status_code == 422
    assert client.post(S, json={"name": "Nope", "company_id": "missing"}, headers=admin).status_code == 404
    assert client.post(S, json={"name": "Nope"}, headers=auditor).status_code == 403
    assert client.get(S, headers=worker).status_code == 403 and client.get(S).status_code == 401

    listing = client.get(S, headers=auditor).json()
    assert [s["name"] for s in listing["items"]] == ["Bridge Rd upgrade", "Depot"] and listing["counts"] == {"active": 2, "inactive": 0}
    assert "sign_on_url" not in listing["items"][0]
    assert [s["id"] for s in client.get(f"{S}?q=inverm", headers=admin).json()["items"]] == [depot["id"]]
    # Someone who can view but not manage sites never sees the QR address.
    assert "sign_on_url" not in client.get(f"{S}/{depot['id']}", headers=auditor).json()
    assert client.get(f"{S}/{depot['id']}/qr.png", headers=auditor).status_code == 403

    changed = client.patch(f"{S}/{job['id']}", json={"active": False, "description": " Stage 2 "}, headers=hseq).json()
    assert changed["active"] is False and changed["description"] == "Stage 2"
    assert client.get(S, headers=admin).json()["counts"] == {"active": 1, "inactive": 1}
    assert client.delete(f"{S}/{job['id']}", headers=hseq).status_code == 403  # no delete right
    assert client.delete(f"{S}/{job['id']}", headers=admin).json()["ok"] is True
    assert client.get(f"{S}/{job['id']}", headers=admin).status_code == 404
    assert [s["id"] for s in client.get(f"{S}/recycle-bin", headers=admin).json()["items"]] == [job["id"]]
    assert client.post(f"{S}/{job['id']}/restore", headers=admin).json()["deleted_at"] is None


def test_tenant_isolation_for_sites_and_visitors(world, client):
    admin1, admin2 = world.auth("admin1"), world.auth("admin2")
    depot = new_site(client, admin1)
    visit = visitor_in(client, token_of(depot)).json()
    for call in (
        lambda: client.get(f"{S}/{depot['id']}", headers=admin2),
        lambda: client.patch(f"{S}/{depot['id']}", json={"name": "Mine now"}, headers=admin2),
        lambda: client.delete(f"{S}/{depot['id']}", headers=admin2),
        lambda: client.get(f"{S}/{depot['id']}/active-signons", headers=admin2),
        lambda: client.get(f"{S}/{depot['id']}/signon-log", headers=admin2),
        lambda: client.post(f"{S}/{depot['id']}/rotate-token", headers=admin2),
        lambda: client.get(f"{V}/{visit['visitor_id']}", headers=admin2),
        lambda: client.post(f"{V}/{visit['visitor_id']}/force-signout", headers=admin2),
        lambda: client.delete(f"{V}/{visit['visitor_id']}", headers=admin2),
    ):
        assert call().status_code == 404
    assert client.get(S, headers=admin2).json()["total"] == 0
    assert client.get(V, headers=admin2).json()["items"] == []
    assert client.post(f"{V}/bulk-delete", json={"ids": [visit["visitor_id"]]}, headers=admin2).json() == {
        "deleted": 0, "skipped": [{"id": visit["visitor_id"], "reason": "not_found"}]}


def test_sign_on_questions_are_validated(world, client):
    admin = world.auth("admin1")
    bad = client.post(S, json={"name": "Depot", "signon_questions": [{"type": "choice", "label": "Shift", "choices": ["Day"]}]}, headers=admin)
    assert bad.status_code == 422 and "two choices" in bad.json()["detail"]
    dup = [{"id": "a", "type": "yesno", "label": "Fit for work?"}, {"id": "a", "type": "text", "label": "Notes"}]
    assert client.post(S, json={"name": "Depot", "signon_questions": dup}, headers=admin).status_code == 422
    site = new_site(client, admin, signon_questions=[
        {"type": "yesno", "label": "Are you fit for work?"},
        {"id": "shift", "type": "choice", "label": "Shift", "choices": ["Day", "Night", "Day"]},
        {"type": "text", "label": "Vehicle rego", "required": False},
    ])
    questions = site["signon_questions"]
    assert questions[1] == {"id": "shift", "type": "choice", "label": "Shift", "required": True, "choices": ["Day", "Night"]}
    assert questions[0]["id"].startswith("q_") and questions[2]["required"] is False


def test_qr_png_and_printable_sign(world, client):
    from io import BytesIO

    from pypdf import PdfReader

    admin = world.auth("admin1")
    site = new_site(client, admin, name="Bridge Rd upgrade", address="Bridge Rd", suburb="Launceston")
    png = client.get(f"{S}/{site['id']}/qr.png", headers=admin)
    assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n" and png.headers["cache-control"] == "no-store"
    pdf = client.get(f"{S}/{site['id']}/qr-signage.pdf", headers=admin)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    reader = PdfReader(BytesIO(pdf.content))
    text = reader.pages[0].extract_text()
    assert len(reader.pages) == 1 and "SIGN IN HERE" in text and "Bridge Rd upgrade" in text and "Org One" in text
    assert site["sign_on_url"] in text


# ---------- public surface ----------

def test_public_site_header_reveals_only_what_a_visitor_needs(world, client):
    admin = world.auth("admin1")
    site = new_site(client, admin, address="12 Test Rd", description="Alarm code 4471", signon_questions=[{"type": "yesno", "label": "Fit for work?"}])
    seen = client.get(f"/api/public/site/{token_of(site)}/form").json()
    assert set(seen) == {"site_name", "address", "organisation", "timezone", "questions", "visitor_declaration"}
    assert seen["site_name"] == "Depot" and seen["organisation"] == "Org One" and "follow the site rules" in seen["visitor_declaration"]
    blob = str(seen)
    assert site["id"] not in blob and world.org1.id not in blob and "4471" not in blob
    assert client.get(f"/api/scan/site/{token_of(site)}").json() == seen
    for bad in ("nope", site["id"]):  # the site id is not a key
        assert client.get(f"/api/public/site/{bad}/form").status_code == 404
    # An inactive site's code stops working, and a rotated code retires the old sign.
    client.patch(f"{S}/{site['id']}", json={"active": False}, headers=admin)
    assert client.get(f"/api/public/site/{token_of(site)}/form").status_code == 404
    assert visitor_in(client, token_of(site)).status_code == 404
    client.patch(f"{S}/{site['id']}", json={"active": True}, headers=admin)
    rotated = client.post(f"{S}/{site['id']}/rotate-token", headers=admin).json()
    assert token_of(rotated) != token_of(site)
    assert client.get(f"/api/public/site/{token_of(site)}/form").status_code == 404
    assert client.get(f"/api/public/site/{token_of(rotated)}/form").status_code == 200


def test_visitor_sign_in_is_validated_and_idempotent(world, client, db):
    admin = world.auth("admin1")
    site = new_site(client, admin, signon_questions=[{"id": "fit", "type": "yesno", "label": "Fit for work?"},
                                                     {"id": "shift", "type": "choice", "label": "Shift", "choices": ["Day", "Night"], "required": False}])
    token = token_of(site)
    assert visitor_in(client, token, induction_acknowledged=False).status_code == 422
    assert visitor_in(client, token, name="V").status_code == 422
    missing = visitor_in(client, token)
    assert missing.status_code == 422 and missing.json()["detail"]["errors"][0]["message"] == "Fit for work?: an answer is needed"
    wrong = visitor_in(client, token, answers=[{"question_id": "fit", "value": "yes"}, {"question_id": "shift", "value": "Swing"}])
    assert [e["message"] for e in wrong.json()["detail"]["errors"]] == ["Fit for work?: answer yes or no", "Shift: choose one of the listed answers"]

    ok = {"answers": [{"question_id": "fit", "value": True}], "company": "Acme Pty Ltd", "phone": "0400 000 111",
          "purpose": "Delivery", "host": "Alex", "client_ref": "ref-12345678", "gps_lat": -41.43, "gps_long": 147.14}
    first = visitor_in(client, token, **ok)
    assert first.status_code == 201 and first.json()["already"] is False and first.json()["on_site"] is True
    # A retry with the same reference, or the same person again while still on site, is the same visit.
    retry = visitor_in(client, token, **ok)
    assert retry.status_code == 200 and retry.json() == {**first.json(), "already": True}
    again = visitor_in(client, token, name="vera  visitor", phone="0400 000 111", answers=ok["answers"])
    assert again.status_code == 200 and again.json()["visitor_id"] == first.json()["visitor_id"]
    assert db.scalar(select(SiteAttendance).where(SiteAttendance.kind == "visitor")).answers == [
        {"question_id": "fit", "label": "Fit for work?", "value": True}]
    assert client.get(f"{S}/{site['id']}", headers=admin).json()["on_site"] == {"workers": 0, "visitors": 1}

    # The visitor's page can refresh and sign out, only with this site's code.
    vid = first.json()["visitor_id"]
    other = new_site(client, admin, name="Other site")
    assert client.get(f"/api/public/visitor/{vid}?token={token_of(other)}").status_code == 404
    assert client.post(f"/api/public/visitor/{vid}/sign-out?token={token_of(other)}").status_code == 404
    assert client.post(f"/api/public/visitor/{vid}/sign-out").status_code == 422  # no code
    assert client.get(f"/api/public/visitor/{vid}?token={token}").json()["on_site"] is True
    out = client.post(f"/api/public/visitor/{vid}/sign-out?token={token}").json()
    assert out["already"] is False and out["on_site"] is False and out["signed_out_at"] >= out["signed_in_at"]
    twice = client.post(f"/api/public/visitor/{vid}/sign-out?token={token}").json()
    assert twice["already"] is True and twice["signed_out_at"] == out["signed_out_at"]
    assert client.get(f"{S}/{site['id']}", headers=admin).json()["on_site"] == {"workers": 0, "visitors": 0}
    # After leaving, the same person arriving again is a new visit.
    back = visitor_in(client, token, name="Vera Visitor", phone="0400 000 111", answers=ok["answers"])
    assert back.status_code == 201 and back.json()["visitor_id"] != vid


def test_visitor_sign_in_is_rate_limited_per_site(world, client):
    admin = world.auth("admin1")
    token = token_of(new_site(client, admin))
    for n in range(10):
        assert visitor_in(client, token, name=f"Visitor {n}").status_code == 201
    blocked = visitor_in(client, token, name="Visitor 11")
    assert blocked.status_code == 429 and "see site staff" in blocked.json()["detail"]
    # Another site is counted separately.
    assert visitor_in(client, token_of(new_site(client, admin, name="Other")), name="Visitor 11").status_code == 201


# ---------- signed-in sign-on ----------

def test_worker_sign_on_is_idempotent_and_uses_the_callers_identity(world, client):
    admin, worker = world.auth("admin1"), world.auth("worker1")
    world.add_worker("Tommy", "Okafor", link="worker1")
    site = new_site(client, admin, signon_questions=[{"id": "fit", "type": "yesno", "label": "Fit for work?"}])
    token = token_of(site)
    assert client.post(f"/api/scan/site/{token}/sign-on", json={}).status_code == 401
    assert client.post(f"/api/scan/site/{token}/sign-on", json={}, headers=worker).status_code == 422  # question unanswered
    body = {"answers": [{"question_id": "fit", "value": True}], "gps_lat": -41.4, "gps_long": 147.1, "gps_accuracy_m": 12}
    first = client.post(f"/api/scan/site/{token}/sign-on", json=body, headers=worker)
    assert first.status_code == 201 and first.json()["already"] is False
    assert first.json()["name"] == "Tommy Okafor" and first.json()["user_id"] == world.id("worker1")  # the worker record's name
    again = client.post(f"/api/scan/site/{token}/sign-on", json=body, headers=worker)
    assert again.status_code == 200 and again.json()["already"] is True and again.json()["id"] == first.json()["id"]

    me = client.get(f"/api/scan/site/{token}/me", headers=worker).json()
    assert me["signed_on"]["id"] == first.json()["id"] and me["site_id"] == site["id"]
    assert [a["site_name"] for a in client.get(f"{S}/my-attendance", headers=worker).json()["items"]] == ["Depot"]
    on_site = client.get(f"{S}/{site['id']}/active-signons", headers=admin).json()["items"]
    assert [(r["kind"], r["name"], r["gps"]["accuracy_m"]) for r in on_site] == [("worker", "Tommy Okafor", 12)]

    off = client.post(f"/api/scan/site/{token}/sign-off", headers=worker).json()
    assert off["already"] is False and off["on_site"] is False and off["sign_out_source"] == "self"
    assert client.post(f"/api/scan/site/{token}/sign-off", headers=worker).json() == {"ok": True, "already": True, "on_site": False}
    assert client.get(f"{S}/my-attendance", headers=worker).json()["items"] == []
    # Signing on again later opens a second visit; the first stays in the log.
    assert client.post(f"/api/scan/site/{token}/sign-on", json=body, headers=worker).status_code == 201
    log = client.get(f"{S}/{site['id']}/signon-log", headers=admin).json()
    assert len(log["items"]) == 2 and [r["on_site"] for r in log["items"]] == [True, False] and log["truncated"] is False

    # A login from another organisation cannot sign on as staff, or read the staff view.
    stranger = world.auth("worker2")
    assert client.post(f"/api/scan/site/{token}/sign-on", json=body, headers=stranger).status_code == 403
    assert client.get(f"/api/scan/site/{token}/me", headers=stranger).status_code == 403
    assert client.get(f"{S}/my-attendance", headers=stranger).json()["items"] == []


def test_office_sign_off_keeps_history_and_delete_closes_open_visits(world, client, db):
    admin, hseq, auditor, worker = (world.auth(k) for k in ("admin1", "hseq1", "auditor1", "worker1"))
    site = new_site(client, admin)
    token = token_of(site)
    client.post(f"/api/scan/site/{token}/sign-on", json={}, headers=worker)
    visit = visitor_in(client, token).json()
    rows = client.get(f"{S}/{site['id']}/active-signons", headers=auditor).json()["items"]
    assert {r["kind"] for r in rows} == {"worker", "visitor"}
    target = next(r for r in rows if r["kind"] == "worker")
    assert client.delete(f"{S}/{site['id']}/active-signons/{target['id']}", headers=auditor).status_code == 403
    done = client.delete(f"{S}/{site['id']}/active-signons/{target['id']}", headers=hseq).json()
    assert done["already"] is False and done["sign_out_source"] == "office" and done["on_site"] is False
    assert client.delete(f"{S}/{site['id']}/active-signons/{target['id']}", headers=hseq).json()["already"] is True
    assert len(client.get(f"{S}/{site['id']}/signon-log", headers=admin).json()["items"]) == 2  # nothing was erased
    assert db.scalars(select(AuditLog).where(AuditLog.action == "site.attendance_signed_out")).one().actor_user_id == world.id("hseq1")

    # Deleting the site signs the remaining visitor out rather than leaving them "on site" forever.
    assert client.delete(f"{S}/{site['id']}", headers=admin).json() == {"ok": True, "signed_out": 1}
    db.expire_all()
    assert db.get(SiteAttendance, visit["visitor_id"]).signed_out_at is not None
    assert visitor_in(client, token).status_code == 404


def test_stale_open_visits_are_flagged(world, client, db):
    admin = world.auth("admin1")
    site = new_site(client, admin)
    visit = visitor_in(client, token_of(site)).json()
    row = db.get(SiteAttendance, visit["visitor_id"])
    row.signed_in_at = utcnow() - timedelta(hours=20)
    db.commit()
    shown = client.get(f"{S}/{site['id']}/active-signons", headers=admin).json()["items"][0]
    assert shown["stale"] is True and shown["minutes"] >= 20 * 60


# ---------- visitor register ----------

def test_visitor_register_filters_counts_corrections_and_export(world, client, db):
    admin, auditor, hseq = world.auth("admin1"), world.auth("auditor1"), world.auth("hseq1")
    depot, job = new_site(client, admin), new_site(client, admin, name="Bridge Rd")
    a = visitor_in(client, token_of(depot), name="Ann Lee", company="=Acme", host="Alex").json()
    b = visitor_in(client, token_of(job), name="Bob Ray").json()
    client.post(f"/api/public/visitor/{b['visitor_id']}/sign-out?token={token_of(job)}")
    old = visitor_in(client, token_of(job), name="Old Visit").json()
    row = db.get(SiteAttendance, old["visitor_id"])
    row.signed_in_at = utcnow() - timedelta(days=40)
    row.signed_out_at = row.signed_in_at + timedelta(hours=1)
    db.commit()

    assert client.get(V, headers=hseq).status_code == 403  # no visitor permission in the catalogue
    everything = client.get(V, headers=auditor).json()
    assert [v["name"] for v in everything["items"]] == ["Bob Ray", "Ann Lee", "Old Visit"] and everything["truncated"] is False
    assert client.get(f"{V}/category-counts", headers=auditor).json() == {"on_site": 1, "today": 2, "total": 3}
    assert [v["name"] for v in client.get(f"{V}?on_site=true", headers=admin).json()["items"]] == ["Ann Lee"]
    assert [v["name"] for v in client.get(f"{V}?site_id={job['id']}&on_site=false", headers=admin).json()["items"]] == ["Bob Ray", "Old Visit"]
    assert [v["name"] for v in client.get(f"{V}?q=acme", headers=admin).json()["items"]] == ["Ann Lee"]
    today = utcnow().date().isoformat()
    recent = client.get(f"{V}?date_from={(utcnow() - timedelta(days=2)).date().isoformat()}", headers=admin).json()["items"]
    assert "Old Visit" not in [v["name"] for v in recent]
    assert client.get(f"{V}?date_from={today}&date_to=2000-01-01", headers=admin).status_code == 422

    # Corrections need a reason, cannot create impossible times, and keep the earlier values.
    vid = a["visitor_id"]
    assert client.patch(f"{V}/{vid}", json={"name": "Anne Lee"}, headers=admin).status_code == 422  # no reason
    assert client.patch(f"{V}/{vid}", json={"name": "Anne Lee", "reason": "x"}, headers=auditor).status_code == 403
    early = (utcnow() - timedelta(days=1)).isoformat()
    assert client.patch(f"{V}/{vid}", json={"signed_out_at": early, "reason": "left early"}, headers=admin).status_code == 422
    late = (utcnow() + timedelta(hours=2)).isoformat()
    assert client.patch(f"{V}/{vid}", json={"signed_out_at": late, "reason": "typo"}, headers=admin).status_code == 422
    fixed = client.patch(f"{V}/{vid}", json={"name": "Anne Lee", "reason": "Spelling"}, headers=admin).json()
    assert fixed["name"] == "Anne Lee"
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "visitor.corrected")).one()
    assert entry.detail == {"reason": "Spelling", "before": {"name": "Ann Lee"}}

    out = client.post(f"{V}/{vid}/force-signout", headers=admin).json()
    assert out["already"] is False and out["sign_out_source"] == "office"
    assert client.post(f"{V}/{vid}/force-signout", headers=admin).json()["already"] is True
    assert client.get(f"{V}/category-counts", headers=admin).json()["on_site"] == 0

    export = client.get(f"{V}/export.csv", headers=auditor)
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    lines = export.text.lstrip("﻿").splitlines()
    assert lines[0].startswith("Site,Type,Name,Company") and len(lines) == 4
    assert "'=Acme" in export.text  # a formula-looking cell is neutralised
    log = client.post(f"{S}/{job['id']}/signon-log/export", headers=auditor)
    assert log.status_code == 200 and len(log.text.splitlines()) == 3


def test_visitor_archive_delete_and_bulk_delete(world, client):
    admin, auditor = world.auth("admin1"), world.auth("auditor1")
    token = token_of(new_site(client, admin))
    ids = [visitor_in(client, token, name=f"Visitor {n}").json()["visitor_id"] for n in range(3)]
    assert client.post(f"{V}/{ids[0]}/archive", headers=admin).status_code == 409  # still on site
    client.post(f"{V}/{ids[0]}/force-signout", headers=admin)
    assert client.post(f"{V}/{ids[0]}/archive", headers=auditor).status_code == 403
    assert client.post(f"{V}/{ids[0]}/archive", headers=admin).json()["archived_at"] is not None
    assert len(client.get(V, headers=admin).json()["items"]) == 2
    assert len(client.get(f"{V}?include_archived=true", headers=admin).json()["items"]) == 3
    assert client.post(f"{V}/{ids[0]}/unarchive", headers=admin).json()["archived_at"] is None

    assert client.delete(f"{V}/{ids[1]}", headers=admin).json() == {"ok": True, "already": False}
    assert client.delete(f"{V}/{ids[1]}", headers=admin).json() == {"ok": True, "already": True}
    assert client.get(f"{V}/{ids[1]}", headers=admin).status_code == 404
    result = client.post(f"{V}/bulk-delete", json={"ids": [ids[0], ids[1], ids[2], "missing"]}, headers=admin).json()
    assert result == {"deleted": 2, "skipped": [{"id": ids[1], "reason": "already_deleted"}, {"id": "missing", "reason": "not_found"}]}
    assert client.get(V, headers=admin).json()["items"] == []
    assert client.get(f"{V}/category-counts", headers=admin).json() == {"on_site": 0, "today": 0, "total": 0}
    assert len(client.get(f"{V}?include_deleted=true", headers=admin).json()["items"]) == 3


def test_site_picker_lists_active_sites_of_this_organisation(world, client, db):
    admin, worker = world.auth("admin1"), world.auth("worker1")
    depot = new_site(client, admin, address="12 Test Rd", suburb="Invermay")
    closed = new_site(client, admin, name="Closed job", active=False)
    db.add(Site(org_id=world.org2.id, name="Depot", scan_token="other-org-token-000000000"))
    db.commit()
    picked = client.get("/api/forms/pickers/sites", headers=worker).json()
    assert picked == {"available": True, "source": "sites", "items": [{"id": depot["id"], "label": "Depot", "address": "12 Test Rd, Invermay"}]}
    assert closed["id"] not in str(picked)
