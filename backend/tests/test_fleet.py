"""Fleet and equipment register, labels, meters, documents and the Navixy sync (stage 6a)."""
import io
import json

import httpx
import pytest
from sqlalchemy import select

from app import integrations
from app.crypto import decrypt_json
from app.models import Asset, AssetMeterReading, AuditLog, IntegrationConfig

A = "/api/assets"
N = "/api/fleet/navixy"
I = "/api/integrations"
BASE = "https://api.eu.navixy.com"
PASSWORD = "navixy-pass-XYZ987"


def add(client, headers, **body):
    r = client.post(A, json={"name": "Ute 01", "kind": "vehicle", **body}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def navixy_api(monkeypatch):
    """A stand-in Navixy account."""
    state = {"hash": "hash-1", "trackers": [], "tags": [{"id": 5, "name": "Civil"}, {"id": 6, "name": "Traffic"}], "states": {}, "odometer": {}, "hours": {},
             "down": False, "counters": True, "auths": 0, "always_invalid": False}
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if state["down"]:
            raise httpx.ConnectError("no route")
        body = json.loads(request.content or b"{}")
        path = request.url.path.removeprefix("/v2/")
        if path == "user/auth":
            state["auths"] += 1
            if body.get("password") != PASSWORD:
                return httpx.Response(401, json={"success": False, "status": {"code": 11, "description": "Access denied"}})
            state["hash"] = f"hash-{state['auths'] + 1}"
            return httpx.Response(200, json={"success": True, "hash": state["hash"]})
        if state["always_invalid"] or body.get("hash") != state["hash"]:
            return httpx.Response(400, json={"success": False, "status": {"code": 4, "description": "User or API key not found or session ended"}})
        if path == "tracker/list":
            return httpx.Response(200, json={"success": True, "list": state["trackers"]})
        if path == "tag/list":
            return httpx.Response(200, json={"success": True, "list": state["tags"]})
        if path == "tracker/get_states":
            return httpx.Response(200, json={"success": True, "states": state["states"]})
        if path == "tracker/counter/value/list":
            if not state["counters"]:
                return httpx.Response(400, json={"success": False, "status": {"code": 236, "description": "Feature unavailable due to tariff restrictions"}})
            return httpx.Response(200, json={"success": True, "value": state["odometer"] if body["type"] == "odometer" else state["hours"]})
        return httpx.Response(404, json={"success": False})

    monkeypatch.setattr(integrations, "transport", httpx.MockTransport(handler))
    return type("Api", (), {"state": state, "calls": calls})


def tracker(i, label, tags=()):
    return {"id": i, "label": label, "group_id": 1, "source": {"id": i * 10, "model": "telfmb920"}, "tag_bindings": [{"tag_id": t, "ordinal": 1} for t in tags]}


def connect_live(client, headers):
    saved = client.put(f"{I}/navixy", json={"settings": {"api_base_url": BASE, "email": "fleet@one.example"}, "secrets": {"password": PASSWORD}, "mode": "live"}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert client.post(f"{I}/navixy/test-connection", headers=headers).json()["ok"] is True


def sync(client, headers, dry_run=False):
    return client.post(f"{N}/sync", json={"dry_run": dry_run}, headers=headers)


# ---------- register ----------

def test_register_permissions_validation_and_tenant_isolation(world, client, db):
    admin, hseq, sup, worker, other = world.auth("admin1"), world.auth("hseq1"), world.auth("super1"), world.auth("worker1"), world.auth("admin2")
    assert client.get(A, headers=worker).status_code == 403
    assert client.post(A, json={"name": "X"}, headers=sup).status_code == 403  # supervisors can look, not change
    assert client.post(A, json={"name": "  "}, headers=admin).status_code == 422
    assert client.post(A, json={"name": "X", "kind": "spaceship"}, headers=admin).status_code == 422
    assert client.post(A, json={"name": "X", "odometer_km": -5}, headers=admin).status_code == 422
    assert client.post(A, json={"name": "X", "tank_capacity_l": "NaN"}, headers=admin).status_code == 422
    ute = add(client, hseq, name=" Ute 01 ", registration="abc123", make="Toyota", odometer_km=1200, tank_capacity_l=80, sub_type="Ute")
    assert ute["name"] == "Ute 01" and ute["registration"] == "ABC123" and ute["label"] == "Ute 01 (ABC123)" and ute["source_label"] == "Added by hand"
    assert ute["odometer_km"] == 1200 and ute["engine_hours"] is None  # unknown is not zero
    digger = add(client, admin, name="Excavator 5t", kind="plant", engine_hours=310.5, status="out_of_service")
    theirs = add(client, other, name="Ute 01", registration="ZZZ999")

    listed = client.get(A, headers=sup).json()
    assert [a["name"] for a in listed["items"]] == ["Excavator 5t", "Ute 01"] and listed["total"] == 2 and listed["can_edit"] is False
    assert [a["id"] for a in client.get(A, params={"q": "abc1"}, headers=admin).json()["items"]] == [ute["id"]]
    assert [a["id"] for a in client.get(A, params={"kind": "plant"}, headers=admin).json()["items"]] == [digger["id"]]
    assert client.get(A, params={"status": "out_of_service"}, headers=admin).json()["total"] == 1
    paged = client.get(A, params={"page_size": 1, "page": 2}, headers=admin).json()
    assert paged["total"] == 2 and [a["name"] for a in paged["items"]] == ["Ute 01"]
    cats = client.get(f"{A}/categories", headers=admin).json()["items"]
    assert [(c["kind"], c["count"]) for c in cats] == [("vehicle", 1), ("plant", 1)] and cats[0]["types"] == [{"sub_type": "Ute", "count": 1}]

    # Another organisation's asset is simply not there, by any route.
    for method, url, kwargs in [("get", f"{A}/{theirs['id']}", {}), ("put", f"{A}/{theirs['id']}", {"json": {"name": "Mine"}}), ("delete", f"{A}/{theirs['id']}", {}),
                                ("get", f"{A}/{theirs['id']}/qr.png", {}), ("get", f"{A}/{theirs['id']}/label.pdf", {}), ("get", f"{A}/{theirs['id']}/meter-history", {}),
                                ("post", f"{A}/{theirs['id']}/meter", {"json": {"odometer_km": 5}}), ("post", f"{A}/labels/bulk", {"json": {"asset_ids": [ute["id"], theirs["id"]]}})]:
        assert getattr(client, method)(url, headers=admin, **kwargs).status_code == 404, url
    assert client.put(f"{A}/{ute['id']}", json={"site_id": "nope"}, headers=admin).status_code == 422

    edited = client.put(f"{A}/{ute['id']}", json={"status": "archived", "notes": "Sold"}, headers=hseq).json()
    assert edited["status"] == "archived" and edited["notes"] == "Sold"
    assert client.get(A, headers=admin).json()["total"] == 1 and client.get(A, params={"status": "archived"}, headers=admin).json()["total"] == 1
    assert client.put(f"{A}/{ute['id']}", json={"odometer_km": 5}, headers=admin).status_code == 422  # meters keep a history
    assert client.delete(f"{A}/{digger['id']}", headers=hseq).status_code == 403  # no delete right
    assert client.delete(f"{A}/{digger['id']}", headers=admin).status_code == 204
    assert client.get(f"{A}/{digger['id']}", headers=admin).status_code == 404
    actions = [a.action for a in db.scalars(select(AuditLog).where(AuditLog.org_id == world.org1.id, AuditLog.target_type == "asset")).all()]
    assert actions.count("asset.created") == 2 and "asset.updated" in actions and "asset.deleted" in actions


def test_meters_keep_history_and_never_go_backwards_quietly(world, client, db):
    admin, hseq = world.auth("admin1"), world.auth("hseq1")
    ute = add(client, admin, odometer_km=1000)
    url = f"{A}/{ute['id']}"
    assert client.post(f"{url}/meter", json={}, headers=admin).status_code == 422
    assert client.post(f"{url}/meter", json={"odometer_km": 1100, "reading_date": "2999-01-01"}, headers=admin).status_code == 422
    assert client.post(f"{url}/meter", json={"odometer_km": float("1e9")}, headers=admin).status_code == 422
    ok = client.post(f"{url}/meter", json={"odometer_km": 1250.5, "engine_hours": 40, "note": "Weekly check"}, headers=hseq).json()
    assert ok["odometer_km"] == 1250.5 and ok["engine_hours"] == 40
    lower = client.post(f"{url}/meter", json={"odometer_km": 900}, headers=hseq)
    assert lower.status_code == 409 and "lower than the last one" in lower.json()["detail"]
    assert client.post(f"{url}/meter/reset", json={"odometer_km": 900, "reason": "Instrument cluster replaced"}, headers=hseq).status_code == 403  # admin only
    assert client.post(f"{url}/meter/reset", json={"odometer_km": 900, "reason": "x"}, headers=admin).status_code == 422
    reset = client.post(f"{url}/meter/reset", json={"odometer_km": 900, "reason": "Instrument cluster replaced"}, headers=admin).json()
    assert reset["odometer_km"] == 900 and reset["engine_hours"] == 40  # hours untouched
    history = client.get(f"{url}/meter-history", headers=world.auth("super1")).json()["items"]
    assert [(h["source"], h["odometer_km"]) for h in history] == [("reset", 900), ("manual", 1250.5), ("manual", 1000)]
    assert history[0]["note"] == "Instrument cluster replaced" and history[0]["recorded_by"] == "Alex Rowe"
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "asset.meter_reset")).one()
    assert entry.detail["before"]["odometer_km"] == 1250.5 and entry.detail["after"]["odometer_km"] == 900


