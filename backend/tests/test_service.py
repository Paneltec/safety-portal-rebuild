"""Service schedules and records (stage 6b)."""
import datetime as dt

from sqlalchemy import select

from app.models import Asset, AuditLog, OutboxMessage
from app.people import today_for

A = "/api/assets"


def add(client, headers, **body):
    r = client.post(A, json={"name": "Tipper 02", "kind": "vehicle", **body}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_schedule_status_by_days_km_and_hours_and_unknown_is_not_ok(world, client, db):
    admin, sup, other = world.auth("admin1"), world.auth("super1"), world.auth("admin2")
    today = today_for(world.org1)
    truck = add(client, admin, odometer_km=20000)
    url = f"{A}/{truck['id']}"
    assert client.post(f"{url}/schedules", json={"name": "Service"}, headers=admin).status_code == 422  # no interval
    assert client.post(f"{url}/schedules", json={"name": "Service", "interval_km": -1}, headers=admin).status_code == 422
    assert client.post(f"{url}/schedules", json={"name": "Service", "interval_km": 10000}, headers=sup).status_code == 403
    assert client.post(f"{url}/schedules", json={"name": "Service", "interval_days": 30, "assigned_worker_id": "nope"}, headers=admin).status_code == 422

    # Kilometres: due at 25,000; the odometer says 20,000, margin 500.
    km = client.post(f"{url}/schedules", json={"name": "10,000 km service", "interval_km": 10000, "baseline_km": 15000}, headers=admin).json()
    assert km["status"] == "ok" and km["parts"][0]["due_at"] == 25000 and km["summary"] == "Due in 5,000 km (at 25,000)"
    client.post(f"{url}/meter", json={"odometer_km": 24600}, headers=admin)
    assert client.get(f"{url}/schedules/{km['id']}", headers=sup).json()["status"] == "due_soon"
    client.post(f"{url}/meter", json={"odometer_km": 25100}, headers=admin)
    over = client.get(f"{url}/schedules/{km['id']}", headers=sup).json()
    assert over["status"] == "overdue" and over["summary"] == "100 km overdue"

    # Hours on an asset with no hour meter recorded: cannot tell, and it says why.
    hours = client.post(f"{url}/schedules", json={"name": "250 hour service", "interval_hours": 250, "baseline_hours": 1000}, headers=admin).json()
    assert hours["status"] == "unknown" and "hour meter has not been recorded" in hours["summary"]
    # No starting point at all: also unknown.
    nobase = client.post(f"{url}/schedules", json={"name": "Annual inspection", "interval_days": 365}, headers=admin).json()
    assert nobase["status"] == "unknown" and nobase["summary"] == "No date for the last service"
    # Days and kilometres together: whichever comes first decides.
    both = client.post(f"{url}/schedules", json={"name": "Either", "interval_days": 180, "interval_km": 50000, "baseline_km": 20000,
                                                 "baseline_date": (today - dt.timedelta(days=200)).isoformat()}, headers=admin).json()
    assert both["status"] == "overdue" and both["summary"] == "20 days overdue" and [p["status"] for p in both["parts"]] == ["overdue", "ok"]

    listed = client.get(f"{url}/schedules", headers=sup).json()
    assert [s["name"] for s in listed["items"]] == ["10,000 km service", "250 hour service", "Annual inspection", "Either"] and listed["can_edit"] is False
    # The register shows the worst of them.
    row = client.get(A, headers=sup).json()["items"][0]
    assert row["service"]["status"] == "overdue"
    assert client.get(f"{A}/service/summary", headers=sup).json() == {"ok": 0, "due_soon": 0, "overdue": 2, "unknown": 2, "schedules": 4}
    inbox = client.get(f"{A}/service/inbox", headers=sup).json()
    assert inbox["total"] == 4 and [i["status"] for i in inbox["items"]] == ["overdue", "overdue", "unknown", "unknown"]
    assert client.get(f"{A}/service/inbox", params={"status": "unknown"}, headers=sup).json()["total"] == 2
    assert client.get(f"{A}/service/inbox", headers=world.auth("worker1")).status_code == 403
    # Nothing leaks to, or from, the other organisation.
    assert client.get(f"{A}/service/inbox", headers=other).json()["total"] == 0
    assert client.get(f"{url}/schedules", headers=other).status_code == 404
    theirs = add(client, other)
    assert client.get(f"{A}/{theirs['id']}/schedules/{km['id']}", headers=other).status_code == 404  # their asset, our schedule
    assert client.put(f"{A}/{theirs['id']}/schedules/{km['id']}", json={"name": "Mine", "interval_days": 1}, headers=other).status_code == 404
    assert client.get("/api/fleet/service-schedule-presets", headers=sup).json()["items"][0]["interval_hours"] == 250


def test_recording_a_service_restarts_the_schedule_and_history_survives_edits(world, client, db):
    admin, hseq, sup = world.auth("admin1"), world.auth("hseq1"), world.auth("super1")
    today = today_for(world.org1)
    digger = add(client, admin, name="Excavator 5t", kind="plant", engine_hours=1240)
    url = f"{A}/{digger['id']}"
    sched = client.post(f"{url}/schedules", json={"name": "250 hour service", "interval_hours": 250, "baseline_hours": 1000}, headers=admin).json()
    assert sched["status"] == "due_soon" and sched["summary"] == "Due in 10 hours (at 1,250)"

    base = {"performed_on": today.isoformat(), "schedule_id": sched["id"]}
    assert client.post(f"{url}/records", json={**base, "engine_hours": 1255}, headers=sup).status_code == 403
    needs = client.post(f"{url}/records", json=base, headers=hseq)
    assert needs.status_code == 422 and "hour-meter reading at the service is needed" in needs.json()["detail"]
    assert client.post(f"{url}/records", json={**base, "engine_hours": 1255, "performed_on": (today + dt.timedelta(days=1)).isoformat()}, headers=hseq).status_code == 422
    assert client.post(f"{url}/records", json={**base, "engine_hours": 1255, "cost": "lots"}, headers=hseq).status_code == 422
    assert client.post(f"{url}/records", json={**base, "engine_hours": 1255, "schedule_id": "nope"}, headers=hseq).status_code == 404
    done = client.post(f"{url}/records", json={**base, "engine_hours": 1255, "technician": "Workshop", "cost": 412.345, "description": "Oils and filters",
                                               "checklist": [{"item": "Engine oil", "result": "ok"}, {"item": "Track tension", "result": "attention", "note": "Adjust next visit"}]}, headers=hseq)
    assert done.status_code == 201, done.text
    record = done.json()
    assert record["cost"] == "412.35" and record["schedule_name"] == "250 hour service" and record["needs_attention"] == 1 and record["recorded_by"] == "Sam Keating"
    # The schedule now counts from this service, and the asset's hour meter moved forward.
    after = client.get(f"{url}/schedules/{sched['id']}", headers=sup).json()
    assert after["status"] == "ok" and after["parts"][0]["due_at"] == 1505 and after["last_done"] == {"date": today.isoformat(), "odometer_km": None, "engine_hours": 1255, "from_record": True}
    assert client.get(url, headers=sup).json()["engine_hours"] == 1255
    history = client.get(f"{url}/meter-history", headers=sup).json()["items"]
    assert history[0]["source"] == "service" and history[0]["engine_hours"] == 1255

    # Renaming and re-timing the schedule does not rewrite what was done.
    client.put(f"{url}/schedules/{sched['id']}", json={"name": "500 hour service", "interval_hours": 500, "baseline_hours": 1000}, headers=admin)
    kept = client.get(f"{url}/records/{record['id']}", headers=sup).json()
    assert kept["schedule_name"] == "250 hour service" and kept["engine_hours"] == 1255
    assert client.get(f"{url}/schedules/{sched['id']}", headers=sup).json()["parts"][0]["due_at"] == 1755

    # An older job entered late, with a lower reading, is history: no error and the current meter stays.
    old = client.post(f"{url}/records", json={"kind": "repair", "performed_on": (today - dt.timedelta(days=90)).isoformat(), "engine_hours": 900, "description": "Hose"}, headers=hseq)
    assert old.status_code == 201 and client.get(url, headers=sup).json()["engine_hours"] == 1255
    records = client.get(f"{url}/records", headers=sup).json()
    assert [r["kind"] for r in records["items"]] == ["service", "repair"] and records["total_cost"] == "412.35" and records["records_without_cost"] == 1

    # A correction changes the record, is audited, and moves the schedule's starting point with it.
    fixed = client.put(f"{url}/records/{record['id']}", json={**base, "engine_hours": 1260, "cost": 412.35, "technician": "Workshop"}, headers=hseq).json()
    assert fixed["engine_hours"] == 1260 and fixed["schedule_name"] == "250 hour service"
    assert client.get(f"{url}/schedules/{sched['id']}", headers=sup).json()["parts"][0]["due_at"] == 1760
    # Removing the record needs the delete right; the schedule falls back to its baseline, with no stale due date.
    assert client.delete(f"{url}/records/{record['id']}", headers=hseq).status_code == 403
    assert client.delete(f"{url}/records/{record['id']}", headers=admin).status_code == 204
    back = client.get(f"{url}/schedules/{sched['id']}", headers=sup).json()
    assert back["last_done"]["from_record"] is False and back["parts"][0]["due_at"] == 1500
    # Stopping a schedule keeps the remaining history.
    assert client.delete(f"{url}/schedules/{sched['id']}", headers=admin).status_code == 204
    assert len(client.get(f"{url}/records", headers=sup).json()["items"]) == 1 and client.get(f"{A}/service/summary", headers=sup).json()["schedules"] == 0
    actions = {a.action for a in db.scalars(select(AuditLog).where(AuditLog.org_id == world.org1.id)).all()}
    assert {"service_schedule.created", "service_schedule.updated", "service_record.created", "service_record.updated", "service_record.deleted", "service_schedule.deleted"} <= actions


def test_service_reminders_are_previewed_prepared_once_and_never_automatic(world, client, db):
    admin, sup = world.auth("admin1"), world.auth("super1")
    today = today_for(world.org1)
    mech = world.add_worker("Kim", "Nguyen", email="kim@one.example")
    silent = world.add_worker("Ari", "Blake")
    truck = add(client, admin, odometer_km=30000)
    url = f"{A}/{truck['id']}"
    late = (today - dt.timedelta(days=400)).isoformat()
    client.post(f"{url}/schedules", json={"name": "Annual inspection", "interval_days": 365, "baseline_date": late, "assigned_worker_id": mech.id}, headers=admin)
    client.post(f"{url}/schedules", json={"name": "Brake check", "interval_days": 365, "baseline_date": late, "assigned_worker_id": silent.id}, headers=admin)
    client.post(f"{url}/schedules", json={"name": "Tyres", "interval_days": 365, "baseline_date": late}, headers=admin)
    client.post(f"{url}/schedules", json={"name": "Far off", "interval_km": 10000, "baseline_km": 29000}, headers=admin)
    assert db.scalars(select(OutboxMessage)).all() == []  # opening lists and creating schedules sends nothing

    assert client.post(f"{A}/service/scan-reminders", json={}, headers=sup).status_code == 403
    preview = client.post(f"{A}/service/scan-reminders", json={}, headers=admin).json()
    assert preview["dry_run"] is True and preview["due"] == 3 and preview["counts"] == {"would_prepare": 1, "no_email": 1, "no_one_assigned": 1}
    assert db.scalars(select(OutboxMessage)).all() == []
    done = client.post(f"{A}/service/scan-reminders", json={"dry_run": False}, headers=admin).json()
    message = db.scalars(select(OutboxMessage)).one()
    assert message.recipient == "kim@one.example" and message.purpose == "reminder" and message.status != "sent"  # no email connection: held
    assert "Annual inspection" in message.body_text and "35 days overdue" in message.body_text
    assert done["counts"].get("would_prepare") is None
    again = client.post(f"{A}/service/scan-reminders", json={"dry_run": False}, headers=admin).json()
    assert again["counts"]["already_prepared"] == 1 and len(db.scalars(select(OutboxMessage)).all()) == 1
    assert db.scalars(select(Asset)).one().odometer_km == 30000
