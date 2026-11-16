"""Leave requests (stage 7a)."""
import datetime as dt
import io

from sqlalchemy import select

from app.models import AuditLog, LeaveRequest, OutboxMessage
from app.people import today_for

ME = "/api/me/leave"
L = "/api/leave"


def next_weekday(day, weekday):
    return day + dt.timedelta(days=(weekday - day.weekday()) % 7 or 7)


def ask(client, headers, start, end, **extra):
    return client.post(ME, json={"leave_type": "annual", "start_date": start.isoformat(), "end_date": end.isoformat(), **extra}, headers=headers)


def test_a_worker_requests_leave_and_someone_else_decides(world, client, db):
    tom = world.add_worker("Tom", "Okafor", link="worker1")
    sam = world.add_worker("Sam", "Keating", link="hseq1")
    worker, hseq, admin, sup, other = world.auth("worker1"), world.auth("hseq1"), world.auth("admin1"), world.auth("super1"), world.auth("admin2")
    today = today_for(world.org1)
    monday = next_weekday(today, 0)
    assert client.get(ME, headers=admin).json()["linked"] is False  # no worker record: says so
    assert ask(client, admin, monday, monday).status_code == 409
    assert client.get(ME).status_code == 401

    assert ask(client, worker, monday, monday - dt.timedelta(days=1)).status_code == 422
    assert "days ago" in ask(client, worker, today - dt.timedelta(days=30), today - dt.timedelta(days=29)).json()["detail"]
    assert ask(client, worker, monday, monday + dt.timedelta(days=200)).status_code == 422
    saturday = next_weekday(today, 5)
    weekend = ask(client, worker, saturday, saturday + dt.timedelta(days=1))
    assert weekend.status_code == 422 and "not working days" in weekend.json()["detail"]
    assert ask(client, worker, monday, monday, hours="NaN").status_code == 422 and ask(client, worker, monday, monday, hours=30).status_code == 422
    assert client.post(ME, json={"leave_type": "holiday", "start_date": monday.isoformat(), "end_date": monday.isoformat()}, headers=worker).status_code == 422

    # Monday to the following Monday: six working days at 7.6 hours; the weekend is not counted.
    made = ask(client, worker, monday, monday + dt.timedelta(days=7), reason="Family trip")
    assert made.status_code == 201, made.text
    leave = made.json()
    assert leave["hours"] == "45.60" and len(leave["days"]) == 6 and leave["status"] == "requested" and leave["worker"] == "Tom Okafor" and leave["is_own"] is True
    assert leave["can_decide"] is False and leave["can_cancel"] is True
    clash = ask(client, worker, monday + dt.timedelta(days=3), monday + dt.timedelta(days=9))
    assert clash.status_code == 409 and "already leave booked" in clash.json()["detail"]
    half = ask(client, worker, saturday + dt.timedelta(days=14), saturday + dt.timedelta(days=14), hours=4, leave_type="personal")
    assert half.status_code == 201 and half.json()["hours"] == "4.00" and half.json()["days"] == [{"date": (saturday + dt.timedelta(days=14)).isoformat(), "hours": "4.00"}]
    assert [r["hours"] for r in client.get(ME, headers=worker).json()["requests"]] == ["4.00", "45.60"]

    # Who can see and decide: the office with team access; never the person themselves; never another organisation.
    assert client.get(L, headers=worker).status_code == 403
    assert client.get(f"{L}/{leave['id']}", headers=worker).status_code == 200  # their own
    assert client.get(f"{L}/{leave['id']}", headers=other).status_code == 404
    listed = client.get(L, params={"status": "requested"}, headers=sup).json()
    assert len(listed["items"]) == 2 and listed["can_edit"] is False and listed["items"][0]["can_decide"] is False  # a supervisor can look, not decide
    decide = lambda headers, lid, **body: client.post(f"{L}/{lid}/decision", json=body, headers=headers)  # noqa: E731
    assert decide(sup, leave["id"], decision="approve").status_code == 403
    assert decide(worker, leave["id"], decision="approve").status_code == 403
    assert decide(other, leave["id"], decision="approve").status_code == 404
    assert decide(hseq, leave["id"], decision="reject").status_code == 422  # say why
    assert decide(hseq, leave["id"], decision="maybe").status_code == 422
    approved = decide(hseq, leave["id"], decision="approve", note="Enjoy").json()
    assert approved["status"] == "approved" and approved["decided_by"] == "Sam Keating" and approved["decision_note"] == "Enjoy"
    assert decide(admin, leave["id"], decision="reject", note="Changed my mind").status_code == 409  # already decided
    rejected = decide(admin, half.json()["id"], decision="reject", note="Short staffed that day").json()
    assert rejected["status"] == "rejected" and rejected["can_cancel"] is False
    # A rejected request no longer blocks the days.
    assert ask(client, worker, saturday + dt.timedelta(days=14), saturday + dt.timedelta(days=14), hours=4).status_code == 201

    # Sam asks for leave: Sam cannot approve it, Alex can.
    sams = ask(client, hseq, monday, monday + dt.timedelta(days=1)).json()
    own = decide(hseq, sams["id"], decision="approve")
    assert own.status_code == 403 and "cannot decide your own" in own.json()["detail"]
    assert decide(admin, sams["id"], decision="approve").json()["status"] == "approved"
    assert sam.id == sams["worker_id"] and tom.id == leave["worker_id"]

    summary = client.get(f"{L}/summary", headers=sup).json()
    assert summary["waiting"] == 1 and summary["starting_within_14_days"] == 2 and summary["on_leave_today"] == 0
    away = client.get(f"{L}/on-date", params={"date": monday.isoformat()}, headers=sup).json()
    assert [a["worker"] for a in away["items"]] == ["Sam Keating", "Tom Okafor"]
    assert client.get(f"{L}/on-date", params={"date": monday.isoformat()}, headers=other).json()["items"] == []
    assert client.get(f"{L}/summary", headers=worker).status_code == 403
    assert db.scalars(select(OutboxMessage)).all() == []  # nothing is sent
    actions = [a.action for a in db.scalars(select(AuditLog).where(AuditLog.target_type == "leave_request")).all()]
    assert actions.count("leave.requested") == 4 and actions.count("leave.approved") == 2 and actions.count("leave.rejected") == 1


