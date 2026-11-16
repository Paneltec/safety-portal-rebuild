"""The activity log and audit packs (stage 9b)."""
import csv
import datetime as dt
import hashlib
import io
import json
import zipfile

from sqlalchemy import select

from app.models import AuditExport, AuditLog, StoredFile
from app.people import today_for

from .test_safety import HAZARD, answers

LOG = "/api/audit-log"
PACKS = "/api/audit-exports"


def test_activity_log_is_scoped_filtered_and_hides_pay_entries(world, client, db):
    admin, hseq, worker, sup, owner, other = (world.auth(k) for k in ("admin1", "hseq1", "worker1", "super1", "owner1", "admin2"))
    site = client.post("/api/sites", json={"name": "=Bridge Rd"}, headers=admin).json()
    client.post("/api/sites", json={"name": "Other org depot"}, headers=other)
    client.put(f"/api/users/{world.id('admin1')}/payroll-access", json={"level": "view"}, headers=owner)   # writes a payroll.* entry
    client.put(f"/api/users/{world.id('admin1')}/payroll-access", json={"level": "none"}, headers=owner)

    assert client.get(LOG, headers=worker).status_code == 403 and client.get(LOG, headers=sup).status_code == 403 and client.get(LOG).status_code == 401
    body = client.get(LOG, headers=admin).json()
    actions = [i["action"] for i in body["items"]]
    assert "site.created" in actions and not [a for a in actions if a.startswith("payroll.")] and "pay and timesheet entries" in body["hidden"]
    assert all(i["who"] and i["at"] for i in body["items"]) and "site" in body["target_types"]
    # The payroll owner sees the pay entries as well; the HSEQ lead holds the audit permission.
    assert [a for a in (i["action"] for i in client.get(LOG, headers=owner).json()["items"]) if a.startswith("payroll.")]
    assert client.get(LOG, headers=hseq).status_code == 200
    # Another organisation sees only its own.
    theirs = client.get(LOG, headers=other).json()["items"]
    assert theirs and all(i["target_id"] != site["id"] for i in theirs)
    assert client.get(LOG, params={"target_id": site["id"]}, headers=other).json()["total"] == 0

    # Filters: by kind of action, record, person and day. A pattern character is not a pattern.
    assert {i["action"] for i in client.get(LOG, params={"action": "site."}, headers=admin).json()["items"]} == {"site.created"}
    assert client.get(LOG, params={"action": "%"}, headers=admin).json()["total"] == 0
    assert client.get(LOG, params={"target_type": "site", "target_id": site["id"]}, headers=admin).json()["total"] == 1
    mine = client.get(LOG, params={"actor_id": world.id("admin1")}, headers=admin).json()
    assert mine["total"] >= 1 and {i["who"] for i in mine["items"]} == {"Alex Rowe"}
    assert client.get(LOG, params={"actor_id": world.id("admin2")}, headers=admin).json()["total"] == 0
    today = today_for(world.org1)
    assert client.get(LOG, params={"date_to": (today - dt.timedelta(days=1)).isoformat()}, headers=admin).json()["total"] == 0
    assert client.get(LOG, params={"date_from": today.isoformat(), "date_to": today.isoformat()}, headers=admin).json()["total"] == body["total"]
    assert client.get(LOG, params={"date_from": today.isoformat(), "date_to": (today - dt.timedelta(days=1)).isoformat()}, headers=admin).status_code == 422

    # The file: same entries, never a runnable formula, and taking it is itself recorded.
    before = body["total"]
    out = client.get(f"{LOG}/export.csv", headers=admin)
    assert out.status_code == 200 and out.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(out.content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["When (UTC)", "Who", "Action"] and len(rows) - 1 == before
    assert not [c for row in rows[1:] for c in row if c[:1] in ("=", "+", "@")] and any("'=Bridge Rd" in c or "=Bridge Rd" in c for row in rows for c in row)
    assert client.get(f"{LOG}/export.csv", headers=worker).status_code == 403
    db.expire_all()
    assert db.scalars(select(AuditLog).where(AuditLog.action == "audit_log.exported")).one().detail["entries"] == before
    # There is no way to change or remove an entry.
    entry = body["items"][0]["id"]
    assert client.delete(f"{LOG}/{entry}", headers=admin).status_code in (404, 405) and client.patch(f"{LOG}/{entry}", json={}, headers=admin).status_code in (404, 405)


def test_audit_pack_holds_what_its_manifest_says(world, client, db):
    admin, hseq, worker, sup, auditor, other = (world.auth(k) for k in ("admin1", "hseq1", "worker1", "super1", "auditor1", "admin2"))
    today = today_for(world.org1)
    mia = world.add_worker("Mia", "Tran")
    client.post(f"/api/workers/{mia.id}/certifications", json={"kind": "first_aid", "expiry_date": (today - dt.timedelta(days=3)).isoformat()}, headers=admin)
    client.post("/api/hazards", headers=worker, json={"fields": answers(**HAZARD)})
    client.post("/api/hazards", headers=sup, json={"fields": answers(**{**HAZARD, "title": "@Loose plate"})})
    client.post("/api/hazards", headers=other, json={"fields": answers(**{**HAZARD, "title": "Other organisation hazard"})})

    registers = {r["key"]: r for r in client.get(f"{PACKS}/registers", headers=admin).json()["items"]}
    assert registers["hazard"]["allowed"] and registers["certifications"]["allowed"] and registers["activity_log"]["allowed"]
    assert client.get(f"{PACKS}/registers", headers=worker).status_code == 403
    # Validation and permission.
    ask = {"title": "Client audit, November", "registers": ["hazard", "certifications", "activity_log"]}
    assert client.post(PACKS, json=ask, headers=worker).status_code == 403
    assert client.post(PACKS, json=ask, headers=auditor).status_code == 403  # may read packs, not make them
    assert client.post(PACKS, json={**ask, "registers": ["nonsense"]}, headers=admin).status_code == 422
    assert client.post(PACKS, json={**ask, "registers": []}, headers=admin).status_code == 422
    assert client.post(PACKS, json={**ask, "date_from": "2026-02-01", "date_to": "2026-01-01"}, headers=admin).status_code == 422

    made = client.post(PACKS, json=ask, headers=admin)
    assert made.status_code == 201
    pack = made.json()
    assert pack["status"] == "ready" and pack["file_available"] and {r["key"]: r["records"] for r in pack["registers"]}["hazard"] == 2
    got = client.get(f"{PACKS}/{pack['id']}/download", headers=admin)
    assert got.status_code == 200 and hashlib.sha256(got.content).hexdigest() == pack["sha256"] and len(got.content) == pack["size_bytes"]
    archive = zipfile.ZipFile(io.BytesIO(got.content))
    assert sorted(archive.namelist()) == ["activity_log.csv", "certifications.csv", "hazard.csv", "manifest.json"]
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["organisation"] == "Org One" and manifest["made_by"] == "Alex Rowe" and "Attached files" in manifest["not_included"]
    for entry in manifest["files"]:  # every file is what the manifest says it is
        data = archive.read(entry["name"])
        assert hashlib.sha256(data).hexdigest() == entry["sha256"] and len(data) == entry["bytes"]
        assert len(list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))) - 1 == entry["records"]
    hazards = archive.read("hazard.csv").decode("utf-8-sig")
    assert "Open trench unfenced" in hazards and "'@Loose plate" in hazards and "Other organisation hazard" not in hazards
    certs = list(csv.reader(io.StringIO(archive.read("certifications.csv").decode("utf-8-sig"))))
    assert certs[1][0] == "Mia Tran" and "xpired" in certs[1][7]
    assert not [line for line in archive.read("activity_log.csv").decode("utf-8-sig").splitlines() if ",payroll." in line]

    # A date range leaves out what falls outside it.
    past = client.post(PACKS, json={"title": "Last year", "registers": ["hazard"], "date_from": "2025-01-01", "date_to": "2025-12-31"}, headers=admin).json()
    assert past["registers"][0]["records"] == 0 and past["date_from"] == "2025-01-01"

    # Who can see and fetch: the list for readers; the file only with whole-organisation reach of what is inside.
    assert [p["id"] for p in client.get(PACKS, headers=auditor).json()["items"]] == [past["id"], pack["id"]]
    assert client.get(f"{PACKS}/{pack['id']}/download", headers=auditor).status_code == 403  # holds the activity log
    assert client.get(f"{PACKS}/{past['id']}/download", headers=auditor).status_code == 200
    assert client.get(PACKS, headers=worker).status_code == 403
    for method, url in (("get", f"{PACKS}/{pack['id']}"), ("get", f"{PACKS}/{pack['id']}/download"), ("delete", f"{PACKS}/{pack['id']}")):
        assert getattr(client, method)(url, headers=other).status_code == 404
    assert client.get(PACKS, headers=other).json()["items"] == []
    # A supervisor sees hazards for the team but not the activity log, so cannot make a pack with it.
    refused = client.post(PACKS, json=ask, headers=sup)
    assert refused.status_code == 403

    # Removing takes it off the list and keeps the evidence.
    assert client.delete(f"{PACKS}/{pack['id']}", headers=auditor).status_code == 403
    assert client.delete(f"{PACKS}/{pack['id']}", headers=admin).json() == {"removed": True}
    assert [p["id"] for p in client.get(PACKS, headers=admin).json()["items"]] == [past["id"]] and client.get(f"{PACKS}/{pack['id']}", headers=admin).status_code == 404
    db.expire_all()
    row = db.get(AuditExport, pack["id"])
    assert row.deleted_at is not None and db.get(StoredFile, row.file_id) is not None
    actions = [a.action for a in db.scalars(select(AuditLog).where(AuditLog.target_id == pack["id"]).order_by(AuditLog.created_at)).all()]
    assert actions == ["audit_export.created", "audit_export.downloaded", "audit_export.removed"]