def test_labels_scan_and_documents(world, client):
    admin, sup, worker, other = world.auth("admin1"), world.auth("super1"), world.auth("worker1"), world.auth("admin2")
    ute = add(client, admin, registration="ABC123")
    url = f"{A}/{ute['id']}"
    png = client.get(f"{url}/qr.png", headers=sup)
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    pdf = client.get(f"{url}/label.pdf", headers=sup)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert client.post(f"{A}/labels/bulk", json={"asset_ids": [ute["id"]]}, headers=sup).content[:4] == b"%PDF"
    assert client.get(f"{url}/qr.png", headers=worker).status_code == 403
    token = client.get(url, headers=admin).json()["scan_url"].rsplit("/", 1)[1]
    assert client.get(url, headers=sup).json()["scan_url"] is None  # only editors are shown the label address
    assert client.get(f"{A}/scan/{token}").status_code == 401
    scanned = client.get(f"{A}/scan/{token}", headers=worker).json()  # any signed-in person of this organisation
    assert scanned["label"] == "Ute 01 (ABC123)" and scanned["can_open"] is False
    assert client.get(f"{A}/scan/{token}", headers=other).status_code == 404
    assert client.post(f"{url}/rotate-qr", headers=sup).status_code == 403
    assert client.post(f"{url}/rotate-qr", headers=admin).status_code == 200
    assert client.get(f"{A}/scan/{token}", headers=worker).status_code == 404  # old labels are retired

    files = {"file": ("rego.pdf", io.BytesIO(b"%PDF-1.4 registration papers"), "application/pdf")}
    assert client.post(f"{url}/documents", files=files, data={"title": "Rego"}, headers=sup).status_code == 403
    bad = client.post(f"{url}/documents", files=files, data={"issued_date": "2026-05-01", "expiry_date": "2026-04-01"}, headers=admin)
    assert bad.status_code == 422
    files = {"file": ("rego.pdf", io.BytesIO(b"%PDF-1.4 registration papers"), "application/pdf")}
    doc = client.post(f"{url}/documents", files=files, data={"title": "Registration", "kind": "certificate", "expiry_date": "2020-01-01"}, headers=admin)
    assert doc.status_code == 201 and doc.json()["expiry_status"] == "expired"
    detail = client.get(url, headers=sup).json()
    assert [d["title"] for d in detail["documents"]] == ["Registration"]
    got = client.get(f"{url}/documents/{doc.json()['id']}/download", headers=sup)
    assert got.status_code == 200 and got.content == b"%PDF-1.4 registration papers"
    theirs = add(client, other)
    assert client.get(f"{A}/{theirs['id']}/documents/{doc.json()['id']}/download", headers=other).status_code == 404  # right asset, someone else's document
    assert client.get(f"{url}/documents/{doc.json()['id']}/download", headers=other).status_code == 404
    assert client.delete(f"{url}/documents/{doc.json()['id']}", headers=admin).status_code == 204
    assert client.get(url, headers=admin).json()["documents"] == []


