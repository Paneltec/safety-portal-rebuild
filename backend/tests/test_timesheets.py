"""Payroll access grants and timesheets (stage 7b)."""
import datetime as dt

from sqlalchemy import select

from app.models import AuditLog, OutboxMessage, SiteAttendance, TimesheetDay, UserPermissionOverride
from app.people import today_for

ME = "/api/me/payroll"
P = "/api/payroll"


def grant(client, world, key, level, by="owner1"):
    return client.put(f"/api/users/{world.id(key)}/payroll-access", json={"level": level}, headers=world.auth(by))


def shift(start="07:00", finish="15:30", **extra):
    return {"start": start, "finish": finish, "break_minutes": 30, **extra}


def save(client, headers, day, shifts=None, **extra):
    return client.put(f"{ME}/timesheets/{day.isoformat()}", json={"shifts": [shift()] if shifts is None else shifts, **extra}, headers=headers)


def week_of(day):
    return day - dt.timedelta(days=day.weekday())


def test_payroll_opens_only_for_the_owner_or_a_named_grant_to_an_admin(world, client, db):
    owner, admin, hseq, other = world.auth("owner1"), world.auth("admin1"), world.auth("hseq1"), world.auth("admin2")
    office = [("get", f"{P}/timesheets", None), ("get", f"{P}/summary", None), ("get", f"{P}/time-catalog", None), ("post", f"{P}/timesheets/approve", {"ids": ["x"]}),
              ("post", f"{P}/timesheets/reject", {"ids": ["x"], "reason": "r"}), ("post", f"{P}/timesheets/prefill", {}), ("patch", f"{P}/timesheets/x", {}), ("delete", f"{P}/timesheets/x", None),
              ("post", f"{P}/timesheets", {"worker_id": "x", "work_date": "2026-10-19"})]

    def refused(headers):
        return all(getattr(client, m)(url, headers=headers, **({"json": body} if body is not None else {})).status_code == 403 for m, url, body in office)

    # An ordinary admin, an HSEQ lead and an admin of another organisation are refused by every payroll route.
    assert refused(admin) and refused(hseq) and refused(other) and refused(world.auth("worker1"))
    assert client.get(f"{P}/timesheets", headers=owner).status_code == 200
    # A permission override cannot open payroll, even written straight into the database.
    db.add(UserPermissionOverride(user_id=world.id("admin1"), org_id=world.org1.id, overrides={"payroll": {"view": True, "edit": True}}, reasons={}))
    db.commit()
    assert refused(admin)

    # Only the owner manages access, only for administrators of their own organisation.
    assert grant(client, world, "admin1", "view", by="admin1").status_code == 403
    assert client.get(f"{P}/workbench/access/list", headers=admin).status_code == 403
    assert grant(client, world, "hseq1", "view").status_code == 400 and "restricted to administrators" in grant(client, world, "hseq1", "view").json()["detail"]
    assert grant(client, world, "admin2", "view").status_code == 404
    assert grant(client, world, "owner1", "none").status_code == 400  # the owner retains access
    assert grant(client, world, "admin1", "all").status_code == 422
    assert grant(client, world, "admin1", "view").json() == {"user_id": world.id("admin1"), "level": "view"}
    # View: can read, cannot change anything.
    assert client.get(f"{P}/timesheets", headers=admin).json()["can_edit"] is False
    assert client.post(f"{P}/timesheets/approve", json={"ids": ["x"]}, headers=admin).status_code == 403
    assert client.post(f"{P}/timesheets/prefill", json={}, headers=admin).status_code == 403
    me = client.get("/api/auth/me", headers=admin).json()["effective_permissions"]["payroll"]
    assert (me["view"], me["edit"]) == (True, False)
    assert grant(client, world, "admin1", "edit").status_code == 200
    assert client.post(f"{P}/timesheets/approve", json={"ids": ["x"]}, headers=admin).json()["not_found"] == 1
    listed = {u["name"]: u["level"] for u in client.get(f"{P}/workbench/access/list", headers=owner).json()["users"]}
    assert listed == {"Alex Rowe": "edit", "Olive Owner": "owner"}
    assert client.get(f"/api/users/{world.id('admin1')}/permissions", headers=owner).json()["payroll_access"]["grants"] == [{"level": "edit"}]
    # A grant only counts while the person is an administrator, and removal takes effect on the next request.
    world.users["admin1"].role_id = "hseq_lead"
    db.commit()
    assert refused(admin)
    world.users["admin1"].role_id = "admin"
    db.commit()
    assert client.get(f"{P}/timesheets", headers=admin).status_code == 200
    assert grant(client, world, "admin1", "none").status_code == 200
    assert refused(admin) and client.get(f"{P}/timesheets", headers=owner).status_code == 200  # the owner is unaffected
    changes = [a.detail for a in db.scalars(select(AuditLog).where(AuditLog.action == "payroll.access_changed").order_by(AuditLog.created_at)).all()]
    assert [(c["before"], c["after"]) for c in changes] == [("none", "view"), ("view", "edit"), ("edit", "none")]


