"""Dashboard figures and search across registers (stage 9a)."""
import datetime as dt

from app.people import today_for
from app.routers import overview, workers as workers_router

from .test_safety import HAZARD, answers


def tiles(client, headers):
    body = client.get("/api/dashboard/metrics", headers=headers).json()
    return {t["key"]: t for t in body["tiles"]}, body


def test_dashboard_figures_match_their_registers_and_follow_permissions(world, client, db):
    admin, worker, sup, other = world.auth("admin1"), world.auth("worker1"), world.auth("super1"), world.auth("admin2")
    today = today_for(world.org1)
    tom = world.add_worker("Tom", "Okafor", link="worker1")
    mia = world.add_worker("Mia", "Tran")
    theirs = world.add_worker("Zed", "Other", org=world.org2)
    client.post(f"/api/workers/{tom.id}/certifications", json={"kind": "first_aid", "expiry_date": (today - dt.timedelta(days=3)).isoformat()}, headers=admin)
    client.post(f"/api/workers/{mia.id}/certifications", json={"kind": "first_aid", "expiry_date": (today + dt.timedelta(days=5)).isoformat()}, headers=admin)
    client.post(f"/api/workers/{mia.id}/certifications", json={"kind": "white_card"}, headers=admin)
    client.post(f"/api/workers/{theirs.id}/certifications", json={"kind": "first_aid", "expiry_date": (today - dt.timedelta(days=9)).isoformat()}, headers=other)
    client.post("/api/hazards", headers=worker, json={"fields": answers(**HAZARD)})
    client.post("/api/hazards", headers=sup, json={"fields": answers(**{**HAZARD, "title": "Loose plate"})})

    got, body = tiles(client, admin)
    assert body["unavailable"] == [] and body["today"] == today.isoformat()
    assert (got["certs_expired"]["count"], got["certs_expiring"]["count"], got["certs_unknown"]["count"]) == (1, 1, 1)
    # The same number the register itself reports, and a link that opens the same filter.
    register = client.get("/api/certifications/expiry-count", headers=admin).json()
    assert (register["expired"], register["expiring_soon"], register["unknown"]) == (1, 1, 1)
    assert got["certs_expired"]["path"] == "/app/certifications?status=expired" and "expiry date has passed" in got["certs_expired"]["definition"]
    assert got["hazard_open"]["count"] == 2 == client.get("/api/hazards?status=open", headers=admin).json()["total"]
    assert got["workers_active"]["count"] == 2 and got["leave_waiting"]["count"] == 0 and got["outbox_failed"]["count"] == 0
    assert [t["key"] for t in body["attention"]][0] == "certs_expired"  # the worst first
    assert all(t["count"] for t in body["attention"]) and "workers_active" not in [t["key"] for t in body["attention"]]
    assert body["recent_activity"] and all(set(r) == {"at", "who", "action", "target_type"} for r in body["recent_activity"])
    assert "timesheets_waiting" not in got  # payroll is not opened by being an administrator

    # A worker sees only their own records counted, and no office figures or activity.
    got, body = tiles(client, worker)
    assert got["hazard_open"]["count"] == 1 and "that you created" in got["hazard_open"]["definition"]
    assert got["certs_expired"]["count"] == 1 and got["certs_expiring"]["count"] == 0 and "your own record" in got["certs_expired"]["definition"]
    assert not {"workers_active", "leave_waiting", "outbox_failed", "service_overdue", "renewals_ready"} & set(got) and body["recent_activity"] is None
    # The payroll owner sees the timesheet figure; another organisation sees none of this.
    assert tiles(client, world.auth("owner1"))[0]["timesheets_waiting"]["count"] == 0
    got, _ = tiles(client, other)
    assert got["certs_expired"]["count"] == 1 and got["hazard_open"]["count"] == 0 and got["workers_active"]["count"] == 1
    assert client.get("/api/dashboard/metrics").status_code == 401