def test_equipment_categories_and_summary(world, client):
    admin, sup, other = world.auth("admin1"), world.auth("super1"), world.auth("admin2")
    cats = client.get("/api/equipment/categories", headers=sup).json()
    assert len(cats["items"]) == 6 and cats["can_edit"] is False
    assert client.post("/api/equipment/categories", json={"name": "Gas detectors"}, headers=sup).status_code == 403
    gas = client.post("/api/equipment/categories", json={"name": "Gas detectors"}, headers=admin).json()
    assert client.post("/api/equipment/categories", json={"name": "gas DETECTORS"}, headers=admin).status_code == 409
    assert client.patch(f"/api/equipment/categories/{gas['id']}", json={"name": "Gas monitors"}, headers=admin).json()["name"] == "Gas monitors"
    assert client.patch(f"/api/equipment/categories/{gas['id']}", json={"name": "Mine"}, headers=other).status_code == 404
    assert client.post(A, json={"name": "Detector", "kind": "equipment", "category_id": gas["id"]}, headers=other).status_code == 422  # not their category
    item = add(client, admin, name="Gas monitor 3", kind="equipment", category_id=gas["id"], serial_number="GM-003", next_inspection_date="2020-01-01")
    assert item["category"] == "Gas monitors" and item["inspection_status"] == "overdue"
    add(client, admin, name="Ladder 2", kind="equipment", status="out_of_service")
    add(client, admin, name="Ute 01")
    assert client.get(A, params={"kind": "equipment"}, headers=admin).json()["total"] == 2 and client.get(A, params={"kind": "fleet"}, headers=admin).json()["total"] == 1
    assert client.get("/api/equipment/summary", headers=sup).json() == {
        "total": 2, "out_of_service": 1, "inspection_overdue": 1, "inspection_due_soon": 0, "no_inspection_date": 1, "certificates_expiring_or_expired": 0}
    assert client.get("/api/equipment/summary", headers=other).json()["total"] == 0
    assert client.delete(f"/api/equipment/categories/{gas['id']}", headers=admin).status_code == 409  # still holds equipment
    client.put(f"{A}/{item['id']}", json={"category_id": None}, headers=admin)
    assert client.delete(f"/api/equipment/categories/{gas['id']}", headers=admin).status_code == 204


