"""Inductions matrix, completed training, HR documents and daily jobs (stage 4d)."""
from datetime import date, timedelta

from sqlalchemy import select

from app.models import AuditLog, Certification, DailyJob, OutboxMessage, Site, utcnow

M = "/api/workers/inductions"
T = "/api/completed-training"
J = "/api/daily-jobs"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
TODAY = date.today()


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def cell(client, headers, worker_id, column, **body):
    return client.put(f"{M}/cell", json={"worker_id": worker_id, "column_key": column, **body}, headers=headers)


# ---------- inductions ----------

def test_matrix_columns_cells_and_summary(world, client, db):
    admin = world.auth("admin1")
    mia, ben = world.add_worker("Mia", "Tanaka"), world.add_worker("Ben", "Abbott")
    world.add_worker("Old", "Hand", active=False)
    world.add_worker("Other", "Org", org=world.org2)
    grid = client.get(f"{M}/matrix", headers=admin).json()
    keys = [c["column_key"] for c in grid["columns"]]
    assert "taswater_induction" in keys and len(keys) == 6 and "first_aid" not in keys  # inductions only
    assert [r["worker"]["name"] for r in grid["rows"]] == ["Ben Abbott", "Mia Tanaka"]
    assert grid["summary"] == {"valid": 0, "expiring": 0, "expired": 0, "unknown": 0, "not_applicable": 0, "blank": 12}

    assert cell(client, admin, mia.id, "taswater_induction").status_code == 422  # nothing chosen
    assert cell(client, admin, mia.id, "taswater_induction", expiry_date=iso(10), not_held=True).status_code == 422  # two things
    assert cell(client, admin, mia.id, "first_aid", expiry_date=iso(10)).status_code == 404  # not a matrix column
    assert cell(client, admin, "missing", "taswater_induction", expiry_date=iso(10)).status_code == 404
    assert cell(client, admin, mia.id, "taswater_induction", issue_date=iso(0), expiry_date=iso(-1)).status_code == 422

    done = cell(client, admin, mia.id, "taswater_induction", expiry_date=iso(200)).json()["cell"]
    assert done["status"] == "valid" and done["expiry_date"] == iso(200) and done["has_evidence"] is False
    assert cell(client, admin, mia.id, "tasrail_induction", expiry_date=iso(5)).json()["cell"]["status"] == "expiring"
    assert cell(client, admin, mia.id, "tasgas_induction", expiry_date=iso(-5)).json()["cell"]["status"] == "expired"
    assert cell(client, admin, mia.id, "airport_induction", held_no_expiry=True).json()["cell"]["no_expiry"] is True
    assert cell(client, admin, ben.id, "taswater_induction", not_held=True).json()["cell"] == {"status": "not_applicable"}
    summary = client.get(f"{M}/matrix", headers=admin).json()["summary"]
    assert summary == {"valid": 2, "expiring": 1, "expired": 1, "unknown": 0, "not_applicable": 1, "blank": 7}

    # The same record shows in the certification register: one store, two views.
    register = client.get(f"/api/workers/certifications/all?worker_id={mia.id}", headers=admin).json()
    assert {c["kind"]["slug"] for c in register["items"]} == {"taswater_induction", "tasrail_induction", "tasgas_induction", "airport_induction"}
    # Editing a cell changes the same certificate; it does not add a second one.
    cert_id = done["certification_id"]
    moved = cell(client, admin, mia.id, "taswater_induction", expiry_date=iso(400)).json()["cell"]
    assert moved["certification_id"] == cert_id and moved["expiry_date"] == iso(400)
    assert db.scalar(select(Certification).where(Certification.worker_id == mia.id, Certification.kind_slug == "taswater_induction")).id == cert_id
    # Not applicable cannot hide a recorded completion; clearing removes it, and a date replaces "not applicable".
    assert cell(client, admin, mia.id, "taswater_induction", not_held=True).status_code == 409
    assert cell(client, admin, mia.id, "taswater_induction", clear=True).json()["cell"] == {"status": "blank"}
    assert cell(client, admin, ben.id, "taswater_induction", expiry_date=iso(30)).json()["cell"]["status"] == "expiring"
    assert [a.action for a in db.scalars(select(AuditLog).where(AuditLog.target_id == ben.id).order_by(AuditLog.created_at)).all()] == [
        "induction.marked_not_applicable", "induction.recorded"]

    listed = client.get(f"{M}/matrix?include_inactive=true&q=hand", headers=admin).json()
    assert [r["worker"]["name"] for r in listed["rows"]] == ["Old Hand"]
    export = client.get(f"{M}/export.csv", headers=admin)
    header = export.text.lstrip("\ufeff").splitlines()[0]
    assert export.status_code == 200 and header.split(",")[:2] == ["Worker", "Position"]
    assert all(c["name"] in header for c in grid["columns"])
    assert "Does not expire" in export.text and "(Expired)" in export.text


