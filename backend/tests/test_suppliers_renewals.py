"""Suppliers, contractors, compliance documents and renewal links (stage 4c)."""
from datetime import date, timedelta

from sqlalchemy import select

from app.models import AuditLog, Certification, OutboxMessage, RenewalLink, Supplier, SupplierDocument, User, utcnow

SUP = "/api/suppliers"
REN = "/api/renewals"
PUB = "/api/public/renewals"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
TODAY = date.today()


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def new_supplier(client, headers, **body):
    response = client.post(SUP, json={"name": "Acme Civil", **body}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def add_doc(client, headers, supplier_id, doc_type="public_liability", content=PDF, **form):
    return client.post(f"{SUP}/{supplier_id}/documents", data={"doc_type": doc_type, **form},
                       files={"file": ("policy.pdf", content, "application/pdf")}, headers=headers)


def token_of(payload) -> str:
    return payload["link"].rsplit("/", 1)[1]


def public_upload(client, token, doc_type, content=PDF, **form):
    return client.post(f"{PUB}/{token}/upload", data={"doc_type": doc_type, **form}, files={"file": ("doc.pdf", content, "application/pdf")})


# ---------- directory ----------

def test_supplier_crud_validation_and_duplicates(world, client):
    admin, auditor, worker = world.auth("admin1"), world.auth("auditor1"), world.auth("worker1")
    acme = new_supplier(client, admin, abn="51 824 753 556", contact_email="Office@Acme.Example", trade="Earthworks", is_contractor=True)
    assert acme["abn"] == "51824753556" and acme["contact_email"] == "office@acme.example" and acme["status"] == "active"
    assert [m["slug"] for m in acme["compliance"]["missing"]] == ["public_liability", "workers_compensation"]
    assert client.post(SUP, json={"name": "Bad", "abn": "51 824 753 557"}, headers=admin).status_code == 422  # checksum
    assert client.post(SUP, json={"name": "Bad", "abn": "123"}, headers=admin).status_code == 422
    assert client.post(SUP, json={"name": "B"}, headers=admin).status_code == 422
    assert client.post(SUP, json={"name": "Nope"}, headers=auditor).status_code == 403
    assert client.get(SUP, headers=worker).status_code == 403 and client.get(SUP).status_code == 401
    twin = new_supplier(client, admin, name="ACME CIVIL")
    assert [d["id"] for d in twin["possible_duplicates"]] == [acme["id"]]
    assert twin["compliance"]["missing"] == []  # a plain supplier is not expected to hold contractor documents

    listing = client.get(SUP, headers=auditor).json()
    assert listing["total"] == 2 and listing["counts"] == {"active": 2, "inactive": 0, "archived": 0}
    assert [s["id"] for s in client.get(f"{SUP}?kind=contractor", headers=admin).json()["items"]] == [acme["id"]]
    assert [s["id"] for s in client.get(f"{SUP}?q=earthw", headers=admin).json()["items"]] == [acme["id"]]
    assert [s["id"] for s in client.get("/api/contractors", headers=admin).json()["items"]] == [acme["id"]]
    assert client.get(f"/api/contractors/{twin['id']}", headers=admin).status_code == 404

    assert client.post(f"{SUP}/{twin['id']}/archive", headers=admin).json()["status"] == "archived"
    assert client.get(SUP, headers=admin).json()["counts"] == {"active": 1, "inactive": 0, "archived": 1}
    assert client.post(f"{SUP}/{twin['id']}/unarchive", headers=admin).json()["status"] == "active"
    assert client.delete(f"{SUP}/{twin['id']}", headers=world.auth("hseq1")).status_code == 403
    assert client.delete(f"{SUP}/{twin['id']}", headers=admin).json() == {"ok": True}
    assert client.get(f"{SUP}/{twin['id']}", headers=admin).status_code == 404
    assert client.post(f"{SUP}/{twin['id']}/restore", headers=admin).json()["status"] == "active"


def test_local_edits_to_an_imported_supplier_are_remembered(world, client, db):
    admin = world.auth("admin1")
    row = Supplier(org_id=world.org1.id, name="Imported Pty Ltd", contact_phone="0400 000 000", source_system="simpro",
                   source_company="paneltec", source_id="V-17", source_data={"name": "Imported Pty Ltd"})
    db.add(row)
    db.commit()
    changed = client.patch(f"{SUP}/{row.id}", json={"contact_phone": "0411 111 111", "trade": "Fencing"}, headers=admin).json()
    assert changed["locally_edited"] == ["contact_phone", "trade"] and changed["source"]["id"] == "V-17"
    # A hand-added supplier has nothing to protect from an import.
    local = new_supplier(client, admin)
    assert client.patch(f"{SUP}/{local['id']}", json={"trade": "Plumbing"}, headers=admin).json()["locally_edited"] == []


def test_tenant_isolation_and_contractor_representative_scope(world, client, db):
    admin1, admin2 = world.auth("admin1"), world.auth("admin2")
    acme = new_supplier(client, admin1, is_contractor=True)
    other = new_supplier(client, admin1, name="Other Contractor", is_contractor=True)
    plain = new_supplier(client, admin1, name="Paper Supplies")
    doc = add_doc(client, admin1, acme["id"], expiry_date=iso(100)).json()
    note = client.post(f"{SUP}/{acme['id']}/notes", json={"body": "Reviewed"}, headers=admin1).json()
    for call in (
        lambda: client.get(f"{SUP}/{acme['id']}", headers=admin2),
        lambda: client.patch(f"{SUP}/{acme['id']}", json={"trade": "x"}, headers=admin2),
        lambda: client.get(f"{SUP}/{acme['id']}/documents", headers=admin2),
        lambda: client.get(doc["file_url"], headers=admin2),
        lambda: client.patch(f"{SUP}/notes/{note['id']}", json={"body": "x"}, headers=admin2),
        lambda: add_doc(client, admin2, acme["id"]),
        lambda: client.post(REN, json={"entity_id": acme["id"], "doc_types_requested": ["public_liability"]}, headers=admin2),
    ):
        assert call().status_code == 404
    assert client.get(SUP, headers=admin2).json()["total"] == 0

    # An external representative: contractor role, login tied to one supplier.
    rep = User(org_id=world.org1.id, email="rep@acme.example", name="Rita Rep", role_id="contractor_rep", status="active",
               password_hash=world.users["admin1"].password_hash)
    db.add(rep)
    db.commit()
    world.users["rep"] = rep
    rep_auth = world.auth("rep")
    assert client.get(SUP, headers=rep_auth).json()["items"] == []  # not linked yet: sees nothing
    assert client.post(f"{SUP}/{acme['id']}/link-user", json={"user_id": world.id("worker2")}, headers=admin1).status_code == 404
    linked = client.post(f"{SUP}/{acme['id']}/link-user", json={"user_id": rep.id}, headers=admin1).json()
    assert [l["id"] for l in linked["logins"]] == [rep.id]
    assert client.post(f"{SUP}/{other['id']}/link-user", json={"user_id": rep.id}, headers=admin1).status_code == 409
    assert [s["id"] for s in client.get(SUP, headers=rep_auth).json()["items"]] == [acme["id"]]
    assert client.get(f"{SUP}/{other['id']}", headers=rep_auth).status_code == 404
    assert client.get(f"{SUP}/{plain['id']}", headers=rep_auth).status_code == 404
    assert client.get(f"{SUP}/{other['id']}/documents", headers=rep_auth).status_code == 404
    # They can keep their own company's details and documents up to date, and nothing more.
    assert client.patch(f"{SUP}/{acme['id']}", json={"contact_phone": "0400 123 123"}, headers=rep_auth).status_code == 200
    assert client.patch(f"{SUP}/{acme['id']}", json={"is_contractor": False}, headers=rep_auth).status_code == 403
    assert add_doc(client, rep_auth, acme["id"], doc_type="workers_compensation", expiry_date=iso(50)).status_code == 201
    assert client.delete(f"{SUP}/{acme['id']}", headers=rep_auth).status_code == 403
    assert client.get(REN, headers=rep_auth).status_code == 403
    assert "logins" not in client.get(f"{SUP}/{acme['id']}", headers=rep_auth).json()
    assert set(client.get(f"{SUP}/panel-counts", headers=rep_auth).json()["counts"]) == {acme["id"]}
    client.delete(f"{SUP}/{acme['id']}/link-user/{rep.id}", headers=admin1)
    assert client.get(SUP, headers=rep_auth).json()["items"] == []


def test_compliance_documents_supersede_and_keep_history(world, client):
    admin = world.auth("admin1")
    acme = new_supplier(client, admin, is_contractor=True)
    assert add_doc(client, admin, acme["id"], doc_type="nope").status_code == 422
    assert add_doc(client, admin, acme["id"], content=b"MZ\x90\x00 exe").status_code == 415
    assert add_doc(client, admin, acme["id"], issue_date=iso(0), expiry_date=iso(-1)).status_code == 422
    old = add_doc(client, admin, acme["id"], reference="PL-1", expiry_date=iso(-3)).json()
    assert old["status"] == "expired" and old["doc_type"]["label"] == "Public liability insurance"
    summary = client.get(f"{SUP}/{acme['id']}", headers=admin).json()["compliance"]
    assert summary["expired"] == 1 and [m["slug"] for m in summary["missing"]] == ["workers_compensation"]
    new = add_doc(client, admin, acme["id"], reference="PL-2", expiry_date=iso(300)).json()
    add_doc(client, admin, acme["id"], doc_type="workers_compensation")
    docs = client.get(f"{SUP}/{acme['id']}/documents", headers=admin).json()
    assert [(d["doc_type"]["slug"], d["status"]) for d in docs["items"]] == [("public_liability", "valid"), ("workers_compensation", "unknown")]
    assert docs["missing"] == []
    history = client.get(f"{SUP}/{acme['id']}/documents?history=true", headers=admin).json()["items"]
    assert {d["id"] for d in history} >= {old["id"], new["id"]} and next(d for d in history if d["id"] == old["id"])["current"] is False
    assert client.get(old["file_url"], headers=admin).content == PDF
    # Deleting the newer one brings the earlier one back; the earlier one cannot be deleted from under it.
    assert client.delete(f"{SUP}/{acme['id']}/documents/{old['id']}", headers=admin).status_code == 409
    assert client.delete(f"{SUP}/{acme['id']}/documents/{new['id']}", headers=admin).json() == {"ok": True}
    assert client.get(f"{SUP}/{acme['id']}", headers=admin).json()["compliance"]["expired"] == 1
    assert add_doc(client, world.auth("auditor1"), acme["id"]).status_code == 403


def test_notes_tasks_and_members(world, client):
    admin, auditor = world.auth("admin1"), world.auth("auditor1")
    acme = new_supplier(client, admin)
    note = client.post(f"{SUP}/{acme['id']}/notes", json={"body": " Met on site "}, headers=admin).json()
    assert note["body"] == "Met on site" and note["author"] == "Alex Rowe"
    assert client.post(f"{SUP}/{acme['id']}/notes", json={"body": "x"}, headers=auditor).status_code == 403
    assert client.patch(f"{SUP}/notes/{note['id']}", json={"body": "Met at the depot"}, headers=admin).json()["body"] == "Met at the depot"

    task = client.post(f"{SUP}/{acme['id']}/tasks", json={"title": "Chase insurance", "due_date": iso(-2), "priority": "high",
                                                           "assigned_to": world.id("hseq1")}, headers=admin).json()
    assert task["overdue"] is True and task["assigned_name"] == "Sam Keating"
    assert client.post(f"{SUP}/{acme['id']}/tasks", json={"title": "x", "assigned_to": world.id("worker2")}, headers=admin).status_code in (404, 422)
    assert client.post(f"{SUP}/{acme['id']}/tasks", json={"title": "Bad", "status": "finished"}, headers=admin).status_code == 422
    done = client.patch(f"{SUP}/tasks/{task['id']}", json={"status": "done"}, headers=admin).json()
    assert done["overdue"] is False and done["completed_at"] is not None
    assert client.patch(f"{SUP}/tasks/{task['id']}", json={"status": "open"}, headers=admin).json()["completed_at"] is None

    first = client.post(f"{SUP}/{acme['id']}/members", json={"name": "Pat Lee", "is_primary_contact": True}, headers=admin).json()
    second = client.post(f"{SUP}/{acme['id']}/members", json={"name": "Kim Roy", "email": "Kim@Acme.Example", "is_primary_contact": True}, headers=admin).json()
    members = client.get(f"{SUP}/{acme['id']}/members", headers=auditor).json()["items"]
    assert [(m["name"], m["is_primary_contact"]) for m in members] == [("Kim Roy", True), ("Pat Lee", False)]  # one primary contact
    assert second["email"] == "kim@acme.example"
    assert client.get(f"{SUP}/panel-counts", headers=admin).json()["counts"][acme["id"]] == {"open_tasks": 1, "notes": 1, "members": 2}
    assert client.delete(f"{SUP}/members/{first['id']}", headers=admin).json() == {"ok": True}
    assert client.delete(f"{SUP}/notes/{note['id']}", headers=admin).json() == {"ok": True}
    assert client.delete(f"{SUP}/tasks/{task['id']}", headers=admin).json() == {"ok": True}
    assert acme["id"] not in client.get(f"{SUP}/panel-counts", headers=admin).json()["counts"] or \
        client.get(f"{SUP}/panel-counts", headers=admin).json()["counts"][acme["id"]] == {"open_tasks": 0, "notes": 0, "members": 1}


def test_document_types_are_seeded_and_configurable(world, client):
    admin = world.auth("admin1")
    types = client.get(f"{REN}/doc-types", headers=admin).json()["items"]
    assert [t["slug"] for t in types][:2] == ["public_liability", "workers_compensation"] and len(types) == 7
    made = client.post(f"{REN}/doc-types", json={"label": "Asbestos licence", "required_for_contractors": True}, headers=admin)
    assert made.status_code == 201 and made.json()["slug"] == "asbestos_licence"
    assert client.post(f"{REN}/doc-types", json={"label": "asbestos licence"}, headers=admin).status_code == 409
    assert client.post(f"{REN}/doc-types", json={"label": "Nope"}, headers=world.auth("auditor1")).status_code == 403
    acme = new_supplier(client, admin, is_contractor=True)
    assert "asbestos_licence" in [m["slug"] for m in acme["compliance"]["missing"]]
    retired = client.delete(f"{REN}/doc-types/{made.json()['id']}", headers=admin).json()
    assert retired["active"] is False
    assert "asbestos_licence" not in [t["slug"] for t in client.get(f"{REN}/doc-types", headers=admin).json()["items"]]
    assert "asbestos_licence" not in [m["slug"] for m in client.get(f"{SUP}/{acme['id']}", headers=admin).json()["compliance"]["missing"]]
    assert client.get(f"{REN}/doc-types", headers=world.auth("worker1")).status_code == 403


# ---------- renewal links ----------

def test_renewal_link_full_cycle_for_a_supplier(world, client, db):
    admin = world.auth("admin1")
    acme = new_supplier(client, admin, is_contractor=True, contact_email="office@acme.example")
    previous = add_doc(client, admin, acme["id"], reference="PL-OLD", expiry_date=iso(-5)).json()
    assert client.post(REN, json={"entity_id": acme["id"], "doc_types_requested": ["nope"]}, headers=admin).status_code == 422
    assert client.post(REN, json={"entity_id": acme["id"], "doc_types_requested": []}, headers=admin).status_code == 422
    assert client.post(REN, json={"entity_id": acme["id"], "doc_types_requested": ["public_liability"]}, headers=world.auth("auditor1")).status_code == 403
    made = client.post(REN, json={"entity_id": acme["id"], "doc_types_requested": ["public_liability", "workers_compensation"],
                                  "message": "Please send current certificates."}, headers=admin)
    assert made.status_code == 201, made.text
    request = made.json()
    token = token_of(request)
    assert request["link"].startswith("https://portal.test/renew/") and request["delivery"] == "held" and request["status"] == "pending"
    # Only a hash is kept, and the message is held, not sent.
    link = db.get(RenewalLink, request["id"])
    assert token not in link.token_hash and len(link.token_hash) == 64
    message = db.get(OutboxMessage, link.outbox_message_id)
    assert (message.status, message.recipient, message.purpose) == ("held", "office@acme.example", "renewal_request")
    assert token not in str(client.get(f"{REN}/{request['id']}", headers=admin).json())  # never shown again

    # The recipient's page shows this request and nothing else.
    page = client.get(f"{PUB}/{token}").json()
    assert set(page) == {"organisation", "timezone", "for_name", "kind", "message", "status", "expires_at", "submitted_at", "documents", "can_upload"}
    assert page["for_name"] == "Acme Civil" and page["organisation"] == "Org One" and page["can_upload"] is True
    assert [d["slug"] for d in page["documents"]] == ["public_liability", "workers_compensation"]
    blob = str(page)
    assert acme["id"] not in blob and world.org1.id not in blob and "PL-OLD" not in blob
    assert client.get(f"{PUB}/not-a-token").status_code == 404

    # Uploads are checked; only what was asked for is accepted.
    assert public_upload(client, token, "motor_vehicle").status_code == 422
    assert public_upload(client, token, "public_liability", content=b"MZ\x90\x00 exe").status_code == 415
    assert public_upload(client, token, "public_liability", issue_date=iso(0), expiry_date=iso(-1)).status_code == 422
    assert client.post(f"{PUB}/{token}/submit").status_code == 422  # nothing uploaded
    assert public_upload(client, token, "public_liability", reference="PL-1", expiry_date=iso(200)).status_code == 200
    short = client.post(f"{PUB}/{token}/submit")
    assert short.status_code == 422 and short.json()["detail"] == "Still needed: Workers compensation insurance"
    replaced = public_upload(client, token, "public_liability", reference="PL-2", expiry_date=iso(365)).json()
    assert replaced["documents"][0]["uploaded"]["reference"] == "PL-2"
    public_upload(client, token, "workers_compensation", reference="WC-9", expiry_date=iso(365))
    sent = client.post(f"{PUB}/{token}/submit").json()
    assert sent["status"] == "submitted" and sent["can_upload"] is False
    assert client.post(f"{PUB}/{token}/submit").json()["status"] == "submitted"  # repeat is harmless
    assert public_upload(client, token, "public_liability").status_code == 409  # closed
    # Nothing counts until the office accepts it.
    assert client.get(f"{SUP}/{acme['id']}", headers=admin).json()["compliance"]["expired"] == 1

    detail = client.get(f"{REN}/{request['id']}", headers=admin).json()
    assert detail["status_label"] == "Ready to review" and [u["reference"] for u in detail["uploads"]] == ["PL-2", "WC-9"]
    assert client.get(detail["uploads"][0]["file_url"], headers=admin).content == PDF
    assert client.get(detail["uploads"][0]["file_url"], headers=world.auth("admin2")).status_code == 404
    pl, wc = detail["uploads"]
    one = client.post(f"{REN}/{request['id']}/review", json={"decisions": [{"upload_id": pl["id"], "decision": "accept"}]}, headers=admin)
    assert one.status_code == 422 and "every uploaded file" in one.json()["detail"]
    no_reason = [{"upload_id": pl["id"], "decision": "accept"}, {"upload_id": wc["id"], "decision": "reject"}]
    assert client.post(f"{REN}/{request['id']}/review", json={"decisions": no_reason}, headers=admin).status_code == 422
    decided = client.post(f"{REN}/{request['id']}/review", headers=admin, json={"decisions": [
        {"upload_id": pl["id"], "decision": "accept", "expiry_date": iso(360)},  # the office corrects the date
        {"upload_id": wc["id"], "decision": "reject", "note": "Wrong policy holder"},
    ]}).json()
    assert decided["status"] == "partly_accepted"
    docs = client.get(f"{SUP}/{acme['id']}/documents?history=true", headers=admin).json()["items"]
    current = next(d for d in docs if d["current"] and d["doc_type"]["slug"] == "public_liability")
    assert (current["reference"], current["expiry_date"], current["source"], current["status"]) == ("PL-2", iso(360), "renewal", "valid")
    assert next(d for d in docs if d["id"] == previous["id"])["current"] is False  # the earlier certificate is kept
    assert db.scalars(select(AuditLog).where(AuditLog.action == "renewal.accepted_documents")).one().target_id == acme["id"]
    assert client.post(f"{REN}/{request['id']}/review", json={"decisions": no_reason}, headers=admin).status_code == 409

    # A second round for the rejected file: fresh link, old link dead, reason shown.
    again = client.post(f"{REN}/{request['id']}/reissue", json={"expires_in_days": 7}, headers=admin).json()
    token2 = token_of(again)
    assert token2 != token and client.get(f"{PUB}/{token}").status_code == 404
    page2 = client.get(f"{PUB}/{token2}").json()
    assert page2["documents"][0]["already_accepted"] is True and page2["documents"][1]["rejected_note"] == "Wrong policy holder"
    assert public_upload(client, token2, "public_liability").status_code == 409  # already accepted
    public_upload(client, token2, "workers_compensation", reference="WC-10", expiry_date=iso(300))
    assert client.post(f"{PUB}/{token2}/submit").json()["status"] == "submitted"
    detail2 = client.get(f"{REN}/{request['id']}", headers=admin).json()
    assert len(detail2["uploads"]) == 1 and len(detail2["earlier_uploads"]) == 2
    final = client.post(f"{REN}/{request['id']}/review", headers=admin, json={"decisions": [{"upload_id": detail2["uploads"][0]["id"], "decision": "accept"}]}).json()
    assert final["status"] == "accepted"
    summary = client.get(f"{SUP}/{acme['id']}", headers=admin).json()["compliance"]
    assert (summary["valid"], summary["expired"], summary["missing"]) == (2, 0, [])
    assert client.post(f"{REN}/{request['id']}/reissue", json={}, headers=admin).status_code == 409


def test_links_expire_and_can_be_revoked_changed_and_deleted(world, client, db):
    admin = world.auth("admin1")
    acme = new_supplier(client, admin)
    made = client.post(REN, json={"entity_id": acme["id"], "doc_types_requested": ["licence"], "expires_in_days": 3}, headers=admin).json()
    token = token_of(made)
    assert made["delivery"] == "none"  # no contact email, so no message was even queued
    changed = client.patch(f"{REN}/{made['id']}", json={"doc_types_requested": ["licence", "swms"], "message": "Both please"}, headers=admin).json()
    assert [d["slug"] for d in changed["doc_types"]] == ["licence", "swms"]
    assert [d["slug"] for d in client.get(f"{PUB}/{token}").json()["documents"]] == ["licence", "swms"]  # same link
    past = (utcnow() - timedelta(days=1)).isoformat()
    assert client.patch(f"{REN}/{made['id']}", json={"expires_at": past}, headers=admin).status_code == 422

    link = db.get(RenewalLink, made["id"])
    link.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert client.get(f"{PUB}/{token}").json()["status"] == "expired"
    assert public_upload(client, token, "licence").status_code == 410
    assert client.get(f"{REN}?status=expired", headers=admin).json()["total"] == 1
    fresh = client.post(f"{REN}/{made['id']}/reissue", json={"expires_in_days": 5}, headers=admin).json()
    assert client.get(f"{PUB}/{token_of(fresh)}").json()["can_upload"] is True

    assert client.post(f"{REN}/{made['id']}/revoke", headers=admin).json()["status"] == "revoked"
    assert client.get(f"{PUB}/{token_of(fresh)}").status_code == 404
    assert public_upload(client, token_of(fresh), "licence").status_code == 404
    assert client.patch(f"{REN}/{made['id']}", json={"message": "x"}, headers=admin).status_code == 409
    assert client.delete(f"{REN}/{made['id']}", headers=world.auth("hseq1")).status_code == 403
    assert client.delete(f"{REN}/{made['id']}", headers=admin).json() == {"ok": True}
    assert client.get(f"{REN}/{made['id']}", headers=admin).status_code == 404
    assert client.get(REN, headers=admin).json()["total"] == 0


def test_bulk_requests_skip_suppliers_already_asked(world, client):
    admin = world.auth("admin1")
    a, b = new_supplier(client, admin, is_contractor=True), new_supplier(client, admin, name="Beta Civil", is_contractor=True)
    client.post(REN, json={"entity_id": a["id"], "doc_types_requested": ["public_liability", "licence"]}, headers=admin)
    result = client.post(f"{REN}/bulk", headers=admin, json={"contractor_ids": [a["id"], b["id"], "missing"], "doc_types_requested": ["public_liability"]}).json()
    assert [c["entity_name"] for c in result["created"]] == ["Beta Civil"] and result["created"][0]["link"].startswith("https://portal.test/renew/")
    assert result["skipped"] == [{"id": a["id"], "reason": "already_requested"}, {"id": "missing", "reason": "not_found"}]
    listing = client.get(REN, headers=admin).json()
    assert listing["total"] == 2 and listing["counts"]["pending"] == 2
    assert client.get(f"{REN}?entity_id={b['id']}", headers=admin).json()["total"] == 1
    ids = [r["id"] for r in listing["items"]]
    assert client.post(f"{REN}/bulk-delete", json={"ids": [*ids, "nope"]}, headers=admin).json() == {
        "requested": 3, "deleted": 2, "skipped": [{"id": "nope", "reason": "not_found"}]}


def test_worker_certificate_renewal_through_a_link(world, client, db):
    admin = world.auth("admin1")
    mia = world.add_worker("Mia", "Tanaka", email="mia@one.example")
    old = client.post(f"/api/workers/{mia.id}/certifications", json={"kind": "first_aid", "number": "FA-1", "expiry_date": iso(-10)}, headers=admin).json()
    # Supervisors hold renewals.edit but not certification edit, so they cannot ask a worker for certificates.
    body = {"entity_type": "worker", "entity_id": mia.id, "doc_types_requested": ["First Aid", "white card"]}
    assert client.post(REN, json=body, headers=world.auth("super1")).status_code == 403
    assert client.post(REN, json={**body, "doc_types_requested": ["unicycle"]}, headers=admin).status_code == 422
    made = client.post(REN, json=body, headers=admin).json()
    assert [d["slug"] for d in made["doc_types"]] == ["first_aid", "white_card"] and made["recipient_email"] == "mia@one.example"
    token = token_of(made)
    page = client.get(f"{PUB}/{token}").json()
    assert page["kind"] == "worker" and page["for_name"] == "Mia Tanaka" and [d["label"] for d in page["documents"]] == ["First Aid", "White Card (Construction Induction)"]
    assert "FA-1" not in str(page) and mia.id not in str(page)
    public_upload(client, token, "first_aid", expiry_date=iso(700))
    public_upload(client, token, "white_card")
    client.post(f"{PUB}/{token}/submit")
    uploads = client.get(f"{REN}/{made['id']}", headers=admin).json()["uploads"]
    decisions = [{"upload_id": uploads[0]["id"], "decision": "accept"}, {"upload_id": uploads[1]["id"], "decision": "accept", "no_expiry": True}]
    assert client.post(f"{REN}/{made['id']}/review", json={"decisions": decisions}, headers=admin).json()["status"] == "accepted"

    certs = client.get(f"/api/workers/{mia.id}/certifications?history=true", headers=admin).json()["items"]
    first_aid = [c for c in certs if c["kind"]["slug"] == "first_aid"]
    current = next(c for c in first_aid if c["current"])
    assert (current["status"], current["number"], current["supersedes_id"], current["evidence"]["name"]) == ("valid", "FA-1", old["id"], "doc.pdf")
    assert next(c for c in first_aid if c["id"] == old["id"])["current"] is False
    white = next(c for c in certs if c["kind"]["slug"] == "white_card")
    assert white["no_expiry"] is True and white["status"] == "valid"
    assert db.scalar(select(Certification).where(Certification.id == current["id"])).evidence_file_id is not None