# ---------- Navixy ----------

def test_simulator_vehicles_are_labelled_and_a_preview_saves_nothing(world, client, db):
    admin = world.auth("admin1")
    assert sync(client, admin).status_code == 409  # not set up
    picker = client.get("/api/forms/fleet/vehicles", headers=world.auth("worker1")).json()
    assert picker["items"] == [] and picker["connection"] == "not_connected" and picker["manual_entry_allowed"] is True and "Type the vehicle in" in picker["message"]
    client.put(f"{I}/navixy", json={"mode": "simulator"}, headers=admin)
    preview = sync(client, admin, dry_run=True).json()
    assert preview["status"] == "previewed" and preview["simulated"] is True and preview["counts"]["created"] == 5
    assert db.scalars(select(Asset)).all() == [] and client.get(f"{N}/status", headers=admin).json()["last_synced_at"] is None
    done = sync(client, admin).json()
    assert done["status"] == "committed" and done["counts"]["created"] == 5
    assets = db.scalars(select(Asset).order_by(Asset.name)).all()
    assert {a.source_system for a in assets} == {"simulator"} and all(a.name.startswith("Sim ") for a in assets)
    listed = client.get(A, headers=admin).json()["items"]
    assert all(a["simulated"] and a["source_label"] == "Simulator (not real)" for a in listed)
    excavator = next(a for a in listed if a["name"] == "Sim Excavator 03")
    assert excavator["odometer_km"] is None and excavator["engine_hours"] == 2840.5 and excavator["tags"] == ["Sim Civil"]
    assert sync(client, admin).json()["counts"]["unchanged"] == 5  # running it again adds nothing
    picker = client.get("/api/forms/fleet/vehicles", headers=world.auth("worker1")).json()
    assert picker["connection"] == "simulator" and len(picker["items"]) == 5 and picker["items"][0]["source"] == "simulator"
    assert client.get("/api/forms/fleet/vehicles", headers=world.auth("worker2")).json()["items"] == []  # the other organisation sees none of them
    status = client.get(f"{N}/status", headers=world.auth("super1")).json()
    assert status["simulated"] is True and status["last_synced_at"] and status["can_sync"] is False


