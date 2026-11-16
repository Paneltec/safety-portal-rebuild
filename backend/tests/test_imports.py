"""Simpro import of workers, sites and suppliers (stage 5b)."""
import httpx
import pytest
from sqlalchemy import select

from app import integrations, simpro
from app.models import AuditLog, ImportReviewItem, IntegrationConfig, Site, Supplier, User, Worker

I = "/api/integrations"
S = f"{I}/simpro"
TOKEN = "simpro-token-ABC123456"
BASE = "https://acme.simprosuite.com"


@pytest.fixture
def simpro_api(monkeypatch):
    """A stand-in Simpro. `data[(company, path)]` is the full list; it is served in pages."""
    data: dict[tuple[str, str], list[dict]] = {}
    calls: list[httpx.Request] = []
    fail: dict[str, httpx.Response] = {}
    throttle = {"left": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/v1.0/companies/":
            return httpx.Response(200, json=[{"ID": 0, "Name": "Co Zero"}, {"ID": 2, "Name": "Co Two"}])
        _, _, _, _, company, resource, *_ = path.split("/")
        if company in fail:
            return fail[company]
        if throttle["left"] > 0:
            throttle["left"] -= 1
            return httpx.Response(429)
        rows = data.get((company, resource), [])
        size, page = int(request.url.params["pageSize"]), int(request.url.params["page"])
        pages = max(1, -(-len(rows) // size))
        return httpx.Response(200, json=rows[(page - 1) * size: page * size], headers={"Result-Pages": str(pages), "Result-Total": str(len(rows))})

    monkeypatch.setattr(integrations, "transport", httpx.MockTransport(handler))
    monkeypatch.setattr(simpro, "sleep", lambda seconds: None)
    return type("Api", (), {"data": data, "calls": calls, "fail": fail, "throttle": throttle})


def connect_live(client, headers, companies=("0",)):
    saved = client.put(f"{I}/simpro", json={"settings": {"api_base_url": BASE, "company_ids": list(companies)}, "secrets": {"api_token": TOKEN}, "mode": "live"}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert client.post(f"{I}/simpro/test-connection", headers=headers).json()["ok"] is True


def sync(client, headers, resource, dry_run=False):
    return client.post(f"{S}/sync", json={"resource": resource, "dry_run": dry_run}, headers=headers)


def employee(i, name, **extra):
    return {"ID": i, "Name": name, "Position": extra.pop("position", "Labourer"), "Archived": extra.pop("archived", False),
            "PrimaryContact": {"Email": extra.pop("email", None), "CellPhone": extra.pop("mobile", None), "WorkPhone": None}}


# ---------- preconditions and permissions ----------

def test_import_needs_a_working_connection_and_the_right_permissions(world, client, simpro_api):
    admin, hseq, auditor = world.auth("admin1"), world.auth("hseq1"), world.auth("auditor1")
    assert sync(client, admin, "workers").status_code == 409  # not set up
    client.put(f"{I}/simpro", json={"settings": {"api_base_url": BASE}, "secrets": {"api_token": TOKEN}, "mode": "live"}, headers=admin)
    untested = sync(client, admin, "workers")
    assert untested.status_code == 409 and "Test the Simpro connection" in untested.json()["detail"]
    connect_live(client, admin)
    assert sync(client, auditor, "workers").status_code == 403  # view only
    assert sync(client, world.auth("worker1"), "workers").status_code == 403
    # An HSEQ lead can edit both connections and workers, so may run it.
    assert sync(client, hseq, "workers", dry_run=True).status_code == 200
    assert client.post(f"{S}/sync", json={"resource": "payroll"}, headers=admin).status_code == 422
    assert client.get(f"{S}/runs", headers=world.auth("worker1")).status_code == 403
    assert client.get(f"{S}/companies", headers=auditor).json() == {
        "selected": ["0"], "found": [{"id": "0", "name": "Co Zero"}, {"id": "2", "name": "Co Two"}], "missing": []}


# ---------- workers ----------

def test_preview_saves_nothing_then_import_creates_by_source_key(world, client, db, simpro_api):
    admin = world.auth("admin1")
    connect_live(client, admin)
    simpro_api.data[("0", "employees")] = [
        employee(11, "Mia Tanaka", email="Mia@One.Example", mobile="0400 111 222", position="Leading hand"),
        employee(12, "Ben Abbott", archived=True),
        {"ID": 13},  # no name: skipped, reported
        employee(11, "Mia Tanaka"),  # listed twice
    ]
    preview = sync(client, admin, "workers", dry_run=True).json()
    assert preview["status"] == "previewed" and preview["dry_run"] is True
    assert preview["counts"] == {"created": 2, "updated": 0, "unchanged": 0, "kept_local": 0, "needs_review": 0, "skipped": 2}
    assert [c["name"] for c in preview["created"]] == ["Mia Tanaka", "Ben Abbott"]
    assert db.scalars(select(Worker)).all() == [] and client.get(f"{S}/last-synced", headers=admin).json()["resources"]["workers"] is None
    assert client.get(f"{I}/simpro", headers=admin).json()["last_sync_at"] is None  # a preview is not a sync

    done = sync(client, admin, "workers").json()
    assert done["status"] == "committed" and done["counts"]["created"] == 2 and done["simulated"] is False
    assert done["companies"] == [{"company_id": "0", "ok": True, "fetched": 4, "error": None}]
    request = next(c for c in simpro_api.calls if "/employees/" in c.url.path)
    assert request.headers["authorization"] == f"Bearer {TOKEN}" and request.url.params["pageSize"] == "250"
    rows = {w.source_id: w for w in db.scalars(select(Worker)).all()}
    mia = rows["11"]
    assert (mia.first_name, mia.last_name, mia.email, mia.mobile, mia.position, mia.active) == ("Mia", "Tanaka", "mia@one.example", "0400 111 222", "Leading hand", True)
    assert (mia.source_system, mia.source_company) == ("simpro", "0") and rows["12"].active is False  # archived in Simpro: inactive here
    # Importing never creates a login.
    assert db.scalars(select(User).where(User.email == "mia@one.example")).first() is None
    assert client.get(f"{I}/simpro", headers=admin).json()["last_sync_at"] is not None
    shown = client.get(f"/api/workers/{mia.id}", headers=admin).json()
    assert shown["source"] == {"system": "simpro", "company": "0", "id": "11"}
    assert TOKEN not in str([a.detail for a in db.scalars(select(AuditLog)).all()])

    # Running it again changes nothing and creates no duplicates.
    again = sync(client, admin, "workers").json()
    assert again["counts"]["created"] == 0 and again["counts"]["unchanged"] == 2
    assert len(db.scalars(select(Worker)).all()) == 2


def test_hand_edits_survive_reimport_and_other_changes_come_through(world, client, db, simpro_api):
    admin = world.auth("admin1")
    connect_live(client, admin)
    simpro_api.data[("0", "employees")] = [employee(11, "Mia Tanaka", mobile="0400 111 222", position="Labourer")]
    sync(client, admin, "workers")
    mia = db.scalars(select(Worker)).one()
    edited = client.patch(f"/api/workers/{mia.id}", json={"mobile": "0499 999 999", "suburb": "Invermay"}, headers=admin).json()
    assert edited["locally_edited"] == ["mobile"]  # only imported fields are tracked
    simpro_api.data[("0", "employees")] = [employee(11, "Mia Tanaka-Lee", mobile="0400 000 000", position="Leading hand")]
    result = sync(client, admin, "workers").json()
    assert result["counts"] == {"created": 0, "updated": 1, "unchanged": 0, "kept_local": 1, "needs_review": 0, "skipped": 0}
    assert result["updated"][0]["fields"] == ["last_name", "position"]
    db.expire_all()
    mia = db.get(Worker, mia.id)
    assert (mia.last_name, mia.position, mia.mobile) == ("Tanaka-Lee", "Leading hand", "0499 999 999")
    assert mia.details["suburb"] == "Invermay"  # local-only details are never touched
    # A worker deleted here is not resurrected by the import.
    client.delete(f"/api/workers/{mia.id}", headers=admin)
    skipped = sync(client, admin, "workers").json()
    assert skipped["counts"]["created"] == 0 and skipped["skipped"][0]["reason"] == "deleted in the portal"


def test_lookalikes_go_to_review_and_are_never_merged_automatically(world, client, db, simpro_api):
    admin = world.auth("admin1")
    connect_live(client, admin)
    by_email = world.add_worker("Mia", "T", email="mia@one.example", position="Supervisor", mobile="0400 000 001")
    by_name = world.add_worker("Ben", "Abbott")
    world.add_worker("Mia", "Tanaka", org=world.org2, email="mia@one.example")  # another organisation is never a candidate
    simpro_api.data[("0", "employees")] = [employee(11, "Mia Tanaka", email="mia@one.example", position="Leading hand", mobile="0400 111 222"),
                                           employee(12, "ben abbott"), employee(13, "Cara Diaz")]
    result = sync(client, admin, "workers").json()
    assert result["counts"]["created"] == 1 and result["counts"]["needs_review"] == 2
    assert {(r["name"], r["match_on"]) for r in result["review"]} == {("Mia Tanaka", "email"), ("ben abbott", "name")}
    assert len(db.scalars(select(Worker).where(Worker.org_id == world.org1.id)).all()) == 3  # the two on file plus Cara
    assert sync(client, admin, "workers").json()["counts"]["needs_review"] == 2  # still waiting; not duplicated in the queue
    queue = client.get(f"{S}/review", headers=admin).json()["items"]
    assert len(queue) == 2 and client.get(f"{S}/last-synced", headers=admin).json()["open_review"] == 2
    mia_item = next(q for q in queue if q["match_on"] == "email")
    assert mia_item["candidate"]["id"] == by_email.id and mia_item["candidate"]["position"] == "Supervisor" and mia_item["incoming"]["position"] == "Leading hand"

    # Link: the record on file takes the Simpro identity and keeps its own values where they differ.
    assert client.post(f"{S}/review/{mia_item['id']}/resolve", json={"action": "link"}, headers=world.auth("auditor1")).status_code == 403
    assert client.post(f"{S}/review/{mia_item['id']}/resolve", json={"action": "link"}, headers=world.auth("admin2")).status_code == 404
    linked = client.post(f"{S}/review/{mia_item['id']}/resolve", json={"action": "link"}, headers=admin).json()
    assert linked["status"] == "linked" and linked["result_id"] == by_email.id
    db.expire_all()
    mia = db.get(Worker, by_email.id)
    assert (mia.source_system, mia.source_id, mia.last_name, mia.position, mia.mobile) == ("simpro", "11", "T", "Supervisor", "0400 000 001")
    assert set(mia.locally_edited) == {"last_name", "position", "mobile"}
    assert client.post(f"{S}/review/{mia_item['id']}/resolve", json={"action": "ignore"}, headers=admin).status_code == 409  # already answered

    # Create: a different person with the same name.
    ben_item = next(q for q in queue if q["match_on"] == "name")
    made = client.post(f"{S}/review/{ben_item['id']}/resolve", json={"action": "create"}, headers=admin).json()
    assert made["status"] == "created" and made["result_id"] != by_name.id
    assert db.get(Worker, by_name.id).source_system is None
    final = sync(client, admin, "workers").json()
    assert final["counts"] == {"created": 0, "updated": 0, "unchanged": 3, "kept_local": 3, "needs_review": 0, "skipped": 0}
    assert len(db.scalars(select(Worker).where(Worker.org_id == world.org1.id)).all()) == 4


def test_ignored_review_items_stay_out(world, client, db, simpro_api):
    admin = world.auth("admin1")
    connect_live(client, admin)
    world.add_worker("Ben", "Abbott")
    simpro_api.data[("0", "employees")] = [employee(12, "Ben Abbott")]
    sync(client, admin, "workers")
    item = client.get(f"{S}/review", headers=admin).json()["items"][0]
    assert client.post(f"{S}/review/{item['id']}/resolve", json={"action": "ignore"}, headers=admin).json()["status"] == "ignored"
    result = sync(client, admin, "workers").json()
    assert result["counts"]["needs_review"] == 0 and result["skipped"][0]["reason"] == "ignored in the review queue"
    assert len(db.scalars(select(Worker)).all()) == 1 and client.get(f"{S}/review?status=ignored", headers=admin).json()["items"][0]["id"] == item["id"]


# ---------- companies, pages and limits ----------

def test_one_company_failing_is_reported_and_the_other_still_imports(world, client, db, simpro_api):
    admin = world.auth("admin1")
    connect_live(client, admin, companies=("0", "2"))
    simpro_api.data[("0", "employees")] = [employee(11, "Mia Tanaka")]
    simpro_api.data[("2", "employees")] = [employee(11, "Other Person")]  # same Simpro id, different company: a different person
    simpro_api.fail["2"] = httpx.Response(403)
    partial = sync(client, admin, "workers").json()
    assert partial["status"] == "partial" and partial["counts"]["created"] == 1
    assert partial["companies"][1]["ok"] is False and "rejected these details (HTTP 403)" in partial["companies"][1]["error"]
    assert client.get(f"{S}/last-synced", headers=admin).json()["resources"]["workers"]["status"] == "partial"
    simpro_api.fail.clear()
    assert sync(client, admin, "workers").json()["status"] == "committed"
    assert {(w.source_company, w.source_id, w.first_name) for w in db.scalars(select(Worker)).all()} == {("0", "11", "Mia"), ("2", "11", "Other")}
    # Every company failing saves nothing and does not move "last synced".
    simpro_api.fail.update({"0": httpx.Response(500), "2": httpx.Response(500)})
    before = client.get(f"{I}/simpro", headers=admin).json()["last_sync_at"]
    failed = sync(client, admin, "workers").json()
    assert failed["status"] == "failed" and "company 0" in failed["error"]
    assert client.get(f"{I}/simpro", headers=admin).json()["last_sync_at"] == before


def test_pages_are_followed_and_rate_limits_retried(world, client, db, simpro_api, monkeypatch):
    admin = world.auth("admin1")
    connect_live(client, admin)
    monkeypatch.setattr(simpro, "PAGE_SIZE", 2)
    simpro_api.data[("0", "sites")] = [{"ID": i, "Name": f"Site {i}", "Address": {"Address": f"{i} Road", "City": "Perth", "State": "TAS", "PostalCode": "7300"}} for i in range(1, 6)]
    simpro_api.throttle["left"] = 2  # two "too many requests" answers, then it works
    done = sync(client, admin, "sites").json()
    assert done["status"] == "committed" and done["counts"]["created"] == 5 and done["companies"][0]["fetched"] == 5
    pages = [c.url.params["page"] for c in simpro_api.calls if "/sites/" in c.url.path]
    assert pages == ["1", "1", "1", "2", "3"]
    site = db.scalars(select(Site).where(Site.source_id == "3")).one()
    assert (site.name, site.address, site.suburb, site.postcode) == ("Site 3", "3 Road", "Perth", "7300") and len(site.scan_token) >= 24
    # Limited every time: the import stops and says so rather than looping.
    simpro_api.throttle["left"] = 99
    stuck = sync(client, admin, "sites").json()
    assert stuck["status"] == "failed" and "called too often" in stuck["error"]


# ---------- sites and suppliers ----------

def test_sites_and_suppliers_import_with_hand_edits_and_review(world, client, db, simpro_api):
    admin = world.auth("admin1")
    connect_live(client, admin)
    on_file = client.post("/api/suppliers", json={"name": "Acme Quarry", "abn": "51 824 753 556", "is_contractor": True}, headers=admin).json()
    simpro_api.data[("0", "vendors")] = [
        {"ID": 501, "Name": "ACME QUARRY PTY LTD", "ABN": "51 824 753 556", "Phone": "03 6300 0000", "Email": "Accounts@Acme.Example", "Address": {"City": "Launceston"}},
        {"ID": 502, "Name": "Beta Hire", "Phone": "03 6300 1111"},
    ]
    result = sync(client, admin, "suppliers").json()
    assert result["counts"]["created"] == 1 and result["review"] == [{"name": "ACME QUARRY PTY LTD", "match_on": "abn", "candidate_id": on_file["id"]}]
    item = client.get(f"{S}/review", headers=admin).json()["items"][0]
    client.post(f"{S}/review/{item['id']}/resolve", json={"action": "link"}, headers=admin)
    linked = client.get(f"/api/suppliers/{on_file['id']}", headers=admin).json()
    assert linked["source"]["id"] == "501" and linked["name"] == "Acme Quarry" and linked["is_contractor"] is True  # local name and flag kept
    assert linked["contact_email"] == "accounts@acme.example" and linked["contact_phone"] == "03 6300 0000"  # blanks filled from Simpro
    beta = db.scalars(select(Supplier).where(Supplier.source_id == "502")).one()
    client.patch(f"/api/suppliers/{beta.id}", json={"contact_phone": "0400 222 333"}, headers=admin)
    client.post(f"/api/suppliers/{beta.id}/notes", json={"body": "Preferred for wet hire."}, headers=admin)
    simpro_api.data[("0", "vendors")][1] = {"ID": 502, "Name": "Beta Hire Tasmania", "Phone": "03 6300 9999"}
    again = sync(client, admin, "suppliers").json()
    assert again["counts"]["updated"] == 1 and again["counts"]["kept_local"] >= 1
    after = client.get(f"/api/suppliers/{beta.id}", headers=admin).json()
    assert after["name"] == "Beta Hire Tasmania" and after["contact_phone"] == "0400 222 333"
    assert len(client.get(f"/api/suppliers/{beta.id}/notes", headers=admin).json()["items"]) == 1  # notes untouched

    simpro_api.data[("0", "sites")] = [{"ID": 7, "Name": "Depot", "Address": {"Address": "1 Yard Rd", "City": "Invermay"}}]
    sync(client, admin, "sites")
    site = db.scalars(select(Site).where(Site.source_id == "7")).one()
    client.patch(f"/api/sites/{site.id}", json={"name": "Main depot"}, headers=admin)
    simpro_api.data[("0", "sites")] = [{"ID": 7, "Name": "Depot (old name)", "Address": {"Address": "2 Yard Rd", "City": "Invermay"}}]
    sync(client, admin, "sites")
    db.expire_all()
    site = db.get(Site, site.id)
    assert (site.name, site.address) == ("Main depot", "2 Yard Rd")
    # The other organisation has none of this.
    assert client.get("/api/suppliers", headers=world.auth("admin2")).json()["total"] == 0
    assert client.get(f"{S}/runs", headers=world.auth("admin2")).json()["items"] == []
    assert client.get(f"{S}/review", headers=world.auth("admin2")).json()["items"] == []


# ---------- simulator ----------

def test_simulator_import_is_labelled_and_separate_from_real_simpro(world, client, db, simpro_api):
    admin = world.auth("admin1")
    client.put(f"{I}/simpro", json={"mode": "simulator"}, headers=admin)
    done = sync(client, admin, "workers").json()
    assert done["simulated"] is True and done["mode"] == "simulator" and done["counts"]["created"] == 4
    assert simpro_api.calls == []  # nothing left the server
    rows = db.scalars(select(Worker)).all()
    assert {w.source_system for w in rows} == {"simulator"} and all(w.first_name.startswith("Sim ") for w in rows)
    assert all((w.email or "").endswith("@simulator.invalid") for w in rows)
    shown = client.get(f"/api/workers/{rows[0].id}", headers=admin).json()
    assert shown["source"]["system"] == "simulator"
    assert sync(client, admin, "workers").json()["counts"] == {"created": 0, "updated": 0, "unchanged": 4, "kept_local": 0, "needs_review": 0, "skipped": 0}
    assert sync(client, admin, "sites").json()["counts"]["created"] == 3 and sync(client, admin, "suppliers").json()["counts"]["created"] == 3
    runs = client.get(f"{S}/runs", headers=admin).json()["items"]
    assert all(r["simulated"] for r in runs) and runs[0]["started_by"] == "Alex Rowe"
    # Going live later does not adopt simulator records as real ones: the same id is a different source.
    connect_live(client, admin)
    simpro_api.data[("0", "employees")] = [employee("0-E1", "Real Person")]
    live = sync(client, admin, "workers").json()
    assert live["simulated"] is False and live["counts"]["created"] == 1
    assert {w.source_system for w in db.scalars(select(Worker)).all()} == {"simulator", "simpro"}
    assert db.scalars(select(ImportReviewItem)).all() == [] and db.scalars(select(IntegrationConfig)).one().mode == "live"