def test_a_worker_writes_and_sends_their_week_and_the_office_reviews_it(world, client, db):
    tom = world.add_worker("Tom", "Okafor", link="worker1")
    olive = world.add_worker("Olive", "Owner", link="owner1")
    theirs = world.add_worker("Tom", "Okafor", org=world.org2, link="worker2")
    worker, owner, other_worker = world.auth("worker1"), world.auth("owner1"), world.auth("worker2")
    grant(client, world, "admin1", "edit")
    admin = world.auth("admin1")
    today = today_for(world.org1)
    week = week_of(today) - dt.timedelta(days=7)  # last week, so the whole example is in the past whatever day the tests run
    day = week
    assert client.get(f"{ME}/timesheets", headers=world.auth("hseq1")).json()["linked"] is False
    assert save(client, world.auth("hseq1"), day).status_code == 409

    # Saving a day works the hours out on the server, by the pack's rules: 8 paid hours = 7.6 ordinary + 0.4 overtime.
    saved = save(client, worker, day, notes="Culvert job")
    assert saved.status_code == 200, saved.text
    entry = saved.json()
    assert entry["paid_hours"] == "8.00" and entry["hours"]["ordinary"] == 7.6 and entry["hours"]["ot1"] == 0.4 and entry["status"] == "draft" and entry["revision"] == 1
    assert save(client, worker, today + dt.timedelta(days=1)).status_code == 422
    assert save(client, worker, today - dt.timedelta(days=60)).status_code == 422
    overlap = save(client, worker, day, shifts=[shift("07:00", "10:00", break_minutes=0), shift("09:00", "12:00", break_minutes=0)])
    assert overlap.status_code == 422 and overlap.json()["detail"] == "Shifts overlap; correct the time entries"
    cross = save(client, worker, day, shifts=[shift("16:00", "22:00")])
    assert cross.status_code == 422 and "unpaid break start" in cross.json()["detail"]
    assert save(client, worker, day, shifts=[shift("15:00", "07:00")]).status_code == 422
    assert save(client, worker, day, shifts=[shift(site_id="nope")]).status_code == 422
    assert save(client, worker, day, kind="rdo").status_code == 422  # a day off with shifts
    assert save(client, worker, day, shifts=[{"start": "07:00", "finish": "15:00", "public_holiday": True}]).json()["shifts"][0].get("public_holiday") is None  # only the office marks a public holiday
    stale = save(client, worker, day, expected_revision=1)
    assert stale.status_code == 409 and "another window" in stale.json()["detail"]
    assert save(client, worker, day, expected_revision=2).json()["revision"] == 3
    assert save(client, worker, day + dt.timedelta(days=1), shifts=[], kind="rdo").json()["kind_label"] == "Rostered day off"
    assert save(client, worker, day + dt.timedelta(days=2), shifts=[]).json()["paid_hours"] == "0.00"  # an empty worked day

    mine = client.get(f"{ME}/timesheets", params={"week_start": week.isoformat()}, headers=worker).json()
    assert mine["linked"] is True and len(mine["days"]) == 7 and mine["total_paid_hours"] == "8.00" and mine["ready_to_send"] == 2
    assert client.get(f"{ME}/timesheets", params={"week_start": week.isoformat()}, headers=other_worker).json()["total_paid_hours"] == "0.00"

    # Sending: filled-in days go to the office; the empty one stays. A sent day can no longer be changed by the worker.
    sent = client.post(f"{ME}/submit", json={"week_start": week.isoformat()}, headers=worker).json()
    assert sent == {"submitted": 2, "skipped_empty": 1}
    assert save(client, worker, day).status_code == 409
    row = db.scalars(select(TimesheetDay).where(TimesheetDay.worker_id == tom.id, TimesheetDay.work_date == day)).one()
    assert row.status == "submitted" and row.paid_hours == 8

    # The office: sees the week, corrects a day (public holiday), approves, sends back with a reason.
    listing = client.get(f"{P}/timesheets", params={"week_start": week.isoformat()}, headers=admin).json()
    assert [w["worker"] for w in listing["workers"]] == ["Tom Okafor"] and listing["can_edit"] is True
    assert client.post(f"{P}/timesheets/reject", json={"ids": [row.id]}, headers=admin).status_code == 422  # say why
    back = client.post(f"{P}/timesheets/reject", json={"ids": [row.id, "nope"], "reason": "Finish time looks wrong"}, headers=admin).json()
    assert back == {"changed": 1, "unchanged": 0, "not_found": 1, "own_skipped": 0}
    returned = client.get(f"{ME}/timesheets", params={"week_start": week.isoformat()}, headers=worker).json()
    sent_back = next(d for d in returned["days"] if d["date"] == day.isoformat())
    assert sent_back["entry"]["status"] == "rejected" and sent_back["entry"]["reject_reason"] == "Finish time looks wrong" and sent_back["editable"] is True
    assert save(client, worker, day, shifts=[shift("07:00", "15:00")]).json()["paid_hours"] == "7.50"
    client.post(f"{ME}/submit", json={"week_start": week.isoformat()}, headers=worker)
    db.expire_all()
    holiday = client.patch(f"{P}/timesheets/{row.id}", json={"shifts": [{"start": "07:00", "finish": "15:00", "public_holiday": True}], "expected_revision": row.revision}, headers=admin)
    assert holiday.status_code == 200 and holiday.json()["hours"]["holiday_work"] == 8.0 and holiday.json()["hours"]["penalty_ordinary"] == 7.6
    assert client.patch(f"{P}/timesheets/{row.id}", json={"shifts": [], "expected_revision": 1}, headers=admin).status_code == 409
    approved = client.post(f"{P}/timesheets/approve", json={"ids": [row.id]}, headers=admin).json()
    assert approved["changed"] == 1
    assert client.post(f"{P}/timesheets/approve", json={"ids": [row.id]}, headers=admin).json()["unchanged"] == 1
    db.expire_all()
    assert row.status == "approved" and row.reviewed_by == world.id("admin1")
    assert client.delete(f"{P}/timesheets/{row.id}", headers=admin).status_code == 409  # approved days are kept
    # Changing an approved day puts it back to waiting; a locked (paid) day cannot be changed or sent back at all.
    assert client.patch(f"{P}/timesheets/{row.id}", json={"shifts": [{"start": "07:00", "finish": "15:00"}]}, headers=admin).json()["status"] == "submitted"
    client.post(f"{P}/timesheets/approve", json={"ids": [row.id]}, headers=admin)
    db.expire_all()
    row.status = "locked"
    db.commit()
    assert client.patch(f"{P}/timesheets/{row.id}", json={"shifts": []}, headers=admin).status_code == 409
    assert client.post(f"{P}/timesheets/reject", json={"ids": [row.id], "reason": "x"}, headers=admin).json()["unchanged"] == 1
    assert save(client, worker, day).status_code == 409

    # Nobody approves their own hours: the owner's own day is skipped, and the other admin approves it.
    assert save(client, owner, day).status_code == 200
    client.post(f"{ME}/submit", json={"week_start": week.isoformat()}, headers=owner)
    own = db.scalars(select(TimesheetDay).where(TimesheetDay.worker_id == olive.id)).one()
    assert client.post(f"{P}/timesheets/approve", json={"ids": [own.id]}, headers=owner).json()["own_skipped"] == 1
    assert client.post(f"{P}/timesheets/approve", json={"ids": [own.id]}, headers=admin).json()["changed"] == 1

    # The office enters a day for a worker; one record per worker and date; other organisations are out of reach.
    entered = client.post(f"{P}/timesheets", json={"worker_id": tom.id, "work_date": (day + dt.timedelta(days=3)).isoformat(), "shifts": [{"start": "18:00", "finish": "02:00", "next_day": True, "replacement_day_shift": True}]}, headers=admin)
    assert entered.status_code == 201 and entered.json()["hours"]["night"] == 8.0 and entered.json()["hours"]["penalty_ordinary"] == 7.6 and entered.json()["status"] == "submitted"
    assert client.post(f"{P}/timesheets", json={"worker_id": tom.id, "work_date": (day + dt.timedelta(days=3)).isoformat()}, headers=admin).status_code == 409
    assert client.post(f"{P}/timesheets", json={"worker_id": theirs.id, "work_date": day.isoformat()}, headers=admin).status_code == 404
    spare = db.scalars(select(TimesheetDay).where(TimesheetDay.worker_id == tom.id, TimesheetDay.work_date == day + dt.timedelta(days=2))).one()
    assert client.delete(f"{P}/timesheets/{spare.id}", headers=admin).status_code == 204
    summary = client.get(f"{P}/summary", params={"week_start": week.isoformat()}, headers=admin).json()
    assert summary["by_status"]["locked"]["days"] == 1 and summary["workers"] == 2
    assert db.scalars(select(OutboxMessage)).all() == []