def test_live_sync_matches_on_tracker_id_keeps_hand_edits_and_respects_tags(world, client, db, navixy_api):
    admin, hseq, sup = world.auth("admin1"), world.auth("hseq1"), world.auth("super1")
    client.put(f"{I}/navixy", json={"settings": {"api_base_url": BASE, "email": "fleet@one.example"}, "secrets": {"password": PASSWORD}, "mode": "live"}, headers=admin)
    untested = sync(client, admin)
    assert untested.status_code == 409 and "Test the Navixy connection" in untested.json()["detail"]
    connect_live(client, admin)
    assert sync(client, sup).status_code == 403 and sync(client, world.auth("worker1")).status_code == 403
    by_hand = add(client, admin, name="Tipper 02")  # same name as a tracker: never merged by name
    s = navixy_api.state
    s["trackers"] = [tracker(101, "Ute 01", [5]), tracker(102, "Tipper 02", [5]), tracker(103, "Arrow board", [6]), {"id": 104}, tracker(105, "  ")]
    s["states"] = {"101": {"gps": {"location": {"lat": -41.43, "lng": 147.14}, "speed": 0, "updated": "2026-11-16 01:02:03"}, "last_update": "2026-11-16 01:02:03", "connection_status": "active"}}
    s["odometer"], s["hours"] = {"101": 50210.4, "102": 0}, {"102": 812.0}
    done = sync(client, hseq).json()
    assert done["status"] == "committed" and done["simulated"] is False and done["warnings"] == []
    assert done["counts"]["listed"] == 3 and done["counts"]["created"] == 3
    assets = {a.source_id: a for a in db.scalars(select(Asset).where(Asset.source_system == "navixy")).all()}
    assert set(assets) == {"101", "102", "103"} and assets["102"].id != by_hand["id"]
    ute = client.get(f"{A}/{assets['101'].id}", headers=sup).json()
    assert ute["source_label"] == "Navixy" and ute["tags"] == ["Civil"] and ute["odometer_km"] == 50210.4 and ute["engine_hours"] is None
    assert ute["tracker"]["lat"] == -41.43 and ute["tracker"]["last_seen_at"].startswith("2026-11-16T01:02:03")
    assert client.get(f"{A}/{assets['103'].id}", headers=sup).json()["tracker"] is None  # no reading is not a zero reading
    assert client.get(f"{N}/status", headers=admin).json()["tags"] == [{"id": "5", "name": "Civil"}, {"id": "6", "name": "Traffic"}]
    hashed = [json.loads(c.content)["hash"] for c in navixy_api.calls if c.url.path.endswith("tracker/list")]
    assert hashed and all(h.startswith("hash-") for h in hashed)

    # A name changed here is kept; a kind chosen here is kept; the tracker's new label does not overwrite it.
    client.put(f"{A}/{assets['101'].id}", json={"name": "Ute 01 (Dave)", "kind": "plant", "registration": "abc123"}, headers=admin)
    s["trackers"][0] = tracker(101, "UTE-01 renamed in Navixy", [5, 6])
    s["trackers"][1] = tracker(102, "Tipper 02B", [5])
    again = sync(client, admin).json()
    assert again["counts"]["kept_local"] == 1 and again["counts"]["updated"] == 2 and again["counts"]["created"] == 0
    db.expire_all()
    assert assets["101"].name == "Ute 01 (Dave)" and assets["101"].kind == "plant" and assets["101"].source_groups == ["Civil", "Traffic"]
    assert assets["102"].name == "Tipper 02B"

    # Choosing tags: only those trackers feed the register; the others are flagged, never deleted.
    assert client.put(f"{N}/tags", json={"tag_ids": ["99"]}, headers=admin).status_code == 422
    assert client.put(f"{N}/tags", json={"tag_ids": ["6"]}, headers=sup).status_code == 403
    assert client.put(f"{N}/tags", json={"tag_ids": ["6"]}, headers=admin).json() == {"selected_tags": ["6"]}
    scoped = sync(client, admin).json()
    assert scoped["counts"]["in_scope"] == 2 and scoped["counts"]["not_listed"] == 1 and scoped["not_listed"] == [{"name": "Tipper 02B"}]
    db.expire_all()
    assert assets["102"].source_missing is True and assets["102"].deleted_at is None
    assert client.get(f"{A}/{assets['102'].id}", headers=admin).json()["not_listed_by_provider"] is True

    # Removed here on purpose: a later sync does not bring it back.
    client.put(f"{N}/tags", json={"tag_ids": []}, headers=admin)
    client.delete(f"{A}/{assets['103'].id}", headers=admin)
    final = sync(client, admin).json()
    assert final["counts"]["removed_here"] == 1 and final["counts"]["created"] == 0
    assert client.get(A, params={"source": "navixy"}, headers=admin).json()["total"] == 2