def test_matrix_scope_and_permissions(world, client):
    admin, worker, supervisor, auditor = (world.auth(k) for k in ("admin1", "worker1", "super1", "auditor1"))
    mia = world.add_worker("Mia", "Tanaka")
    mine = world.add_worker("Tom", "Okafor", link="worker1")
    cell(client, admin, mia.id, "taswater_induction", expiry_date=iso(100))
    cell(client, admin, mine.id, "taswater_induction", expiry_date=iso(100))
    own = client.get(f"{M}/matrix", headers=worker).json()
    assert [r["worker"]["id"] for r in own["rows"]] == [mine.id] and own["can_edit"] is False
    assert cell(client, worker, mine.id, "taswater_induction", clear=True).status_code == 403
    assert cell(client, supervisor, mia.id, "tasrail_induction", expiry_date=iso(50)).status_code == 200  # supervisors keep the matrix
    assert cell(client, auditor, mia.id, "tasrail_induction", clear=True).status_code == 403
    assert len(client.get(f"{M}/matrix", headers=auditor).json()["rows"]) == 2
    # Another organisation sees and changes nothing here.
    other = world.auth("admin2")
    assert client.get(f"{M}/matrix", headers=other).json()["rows"] == []
    assert cell(client, other, mia.id, "taswater_induction", clear=True).status_code == 404
    assert client.get(f"{M}/matrix").status_code == 401


def test_induction_types_add_rename_retire(world, client):
    admin, supervisor = world.auth("admin1"), world.auth("super1")
    mia = world.add_worker("Mia", "Tanaka")
    made = client.post("/api/inductions/types", json={"name": "Hydro Tasmania induction"}, headers=admin)
    assert made.status_code == 201 and made.json()["column_key"] == "hydro_tasmania_induction"
    assert client.post("/api/inductions/types", json={"name": "First Aid"}, headers=admin).status_code == 409  # clashes with a certificate type
    assert client.post("/api/inductions/types", json={"name": "Nope"}, headers=world.auth("worker1")).status_code == 403
    tid = made.json()["id"]
    assert client.patch(f"/api/inductions/types/{tid}", json={"name": "Hydro Tasmania site induction"}, headers=admin).json()["name"] == "Hydro Tasmania site induction"
    cell(client, admin, mia.id, "hydro_tasmania_induction", expiry_date=iso(90))
    assert client.delete(f"/api/inductions/types/{tid}", headers=supervisor).status_code == 403
    assert client.delete(f"/api/inductions/types/{tid}", headers=admin).json()["active"] is False
    assert "hydro_tasmania_induction" not in [c["column_key"] for c in client.get(f"{M}/matrix", headers=admin).json()["columns"]]
    # The completion is still on the worker's record with its name.
    held = client.get(f"/api/workers/{mia.id}/certifications", headers=admin).json()["items"]
    assert [c["kind"]["label"] for c in held] == ["Hydro Tasmania site induction"]
    assert client.post(f"/api/inductions/types/{tid}/restore", headers=admin).json()["active"] is True
    # A certificate type cannot be reached through the induction routes.
    kinds = client.get("/api/certification-kinds", headers=admin).json()["items"]
    first_aid = next(k for k in kinds if k["slug"] == "first_aid")
    assert first_aid["category"] == "certificate" and client.delete(f"/api/inductions/types/{first_aid['id']}", headers=admin).status_code == 404


# ---------- training ----------