def test_one_figure_failing_is_reported_as_unavailable_not_zero(world, client, monkeypatch):
    def broken(**_):
        raise RuntimeError("boom")

    monkeypatch.setattr(workers_router, "expiry_count", broken)
    got, body = tiles(client, world.auth("admin1"))
    assert got["certs_expired"]["count"] is None and got["certs_expired"]["available"] is False
    assert set(body["unavailable"]) == {"certs_expired", "certs_expiring", "certs_unknown"}
    assert got["workers_active"]["available"] is True and got["hazard_open"]["count"] == 0  # the rest still answer
    assert "boom" not in client.get("/api/dashboard/metrics", headers=world.auth("admin1")).text


def test_search_finds_only_what_the_caller_may_open(world, client, db):
    admin, worker, sup, other = world.auth("admin1"), world.auth("worker1"), world.auth("super1"), world.auth("admin2")
    tom = world.add_worker("Tom", "Bridgewater", link="worker1")
    world.add_worker("Mia", "Bridges")
    world.add_worker("Zed", "Bridgeman", org=world.org2)
    site = client.post("/api/sites", json={"name": "Bridge Rd upgrade", "address": "1 Bridge Rd"}, headers=admin).json()
    client.post("/api/suppliers", json={"name": "Bridgestone Hire"}, headers=admin)
    client.post("/api/assets", json={"kind": "vehicle", "name": "Hilux 7", "registration": "BRG123"}, headers=admin)
    mine = client.post("/api/hazards", headers=worker, json={"fields": answers(**{**HAZARD, "title": "Bridge deck edge"})}).json()
    client.post("/api/hazards", headers=sup, json={"fields": answers(**{**HAZARD, "title": "Bridge scaffold"})})
    client.post("/api/sites", json={"name": "Bridge St depot"}, headers=other)

    def find(headers, q="bridg", **params):
        body = client.get("/api/search", params={"q": q, **params}, headers=headers).json()
        return {g["kind"]: [i["title"] for i in g["items"]] for g in body["groups"]}, body

    got, body = find(admin)
    assert got["workers"] == ["Mia Bridges", "Tom Bridgewater"] or sorted(got["workers"]) == ["Mia Bridges", "Tom Bridgewater"]
    assert got["sites"] == ["Bridge Rd upgrade"] and got["suppliers"] == ["Bridgestone Hire"] and sorted(got["hazard"]) == ["Bridge deck edge", "Bridge scaffold"]
    assert body["failed"] == [] and "does not read inside files" in body["note"]
    paths = {i["path"] for g in body["groups"] for i in g["items"]}
    assert f"/app/sites/{site['id']}" in paths and f"/app/r/hazards/{mine['id']}" in paths and f"/app/workers/{tom.id}" in paths
    assert find(admin, q="BRG1")[0] == {"assets": ["Hilux 7"]}
    assert find(admin, kinds="sites,hazard")[0].keys() == {"sites", "hazard"}

    # A worker: their own worker record and their own hazard, no suppliers, fleet or users.
    got, body = find(worker)
    assert got.get("workers") == ["Tom Bridgewater"] and got["hazard"] == ["Bridge deck edge"]
    assert not {"suppliers", "assets", "users"} & set(got) and "Suppliers" not in body["searched"]
    assert find(worker, kinds="suppliers,users")[0] == {}  # asking for a register you cannot see returns nothing, not an error
    # Another organisation finds only its own.
    got, _ = find(other)
    assert got == {"workers": ["Zed Bridgeman"], "sites": ["Bridge St depot"]}
    assert client.get("/api/search", params={"q": "b"}, headers=admin).status_code == 422
    assert client.get("/api/search", params={"q": "bridge"}).status_code == 401
    assert find(admin, q="%%")[0] == {} and find(admin, q="_%_")[0] == {}  # pattern characters alone do not list everything


def test_search_survives_one_register_failing(world, client, monkeypatch):
    world.add_worker("Mia", "Bridges")
    client.post("/api/sites", json={"name": "Bridge Rd upgrade"}, headers=world.auth("admin1"))

    def broken(**_):
        raise RuntimeError("boom")

    monkeypatch.setattr(overview.sites, "list_sites", broken)
    body = client.get("/api/search", params={"q": "bridg"}, headers=world.auth("admin1")).json()
    assert body["failed"] == ["Sites"] and [g["kind"] for g in body["groups"]] == ["workers"]