def test_expired_sign_in_is_refreshed_once_and_an_outage_changes_nothing(world, client, db, navixy_api):
    admin = world.auth("admin1")
    connect_live(client, admin)
    s = navixy_api.state
    s["trackers"] = [tracker(101, "Ute 01")]
    s["odometer"] = {"101": 1000}
    assert sync(client, admin).json()["counts"]["created"] == 1
    asset = db.scalars(select(Asset)).one()

    s["hash"] = "rotated-by-navixy"  # the stored key no longer works
    auths = s["auths"]
    assert sync(client, admin).json()["status"] == "committed"
    assert s["auths"] == auths + 1
    db.expire_all()
    stored = decrypt_json(db.scalars(select(IntegrationConfig).where(IntegrationConfig.kind == "navixy")).one().secrets)
    assert stored["session_hash"] == s["hash"] and stored["password"] == PASSWORD

    s["always_invalid"] = True  # Navixy refuses even a fresh key: one more sign-in, then stop
    auths = s["auths"]
    refused = sync(client, admin).json()
    assert refused["status"] == "failed" and "fresh sign-in" in refused["error"] and s["auths"] == auths + 1
    s["always_invalid"] = False

    s["down"] = True
    failed = sync(client, admin).json()
    assert failed["status"] == "failed" and "could not be reached" in failed["error"] and PASSWORD not in json.dumps(failed)
    s["down"] = False
    db.expire_all()
    assert asset.source_missing is False and asset.deleted_at is None and asset.odometer_km == 1000
    status = client.get(f"{N}/status", headers=admin).json()
    assert status["last_error"] is None or "could not" in status["last_error"]
    assert status["last_synced_at"] is not None

    # A lower reading from the tracker is kept as evidence and flagged, not applied.
    s["odometer"] = {"101": 400}
    low = sync(client, admin).json()
    assert low["counts"]["meter_review"] == 1
    detail = client.get(f"{A}/{asset.id}", headers=admin).json()
    assert detail["odometer_km"] == 1000 and detail["meter_review"]["readings"][0] == {"meter": "odometer_km", "on_file": 1000, "incoming": 400}
    readings = db.scalars(select(AssetMeterReading).where(AssetMeterReading.asset_id == asset.id)).all()
    assert sorted((r.odometer_km, r.applied) for r in readings) == [(400, False), (1000, True)]
    assert client.post(f"{A}/{asset.id}/meter/dismiss-review", headers=admin).json()["meter_review"] is None

    # An account without meter readings still syncs; it says so and leaves meters alone.
    s["counters"] = False
    limited = sync(client, admin).json()
    assert limited["status"] == "committed" and any("odometer" in w for w in limited["warnings"])
    db.expire_all()
    assert asset.odometer_km == 1000


def test_form_vehicle_answers_accept_register_and_hand_entry():
    from app.forms import _check
    field = {"id": "v", "type": "navixy_vehicle", "label": "Vehicle", "config": {}}
    assert _check(field, {"label": "Ute 01", "asset_id": "a1", "source": "register"}, set()) == {"label": "Ute 01", "source": "register", "asset_id": "a1"}
    assert _check(field, {"label": "Hire truck", "source": "manual"}, set()) == {"label": "Hire truck", "source": "manual"}
    with pytest.raises(ValueError):
        _check(field, {"label": "Ute 01", "source": "navixy"}, set())  # chosen from the list but with no reference