def test_prefill_from_site_sign_ons_never_overwrites(world, client, db):
    from app.models import Site
    tom = world.add_worker("Tom", "Okafor", link="worker1")
    kim = world.add_worker("Kim", "Nguyen")
    owner = world.auth("owner1")
    site = Site(org_id=world.org1.id, name="Depot", scan_token="tok-prefill-1")
    db.add(site)
    db.flush()
    today = today_for(world.org1)
    week = week_of(today) - dt.timedelta(days=7)
    day = week
    from zoneinfo import ZoneInfo
    hobart = ZoneInfo("Australia/Hobart")

    def visit(worker, start, end, on=day):
        db.add(SiteAttendance(org_id=world.org1.id, site_id=site.id, kind="worker", worker_id=worker.id, name="x", source="qr",
                              signed_in_at=dt.datetime.combine(on, dt.time.fromisoformat(start), tzinfo=hobart),
                              signed_out_at=dt.datetime.combine(on, dt.time.fromisoformat(end), tzinfo=hobart) if end else None))

    visit(tom, "07:00", "11:30")
    visit(tom, "12:00", "15:30")
    visit(kim, "07:00", "15:00")
    visit(kim, "06:00", None, on=day + dt.timedelta(days=1))  # still signed on: not used
    db.commit()
    save(client, world.auth("worker1"), day + dt.timedelta(days=1))  # Tom already wrote the next day himself
    done = client.post(f"{P}/timesheets/prefill", json={"week_start": week.isoformat()}, headers=owner).json()
    assert done["created"] == 2 and done["already_there"] == 0 and done["could_not_use"] == 0
    rows = {(r.worker_id, r.work_date): r for r in db.scalars(select(TimesheetDay)).all()}
    toms = rows[(tom.id, day)]
    assert toms.status == "draft" and toms.source == "prefill" and toms.paid_hours == 8 and [s["start"] for s in toms.shifts] == ["07:00", "12:00"] and toms.shifts[0]["site_id"] == site.id
    assert rows[(tom.id, day + dt.timedelta(days=1))].source == "self"
    again = client.post(f"{P}/timesheets/prefill", json={"week_start": week.isoformat()}, headers=owner).json()
    assert again["created"] == 0 and again["already_there"] == 2
    # The worker checks the draft and sends it like any other day.
    assert client.post(f"{ME}/submit", json={"week_start": week.isoformat()}, headers=world.auth("worker1")).json()["submitted"] == 2