def test_training_records_scope_validation_and_evidence(world, client):
    admin, hseq, worker, supervisor = (world.auth(k) for k in ("admin1", "hseq1", "worker1", "super1"))
    mia = world.add_worker("Mia", "Tanaka")
    mine = world.add_worker("Tom", "Okafor", link="worker1")
    body = {"worker_id": mia.id, "course": "Manual handling refresher", "provider": "In-house", "completed_date": iso(-3)}
    assert client.post(T, json={**body, "completed_date": iso(2)}, headers=admin).status_code == 422
    assert client.post(T, json={**body, "worker_id": "missing"}, headers=admin).status_code == 404
    assert client.post(T, json=body, headers=supervisor).status_code == 403
    record = client.post(T, json=body, headers=hseq).json()
    own = client.post(T, json={"worker_id": mine.id, "course": "Spill response"}, headers=admin).json()
    assert [r["course"] for r in client.get(T, headers=admin).json()["items"]] == ["Manual handling refresher", "Spill response"]
    assert client.get(f"{T}?q=spill", headers=admin).json()["total"] == 1
    # A worker sees only their own training.
    seen = client.get(T, headers=worker).json()
    assert [r["id"] for r in seen["items"]] == [own["id"]] and seen["can_edit"] is False
    assert client.get(f"{T}/{record['id']}", headers=worker).status_code == 404
    assert client.get(f"{T}/{record['id']}", headers=world.auth("admin2")).status_code == 404

    up = client.post(f"{T}/{record['id']}/file", files={"file": ("cert.pdf", PDF, "application/pdf")}, headers=hseq).json()
    assert up["evidence"]["name"] == "cert.pdf" and client.get(up["evidence_url"], headers=admin).content == PDF
    assert client.post(f"{T}/{record['id']}/file", files={"file": ("x.pdf", b"MZ\x90\x00", "application/pdf")}, headers=hseq).status_code == 415
    assert client.patch(f"{T}/{record['id']}", json={"provider": "TasTAFE"}, headers=hseq).json()["provider"] == "TasTAFE"
    assert client.delete(f"{T}/{record['id']}", headers=hseq).status_code == 403  # no delete right
    assert client.delete(f"{T}/{record['id']}", headers=admin).json() == {"ok": True}
    assert client.get(T, headers=admin).json()["total"] == 1


# ---------- HR documents ----------

def test_hr_documents_are_private_and_every_open_is_logged(world, client, db):
    admin, hseq, supervisor = world.auth("admin1"), world.auth("hseq1"), world.auth("super1")
    mia = world.add_worker("Mia", "Tanaka", )
    base = f"/api/workers/{mia.id}/hr-documents"
    assert client.post(base, files={"file": ("contract.pdf", PDF, "application/pdf")}, headers=hseq).status_code == 403
    doc = client.post(base, files={"file": ("contract.pdf", PDF, "application/pdf")}, data={"notes": "Signed 2024"}, headers=admin)
    assert doc.status_code == 201 and doc.json()["title"] == "contract.pdf"
    doc = doc.json()
    assert client.get(base, headers=supervisor).status_code == 403  # no HR access at all
    listing = client.get(base, headers=hseq).json()  # may know a file exists, may not open it
    assert [d["title"] for d in listing["items"]] == ["contract.pdf"] and listing["can_open"] is False
    assert client.get(doc["file_url"], headers=hseq).status_code == 403
    assert client.get(doc["file_url"], headers=world.auth("admin2")).status_code == 404
    assert client.get(doc["file_url"], headers=admin).content == PDF
    opened = db.scalars(select(AuditLog).where(AuditLog.action == "hr_document.opened")).one()
    assert opened.actor_user_id == world.id("admin1") and opened.target_id == mia.id
    # Someone with their own worker record still cannot see their own HR file through this route.
    world.add_worker("Tom", "Okafor", link="worker1")
    assert client.get(base, headers=world.auth("worker1")).status_code == 403

    assert client.patch(f"{base}/{doc['id']}", json={"title": "Employment contract"}, headers=admin).json()["title"] == "Employment contract"
    assert client.post(f"{base}/{doc['id']}/archive", headers=admin).json()["archived_at"] is not None
    assert client.get(base, headers=admin).json()["items"] == []
    assert len(client.get(f"{base}?include_archived=true", headers=admin).json()["items"]) == 1
    assert client.post(f"{base}/{doc['id']}/restore", headers=admin).json()["archived_at"] is None
    assert client.delete(f"{base}/{doc['id']}", headers=admin).json() == {"ok": True}
    assert client.get(doc["file_url"], headers=admin).status_code == 404


# ---------- daily jobs ----------

def _site(db, world, name="Bridge Rd upgrade"):
    site = Site(org_id=world.org1.id, name=name, address="Bridge Rd", suburb="Launceston", scan_token=f"tok-{name}-0000000000000000")
    db.add(site)
    db.commit()
    return site


