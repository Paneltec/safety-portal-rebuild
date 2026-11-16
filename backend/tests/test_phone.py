"""Phone module allocation and the phone home (stage 8a)."""
import datetime as dt

from sqlalchemy import select

from app.models import AuditLog, PhoneModuleSetting
from app.people import today_for

S = "/api/settings/mobile-modules"


def shown(client, headers):
    return set(client.get("/api/me/mobile-modules", headers=headers).json()["shown"])


def test_defaults_by_kind_of_role_and_permissions_still_decide_access(world, client):
    worker, sup, admin = world.auth("worker1"), world.auth("super1"), world.auth("admin1")
    w = client.get("/api/me/mobile-modules", headers=worker).json()
    assert w["bucket"] == "worker" and {"sign_on", "forms", "leave", "timesheets", "payslips", "profile", "daily_jobs"} <= set(w["shown"])
    assert "plant_vehicles" not in w["shown"] and "workers" not in w["shown"] and "ask_intel" not in w["shown"]
    assert w["flat"]["forms"] is True and w["flat"]["document_library"] is False
    s = {m["key"]: m for m in client.get("/api/me/mobile-modules", headers=sup).json()["modules"]}
    assert s["plant_vehicles"]["shown"] is True and s["workers"]["shown"] is True
    # Allocated but not built: says so, and is not shown.
    assert (s["ask_intel"]["allocated"], s["ask_intel"]["shown"], s["ask_intel"]["why_not"]) == (True, False, "Not built in this version")
    # Allocated by default to supervisors, but this supervisor's permissions do not include it: not shown.
    assert (s["suppliers"]["allocated"], s["suppliers"]["permitted"]) == (True, s["suppliers"]["shown"])
    a = client.get("/api/me/mobile-modules", headers=admin).json()
    assert a["bucket"] == "admin" and "users_directory" in a["shown"]
    assert client.get("/api/me/mobile-modules").status_code == 401


def test_exceptions_for_a_role_or_a_person_survive_changes_to_the_defaults(world, client, db):
    admin, worker, sup, other = world.auth("admin1"), world.auth("worker1"), world.auth("super1"), world.auth("admin2")
    for headers in (worker, sup, world.auth("hseq1")):
        assert client.get(S, headers=headers).status_code == 403 and client.put(S, json={"buckets": {}}, headers=headers).status_code == 403
        assert client.patch(f"{S}/overrides", json={"user_id": world.id("worker1"), "module_key": "forms", "value": False}, headers=headers).status_code == 403
    patch = lambda **body: client.patch(f"{S}/overrides", json=body, headers=admin)  # noqa: E731
    assert patch(user_id=world.id("worker1"), module_key="nope", value=True).status_code == 422
    assert patch(module_key="forms", value=True).status_code == 422 and patch(user_id=world.id("worker1"), role_id="worker", module_key="forms", value=True).status_code == 422
    assert patch(user_id=world.id("worker2"), module_key="forms", value=False).status_code == 404  # another organisation's person
    assert patch(role_id="no-such-role", module_key="forms", value=False).status_code == 404

    # One worker loses Forms; the worker role gains the document library entry (shown only if permitted).
    assert patch(user_id=world.id("worker1"), module_key="forms", value=False).status_code == 200
    assert patch(user_id=world.id("worker1"), module_key="site_diary", value=True).status_code == 200
    assert patch(role_id="supervisor", module_key="plant_vehicles", value=False).status_code == 200
    assert "forms" not in shown(client, worker) and "plant_vehicles" not in shown(client, sup)
    assert "forms" in shown(client, world.auth("worker2"))  # the other organisation is untouched

    # Changing the defaults: other workers follow; the exception for this worker stays.
    assert client.put(S, json={"buckets": {"worker": {"bananas": True}}}, headers=admin).status_code == 422
    changed = client.put(S, json={"buckets": {"worker": {"forms": True, "payslips": False, "site_diary": False}, "supervisor": {"plant_vehicles": True}}}, headers=admin).json()
    assert changed["changes"] == 2 and changed["bucket_settings"]["worker"]["payslips"] is False
    mine = {m["key"]: m for m in client.get("/api/me/mobile-modules", headers=worker).json()["modules"]}
    assert (mine["forms"]["allocated"], mine["forms"]["from"]) == (False, "person") and (mine["site_diary"]["allocated"], mine["site_diary"]["from"]) == (True, "person")
    assert (mine["payslips"]["allocated"], mine["payslips"]["from"]) == (False, "default")
    assert "plant_vehicles" not in shown(client, sup)  # the role's exception still wins over the new default
    listing = client.get(S, headers=admin).json()
    assert [(o["user"], o["modules"]) for o in listing["user_overrides"]] == [("Tom Okafor", {"forms": False, "site_diary": True})]
    assert [(o["role"], o["modules"]) for o in listing["role_overrides"]] == [("Supervisor", {"plant_vehicles": False})]
    assert client.get(S, headers=other).json()["user_overrides"] == []

    # Removing one exception, then all of them, returns to the defaults.
    assert patch(user_id=world.id("worker1"), module_key="forms", value=None).json()["modules"] == {"site_diary": True}
    assert "forms" in shown(client, worker)
    assert client.delete(f"{S}/overrides/user/{world.id('worker1')}", headers=other).status_code == 404
    assert client.delete(f"{S}/overrides/user/{world.id('worker1')}", headers=admin).status_code == 204
    assert client.delete(f"{S}/overrides/role/supervisor", headers=admin).status_code == 204 and "plant_vehicles" in shown(client, sup)
    assert db.scalars(select(PhoneModuleSetting).where(PhoneModuleSetting.scope != "bucket")).all() == []
    assert {a.action for a in db.scalars(select(AuditLog).where(AuditLog.action.like("phone_modules.%"))).all()} == {
        "phone_modules.defaults_changed", "phone_modules.exception_changed", "phone_modules.exceptions_cleared"}

    # Switching a module on never grants access: a worker given "Users" still cannot open it or call its API.
    patch(user_id=world.id("worker1"), module_key="users_directory", value=True)
    users = next(m for m in client.get("/api/me/mobile-modules", headers=worker).json()["modules"] if m["key"] == "users_directory")
    assert (users["allocated"], users["permitted"], users["shown"], users["why_not"]) == (True, False, False, "Their permissions do not include this")
    assert client.get("/api/users", headers=worker).status_code == 403