def test_a_phone_can_resend_the_same_save_without_changing_the_day_twice(world, client, db):
    worker = world.auth("worker1")
    world.add_worker("Tom", "Okafor", link="worker1")
    day = week_of(today_for(world.org1)) - dt.timedelta(days=7)
    first = save(client, worker, day, client_ref="phone-ref-0001")
    assert first.status_code == 200 and first.json()["revision"] == 1
    # The phone lost the answer and sends the same save again: nothing changes and no second audit line is written.
    audits = len(db.scalars(select(AuditLog)).all())
    again = save(client, worker, day, client_ref="phone-ref-0001", expected_revision=0)
    assert again.status_code == 200 and again.json()["revision"] == 1 and again.json()["id"] == first.json()["id"]
    db.expire_all()
    assert len(db.scalars(select(AuditLog)).all()) == audits and len(db.scalars(select(TimesheetDay)).all()) == 1
    # A different save is a real change; a stale one is refused, not forced through.
    assert save(client, worker, day, shifts=[shift("08:00", "16:00")], client_ref="phone-ref-0002", expected_revision=1).json()["revision"] == 2
    assert save(client, worker, day, client_ref="phone-ref-0003", expected_revision=1).status_code == 409
    # Once the office has the day, a queued save is refused with a reason and the day is left as it is.
    assert client.post(f"{ME}/submit", json={"week_start": week_of(day).isoformat()}, headers=worker).status_code == 200
    late = save(client, worker, day, shifts=[shift("06:00", "14:00")], client_ref="phone-ref-0004")
    assert late.status_code == 409 and db.scalars(select(TimesheetDay)).one().client_ref == "phone-ref-0002"
    assert save(client, worker, day, client_ref="short").status_code == 422