def test_issue_accept_complete_and_one_active_job_per_day(world, client, db):
    admin, worker = world.auth("admin1"), world.auth("worker1")
    tom = world.add_worker("Tom", "Okafor", link="worker1", mobile="0400 000 111")
    site = _site(db, world)
    assert client.get(f"{J}/mine/today", headers=worker).json() == {"assignment": None, "status": "no_job"}
    assert client.post(J, json={"worker_id": tom.id}, headers=admin).status_code == 422  # nowhere to go
    assert client.post(J, json={"worker_id": tom.id, "site_name": "Depot", "date": iso(-1)}, headers=admin).status_code == 422
    assert client.post(J, json={"worker_id": tom.id, "site_name": "Depot", "start_time": "7am"}, headers=admin).status_code == 422
    assert client.post(J, json={"worker_id": tom.id, "site_id": "missing"}, headers=admin).status_code == 404
    assert client.post(J, json={"worker_id": tom.id, "site_name": "Depot", "form_template_ids": ["missing"]}, headers=admin).status_code == 422
    for key in ("hseq1", "super1", "worker1"):  # issuing is admin-only in the baseline
        assert client.post(J, json={"worker_id": tom.id, "site_name": "Depot"}, headers=world.auth(key)).status_code == 403

    made = client.post(J, headers=admin, json={"worker_id": tom.id, "site_id": site.id, "start_time": "06:30", "truck": "PT-031",
                                               "staff": ["Tom Okafor", " Mia Tanaka ", "Tom Okafor"], "notes": "Meet at the depot first."})
    assert made.status_code == 201, made.text
    job = made.json()
    assert (job["site_name"], job["address"], job["staff"], job["status"]) == ("Bridge Rd upgrade", "Bridge Rd, Launceston", ["Tom Okafor", "Mia Tanaka"], "issued")
    # A text was prepared and held; nothing was sent.
    assert job["delivery"] == "held"
    message = db.scalars(select(OutboxMessage).where(OutboxMessage.purpose == "daily_job")).one()
    assert (message.status, message.channel, message.recipient) == ("held", "sms", "0400 000 111")

    # One active job per worker per day, unless the office explicitly replaces it.
    second = client.post(J, json={"worker_id": tom.id, "site_name": "Depot"}, headers=admin)
    assert second.status_code == 409 and "already has a job" in second.json()["detail"]
    replaced = client.post(J, json={"worker_id": tom.id, "site_name": "Depot", "override": True}, headers=admin).json()
    listing = client.get(J, headers=admin).json()
    assert listing["counts"]["issued"] == 1 and listing["counts"]["cancelled"] == 1 and listing["date"] == job["date"]
    assert db.get(DailyJob, job["id"]).status == "cancelled"

    today = client.get(f"{J}/mine/today", headers=worker).json()
    assert today["status"] == "issued" and today["assignment"]["id"] == replaced["id"]
    assert client.post(f"{J}/{replaced['id']}/complete", headers=worker).status_code == 409  # accept first
    assert client.post(f"{J}/{replaced['id']}/accept", headers=worker).json()["status"] == "accepted"
    assert client.post(f"{J}/{replaced['id']}/accept", headers=worker).json()["status"] == "accepted"  # repeat is harmless
    assert client.post(f"{J}/{job['id']}/accept", headers=worker).status_code == 409  # the cancelled one
    assert client.patch(f"{J}/{replaced['id']}", json={"notes": "Bring the laser level."}, headers=admin).json()["notes"] == "Bring the laser level."
    assert client.post(f"{J}/{replaced['id']}/complete", headers=worker).json()["status"] == "completed"
    assert client.patch(f"{J}/{replaced['id']}", json={"notes": "x"}, headers=admin).status_code == 409
    assert client.get(f"{J}/mine/today", headers=worker).json() == {"assignment": None, "status": "no_job"}
    mine = client.get(f"{J}/mine", headers=worker).json()
    assert mine["upcoming"] == [] and [j["status"] for j in mine["recent"]] == ["completed", "cancelled"]
    # With today's job finished, a new one can be issued.
    assert client.post(J, json={"worker_id": tom.id, "site_name": "Depot"}, headers=admin).status_code == 201