def test_preview_and_phone_home(world, client, db):
    admin, worker, sup = world.auth("admin1"), world.auth("worker1"), world.auth("super1")
    preview = client.get("/api/mobile/preview-user", params={"user_id": world.id("worker1")}, headers=admin).json()
    assert preview["user"]["name"] == "Tom Okafor" and preview["bucket_label"] == "Workers" and "forms" in preview["shown"]
    assert client.get("/api/mobile/preview-user", params={"user_id": world.id("worker1")}, headers=sup).status_code == 403
    assert client.get("/api/mobile/preview-user", params={"user_id": world.id("worker2")}, headers=admin).status_code == 404

    home = client.get("/api/mobile/home", headers=worker).json()
    assert home["user"] == {"name": "Tom Okafor", "first_name": "Tom", "linked_to_worker": False} and home["org"] == "Org One" and "today_job" not in home
    assert home["native_apps"]["android"] is None and "home screen" in home["native_apps"]["note"]
    tom = world.add_worker("Tom", "Okafor", link="worker1")
    today = today_for(world.org1)
    client.put(f"/api/me/payroll/timesheets/{(today - dt.timedelta(days=1)).isoformat()}", json={"shifts": [{"start": "07:00", "finish": "15:00"}]}, headers=worker)
    home = client.get("/api/mobile/home", headers=worker).json()
    assert home["today_job"] is None and home["signed_on"] == [] and home["leave_waiting"] == 0
    assert [t["text"] for t in home["todo"]] == ["1 timesheet day not sent yet"] and tom.id
    # With the timesheet module switched off for this person, the home screen no longer mentions it.
    client.patch(f"{S}/overrides", json={"user_id": world.id("worker1"), "module_key": "timesheets", "value": False}, headers=admin)
    home = client.get("/api/mobile/home", headers=worker).json()
    assert home["todo"] == [] and "timesheets" not in [m["key"] for m in home["modules"]]
    assert client.get("/api/mobile/home").status_code == 401


def test_there_is_no_native_app_and_the_portal_says_so(world, client):
    worker = world.auth("worker1")
    version = client.get("/api/mobile/downloads/android/version", headers=worker)
    assert version.status_code == 200 and version.json()["available"] is False and version.json()["version"] is None
    missing = client.get("/api/mobile/downloads/android/latest.apk", headers=worker)
    assert missing.status_code == 404 and "add it to the home screen" in missing.json()["detail"]
    assert client.get("/api/mobile/downloads/android/version").status_code == 401