def test_cancelling_evidence_office_entry_and_settings(world, client, db):
    tom = world.add_worker("Tom", "Okafor", link="worker1")
    theirs = world.add_worker("Tom", "Okafor", org=world.org2, link="worker2")
    worker, hseq, admin, sup, other_worker = world.auth("worker1"), world.auth("hseq1"), world.auth("admin1"), world.auth("super1"), world.auth("worker2")
    monday = next_weekday(today_for(world.org1), 0)
    leave = ask(client, worker, monday, monday + dt.timedelta(days=1)).json()

    # Evidence: only on your own request; the office can open it; someone else's worker cannot.
    pdf = lambda: {"file": ("cert.pdf", io.BytesIO(b"%PDF-1.4 medical certificate"), "application/pdf")}  # noqa: E731
    assert client.post(f"{ME}/{leave['id']}/certificate", files=pdf(), headers=other_worker).status_code == 404
    assert client.post(f"{ME}/{leave['id']}/certificate", files={"file": ("x.exe", io.BytesIO(b"MZ..."), "application/octet-stream")}, headers=worker).status_code in (415, 422)
    assert client.post(f"{ME}/{leave['id']}/certificate", files=pdf(), headers=worker).json()["has_certificate"] is True
    assert client.get(f"{L}/{leave['id']}/certificate", headers=worker).content == b"%PDF-1.4 medical certificate"
    assert client.get(f"{L}/{leave['id']}/certificate", headers=hseq).status_code == 200
    assert client.get(f"{L}/{leave['id']}/certificate", headers=sup).status_code == 403  # can see the request, not the evidence
    assert client.get(f"{L}/{leave['id']}/certificate", headers=other_worker).status_code == 404

    # Cancelling: your own, or the office; someone in another organisation cannot.
    assert client.post(f"{ME}/{leave['id']}/cancel", json={}, headers=other_worker).status_code == 404
    assert client.post(f"{L}/{leave['id']}/cancel", json={}, headers=sup).status_code == 403
    cancelled = client.post(f"{ME}/{leave['id']}/cancel", json={"reason": "Plans changed"}, headers=worker).json()
    assert cancelled["status"] == "cancelled" and cancelled["needs_payroll_correction"] is False and cancelled["cancelled_by"] == "Tom Okafor"
    assert client.post(f"{ME}/{leave['id']}/cancel", json={}, headers=worker).status_code == 409
    assert ask(client, worker, monday, monday + dt.timedelta(days=1)).status_code == 201  # the days are free again

    # Leave a pay run has already used: cancelling needs a reason and is flagged for payroll, never silently undone.
    used = ask(client, worker, monday + dt.timedelta(days=7), monday + dt.timedelta(days=8)).json()
    client.post(f"{L}/{used['id']}/decision", json={"decision": "approve"}, headers=hseq)
    row = db.get(LeaveRequest, used["id"])
    row.pay_run_id = "run-1"
    db.commit()
    assert client.post(f"{ME}/{used['id']}/cancel", json={}, headers=worker).status_code == 422
    flagged = client.post(f"{L}/{used['id']}/cancel", json={"reason": "Came to work instead"}, headers=hseq).json()
    assert flagged["status"] == "cancelled" and flagged["needs_payroll_correction"] is True and flagged["used_by_payroll"] is True
    assert client.get(f"{L}/summary", headers=sup).json()["needs_payroll_correction"] == 1

    # The office enters a request for a worker (any date); it still waits for a decision.
    long_ago = monday - dt.timedelta(days=70)
    entry = {"worker_id": tom.id, "leave_type": "personal", "start_date": long_ago.isoformat(), "end_date": long_ago.isoformat()}
    assert client.post(L, json=entry, headers=sup).status_code == 403
    assert client.post(L, json={**entry, "worker_id": theirs.id}, headers=hseq).status_code == 404
    entered = client.post(L, json=entry, headers=hseq).json()
    assert entered["status"] == "requested" and entered["source"] == "office" and entered["requested_by"] == "Sam Keating" and entered["can_decide"] is True

    # Settings: admin only; a ten-hour day changes how new requests are counted.
    assert client.put(f"{L}/settings", json={"hours_per_day": 10, "backdate_days": 7, "work_days": [0, 1, 2, 3]}, headers=hseq).status_code == 403
    assert client.put(f"{L}/settings", json={"hours_per_day": 30, "backdate_days": 7, "work_days": [0]}, headers=admin).status_code == 422
    assert client.put(f"{L}/settings", json={"hours_per_day": 10, "backdate_days": 7, "work_days": [0, 1, 2, 3]}, headers=admin).json()["hours_per_day"] == "10.00"
    week = ask(client, worker, monday + dt.timedelta(days=21), monday + dt.timedelta(days=25)).json()  # Monday to Friday, four working days
    assert week["hours"] == "40.00" and len(week["days"]) == 4
    assert client.get(f"{L}/settings", headers=world.auth("admin2")).json()["hours_per_day"] == "7.6"