def test_jobs_belong_to_their_worker_and_never_fall_back_to_yesterday(world, client, db):
    admin, worker = world.auth("admin1"), world.auth("worker1")
    tom = world.add_worker("Tom", "Okafor", link="worker1")
    mia = world.add_worker("Mia", "Tanaka", link="super1")
    theirs = client.post(J, json={"worker_id": mia.id, "site_name": "Depot"}, headers=admin).json()
    assert theirs["delivery"] == "none"  # no phone number, so no message was prepared
    # Someone else's job is not visible or actionable, including to a supervisor's own login for another's job.
    assert client.get(f"{J}/{theirs['id']}", headers=worker).status_code == 404
    assert client.post(f"{J}/{theirs['id']}/accept", headers=worker).status_code == 404
    assert client.post(f"{J}/{theirs['id']}/decline", json={"reason": "Not mine"}, headers=worker).status_code == 404
    assert client.get(J, headers=worker).status_code == 403
    assert client.get(f"{J}/{theirs['id']}", headers=world.auth("admin2")).status_code == 404
    assert client.post(f"{J}/{theirs['id']}/cancel", headers=world.auth("admin2")).status_code == 404
    assert client.post(J, json={"worker_id": mia.id, "site_name": "Depot"}, headers=world.auth("admin2")).status_code == 404
    # A login with no worker record is told so, rather than shown an empty day.
    assert client.get(f"{J}/mine/today", headers=world.auth("auditor1")).json()["status"] == "no_worker_record"

    declined = client.post(f"{J}/{theirs['id']}/decline", json={"reason": "Off sick today"}, headers=world.auth("super1")).json()
    assert declined["status"] == "declined" and declined["decline_reason"] == "Off sick today"
    assert client.post(f"{J}/{theirs['id']}/decline", json={"reason": "x"}, headers=world.auth("super1")).status_code == 422

    # Yesterday's unanswered job is not offered as today's.
    old = DailyJob(org_id=world.org1.id, job_date=TODAY - timedelta(days=1), worker_id=tom.id, worker_name="Tom Okafor", site_name="Old site", issued_at=utcnow())
    db.add(old)
    db.commit()
    assert client.get(f"{J}/mine/today", headers=worker).json() == {"assignment": None, "status": "no_job"}
    assert [j["site_name"] for j in client.get(f"{J}/mine", headers=worker).json()["recent"]] == ["Old site"]


def test_bulk_issue_with_trial_run(world, client):
    admin = world.auth("admin1")
    a, b = world.add_worker("Ann", "Lee"), world.add_worker("Bob", "Ray")
    gone = world.add_worker("Old", "Hand", active=False)
    client.post(J, json={"worker_id": a.id, "site_name": "Depot"}, headers=admin)
    body = {"worker_ids": [a.id, b.id, gone.id, "missing"], "site_name": "Bridge Rd", "start_time": "07:00"}
    trial = client.post(f"{J}/bulk-create", json={**body, "trial_run": True}, headers=admin).json()
    assert trial["trial_run"] is True and [c["worker_name"] for c in trial["created"]] == ["Bob Ray"]
    assert [(s["name"], s["reason"]) for s in trial["skipped"]] == [("Ann Lee", "already_has_job"), ("Old Hand", "inactive"), (None, "not_found")]
    assert client.get(J, headers=admin).json()["counts"]["issued"] == 1  # nothing was saved
    real = client.post(f"{J}/bulk-create", json=body, headers=admin).json()
    assert [c["worker_name"] for c in real["created"]] == ["Bob Ray"] and real["batch_id"]
    forced = client.post(f"{J}/bulk-create", json={**body, "override": True}, headers=admin).json()
    assert {c["worker_name"] for c in forced["created"]} == {"Ann Lee", "Bob Ray"}
    counts = client.get(J, headers=admin).json()["counts"]
    assert counts["issued"] == 2 and counts["cancelled"] == 2
    assert client.post(f"{J}/bulk-create", json={"worker_ids": [], "site_name": "x"}, headers=admin).status_code == 422


def test_job_picker_offers_the_callers_own_jobs(world, client):
    admin, worker = world.auth("admin1"), world.auth("worker1")
    tom = world.add_worker("Tom", "Okafor", link="worker1")
    mia = world.add_worker("Mia", "Tanaka")
    mine = client.post(J, json={"worker_id": tom.id, "site_name": "Bridge Rd"}, headers=admin).json()
    client.post(J, json={"worker_id": mia.id, "site_name": "Depot"}, headers=admin)
    picked = client.get("/api/forms/pickers/jobs", headers=worker).json()
    assert picked["available"] is True and [p["id"] for p in picked["items"]] == [mine["id"]]
    assert picked["items"][0]["label"].startswith("Bridge Rd · ")
    assert len(client.get("/api/forms/pickers/jobs", headers=admin).json()["items"]) == 2
    assert client.get("/api/forms/pickers/jobs", headers=world.auth("admin2")).json()["items"] == []
