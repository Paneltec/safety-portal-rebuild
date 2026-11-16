"""The pay workbench (stage 7c): settings, profiles, runs, payslips, evidence and what it refuses to do."""
import datetime as dt
import json
from pathlib import Path

from sqlalchemy import select

from app.models import AuditLog, LeaveRequest, OutboxMessage, PayProfile, PayRun, TimesheetDay

P = "/api/payroll"
ME = "/api/me/payroll"
FIXTURES = {c["id"]: c for c in json.loads((Path(__file__).parent / "fixtures" / "payroll_fixtures.json").read_text())["payroll_rows"]}
WEEK = dt.date(2026, 10, 19)  # the Monday used by the pack's examples; payday 29 October 2026
PAYDAY = "2026-10-29"
ABN = "51824753556"
RULES = {"ot1_multiplier": 1.5, "ot2_multiplier": 2, "night_multiplier": 2, "holiday_work_multiplier": 2.5, "super_percent": 12, "meal_allowance": 20, "meal_tax_treatment": "taxable",
         "daily_ordinary_hours": 7.6, "ot1_hours": 2, "night_start": "18:00", "night_end": "06:00", "earning_rules": []}
PROFILE = {k: v for k, v in FIXTURES["ordinary_full_time"]["input"]["profile"].items()} | {"opening_annual": 100, "opening_personal": 50}


def settings(client, headers, revision=0, **extra):
    return client.put(f"{P}/settings", json={"expected_revision": revision, "rules": {**RULES, **extra.pop("rules", {})}, "employer_name": "Example Civil Pty Ltd", "employer_abn": ABN, **extra}, headers=headers)


def profile(client, headers, worker, revision=0, **changes):
    r = client.put(f"{P}/profiles/{worker.id}", json={"expected_revision": revision, **PROFILE, **changes}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def day(client, headers, worker, date, start="07:00", finish="15:06"):
    """An approved timesheet day: 07:00 to 15:06 less a 30 minute break is 7.6 paid hours."""
    made = client.post(f"{P}/timesheets", json={"worker_id": worker.id, "work_date": date.isoformat(), "shifts": [{"start": start, "finish": finish, "break_minutes": 30}]}, headers=headers)
    assert made.status_code == 201, made.text
    assert client.post(f"{P}/timesheets/approve", json={"ids": [made.json()["id"]]}, headers=headers).json()["changed"] == 1
    return made.json()["id"]


def reviewed(run, **overrides):
    return {r["worker_id"]: {"manual": r["manual"], "adjust": r["adjust"], "hours_reviewed": True, "super_reviewed": True, **overrides.get(r["worker_id"], {})} for r in run["rows"]}


def figures(row, case):
    expected = FIXTURES[case]["expected"]
    return {k: row["result"][k] for k in expected} == expected


def test_a_week_from_timesheets_and_leave_to_frozen_run_matches_the_pack(world, client, db):
    owner = world.auth("owner1")
    tom = world.add_worker("Tom", "Okafor", link="worker1")
    kim = world.add_worker("Kim", "Nguyen")
    lee = world.add_worker("Lee", "Fontaine")
    world.add_worker("Sam", "Keating", link="hseq1")
    for i in range(5):
        day(client, owner, tom, WEEK + dt.timedelta(days=i))
    for i in range(1, 5):
        day(client, owner, lee, WEEK + dt.timedelta(days=i))
    leave = client.post("/api/leave", json={"worker_id": lee.id, "leave_type": "annual", "start_date": WEEK.isoformat(), "end_date": WEEK.isoformat()}, headers=owner).json()
    assert client.post(f"/api/leave/{leave['id']}/decision", json={"decision": "approve"}, headers=owner).json()["status"] == "approved"
    profile(client, owner, tom)
    profile(client, owner, kim)
    profile(client, owner, lee, leave_loading_percent=17.5)

    assert client.post(f"{P}/runs", json={"week_start": WEEK.isoformat(), "extra": True}, headers=owner).status_code == 422  # no weekly run yet
    run = client.post(f"{P}/runs", json={"week_start": WEEK.isoformat()}, headers=owner).json()
    assert run["status"] == "draft" and run["label"] == "Weekly run" and [r["name"] for r in run["rows"]] == ["Lee Fontaine", "Tom Okafor"]  # approved hours and leave came in
    assert client.post(f"{P}/runs", json={"week_start": WEEK.isoformat()}, headers=owner).status_code == 409
    url = f"{P}/runs/{run['id']}"
    tom_row = next(r for r in run["rows"] if r["worker_id"] == tom.id)
    assert tom_row["entry"]["ordinary"] == 38.0 and tom_row["result"]["issues"] == ["Confirm the hours have been reviewed", "Confirm super eligibility has been reviewed"]
    assert run["ready"] is False and run["totals"]["rows_with_issues"] == 2

    # Add Kim by hand (38 ordinary, 2 and 1 overtime), set the payday, tick the reviews.
    stale_save = client.put(url, json={"expected_revision": 99, "rows": {}}, headers=owner)
    assert stale_save.status_code == 409 and "another window" in stale_save.json()["detail"]
    rows = reviewed(run) | {kim.id: {"manual": {"ordinary": 38, "ot1": 2, "ot2": 1}, "adjust": {}, "hours_reviewed": True, "super_reviewed": True}}
    assert client.put(url, json={"expected_revision": run["revision"], "payday": "2026-10-01", "rows": rows}, headers=owner).status_code == 422  # payday before the week
    assert client.put(url, json={"expected_revision": run["revision"], "rows": {kim.id: {"manual": {"ordinary": 500}}}}, headers=owner).status_code == 422
    assert client.put(url, json={"expected_revision": run["revision"], "rows": {kim.id: {"manual": {"bananas": 3}}}}, headers=owner).status_code == 422
    assert client.put(url, json={"expected_revision": run["revision"], "rows": {tom.id: None}}, headers=owner).status_code == 409  # has approved hours behind it
    saved = client.put(url, json={"expected_revision": run["revision"], "payday": PAYDAY, "rows": rows}, headers=owner)
    assert saved.status_code == 200, saved.text
    sheet = {r["name"]: r for r in saved.json()["rows"]}
    # The three rows are the pack's worked examples, reached here from real timesheets, leave and settings.
    assert figures(sheet["Tom Okafor"], "ordinary_full_time") and figures(sheet["Kim Nguyen"], "overtime") and figures(sheet["Lee Fontaine"], "annual_leave_with_loading")
    assert saved.json()["totals"] == {"gross": "3609.90", "payg": "610.00", "net": "2999.90", "super": "415.19", "rows": 3, "rows_with_issues": 0} and saved.json()["ready"] is True

    # Finalising needs the employer details; then it freezes, locks the days and marks the leave as used.
    rev = saved.json()["revision"]
    no_employer = client.post(f"{url}/finalise", json={"expected_revision": rev}, headers=owner)
    assert no_employer.status_code == 422 and "employer name" in no_employer.json()["detail"]
    assert settings(client, owner).status_code == 200
    assert client.post(f"{url}/finalise", json={"expected_revision": rev - 1}, headers=owner).status_code == 409
    for blocked in (f"{url}/report.csv", f"{url}/payslips/{tom.id}/pdf"):
        assert client.get(blocked, headers=owner).status_code == 409  # only once finalised
    done = client.post(f"{url}/finalise", json={"expected_revision": rev}, headers=owner)
    assert done.status_code == 200, done.text
    final = done.json()
    assert final["status"] == "finalised" and len(final["hash"]) == 64 and final["employer"] == {"name": "Example Civil Pty Ltd", "abn": ABN} and final["totals"]["net"] == "2999.90"
    db.expire_all()
    days = db.scalars(select(TimesheetDay)).all()
    assert len(days) == 9 and all(d.status == "locked" and d.pay_run_id == run["id"] for d in days)
    assert db.get(LeaveRequest, leave["id"]).pay_run_id == run["id"]
    assert client.patch(f"{P}/timesheets/{days[0].id}", json={"shifts": []}, headers=owner).status_code == 409
    assert client.put(url, json={"expected_revision": final["revision"], "rows": {}}, headers=owner).status_code == 409
    assert client.post(f"{url}/finalise", json={"expected_revision": final["revision"]}, headers=owner).status_code == 409
    # A later change to a rate or a rule does not touch the frozen figures.
    profile(client, owner, tom, revision=1, hourly_rate=99)
    settings(client, owner, revision=1, rules={"super_percent": 20})
    again = client.get(url, headers=owner).json()
    assert again["hash"] == final["hash"] and figures(next(r for r in again["rows"] if r["name"] == "Tom Okafor"), "ordinary_full_time")
    register = client.get(f"{url}/report.csv", headers=owner).text
    assert "Tom Okafor,38.0,1140.00,183.00,957.00,1140.00,136.80,102.92,51.46" in register and "Total,,3609.90,610.00,2999.90,,415.19" in register and "TEST WORKBENCH" in register
    slip = client.get(f"{url}/payslips/{tom.id}/pdf", headers=owner)
    assert slip.status_code == 200 and slip.content[:4] == b"%PDF" and client.get(f"{url}/payslips/nobody/pdf", headers=owner).status_code == 404

    # Payslips: a worker sees only their own, and only after they are released. Nothing is emailed.
    worker, sam = world.auth("worker1"), world.auth("hseq1")
    assert client.get(f"{ME}/payslips", headers=worker).json()["items"] == [] and client.get(f"{ME}/payslips/{run['id']}/pdf", headers=worker).status_code == 404
    assert client.post(f"{url}/payslips/release", json={"release": True}, headers=owner).json()["payslips_released"] is True
    mine = client.get(f"{ME}/payslips", headers=worker).json()["items"]
    assert [(m["gross"], m["net"]) for m in mine] == [("1140.00", "957.00")]
    assert client.get(f"{ME}/payslips/{run['id']}/pdf", headers=worker).content[:4] == b"%PDF"
    assert client.get(f"{ME}/payslips", headers=sam).json()["items"] == [] and client.get(f"{ME}/payslips/{run['id']}/pdf", headers=sam).status_code == 404  # on no row of this run
    assert client.get(f"{ME}/payslips/{run['id']}/pdf", headers=world.auth("worker2")).status_code == 404 and client.get(f"{ME}/payslips/{run['id']}/pdf", headers=world.auth("admin1")).status_code == 404
    assert client.get(f"{url}/payslips/{tom.id}/pdf", headers=worker).status_code == 403  # the office route is not for workers
    assert db.scalars(select(OutboxMessage)).all() == []

    # Leave cancelled after the run used it: flagged on the run, pay untouched.
    client.post(f"/api/leave/{leave['id']}/cancel", json={"reason": "Worked that day after all"}, headers=owner)
    flagged = client.get(url, headers=owner).json()
    assert [c["worker"] for c in flagged["corrections"]] == ["Lee Fontaine"] and flagged["hash"] == final["hash"]

    # An extra run for the same week: by hand only, and it starts from the weekly run's closing leave balances.
    extra = client.post(f"{P}/runs", json={"week_start": WEEK.isoformat(), "extra": True}, headers=owner).json()
    assert extra["label"] == "Extra run 1" and extra["rows"] == []
    assert client.post(f"{P}/runs/{extra['id']}/import", json={"expected_revision": extra["revision"]}, headers=owner).status_code == 409
    profile(client, owner, tom, revision=2)  # back to $30
    bonus = client.put(f"{P}/runs/{extra['id']}", json={"expected_revision": extra["revision"], "payday": PAYDAY,
                                                         "rows": {tom.id: {"manual": {"ot1": 2}, "adjust": {}, "hours_reviewed": True, "super_reviewed": True}}}, headers=owner).json()
    assert bonus["rows"][0]["entry"]["opening_annual"] == 102.923077 and bonus["rows"][0]["result"]["ot1_pay"] == 90.0
    listing = client.get(f"{P}/runs", headers=owner).json()["items"]
    assert [(r["label"], r["status"]) for r in listing] == [("Extra run 1", "draft"), ("Weekly run", "finalised")]


def test_stale_sources_reopen_evidence_and_refusals(world, client, db):
    owner = world.auth("owner1")
    tom = world.add_worker("Tom", "Okafor")
    profile(client, owner, tom)
    settings(client, owner)
    ids = [day(client, owner, tom, WEEK + dt.timedelta(days=i)) for i in range(4)]
    run = client.post(f"{P}/runs", json={"week_start": WEEK.isoformat()}, headers=owner).json()
    url = f"{P}/runs/{run['id']}"
    saved = client.put(url, json={"expected_revision": run["revision"], "payday": PAYDAY, "rows": reviewed(run)}, headers=owner).json()
    assert saved["ready"] is True and saved["stale"] == []

    # A fifth day is approved after the hours were brought in: the worksheet is stale and cannot be finalised.
    day(client, owner, tom, WEEK + dt.timedelta(days=4))
    stale = client.get(url, headers=owner).json()
    assert stale["stale"] == [tom.id] and stale["ready"] is False and "Bring them in again" in stale["stale_note"]
    refused = client.post(f"{url}/finalise", json={"expected_revision": stale["revision"]}, headers=owner)
    assert refused.status_code == 422 and "changed since they were brought in" in refused.json()["detail"]
    fresh = client.post(f"{url}/import", json={"expected_revision": stale["revision"]}, headers=owner).json()
    assert fresh["imported"] == {"workers": 1, "changed": 1, "removed": 0} and fresh["rows"][0]["entry"]["ordinary"] == 38.0
    assert fresh["rows"][0]["hours_reviewed"] is False and fresh["stale"] == []  # changed hours must be looked at again
    not_ready = client.post(f"{url}/finalise", json={"expected_revision": fresh["revision"]}, headers=owner)
    assert not_ready.status_code == 422 and "Tom Okafor" in not_ready.json()["detail"]
    # An approved day corrected by the office goes back to waiting, so it leaves the worksheet at the next import.
    client.patch(f"{P}/timesheets/{ids[0]}", json={"shifts": [{"start": "07:00", "finish": "12:00"}]}, headers=owner)
    assert client.get(url, headers=owner).json()["stale"] == [tom.id]
    client.post(f"{P}/timesheets/approve", json={"ids": [ids[0]]}, headers=owner)
    current = client.post(f"{url}/import", json={"expected_revision": fresh["revision"]}, headers=owner).json()
    assert current["rows"][0]["entry"]["ordinary"] == 35.4  # 30.4 + 5.0
    saved = client.put(url, json={"expected_revision": current["revision"], "rows": reviewed(current)}, headers=owner).json()
    final = client.post(f"{url}/finalise", json={"expected_revision": saved["revision"]}, headers=owner).json()
    assert final["status"] == "finalised"

    # What the workbench will not do. Each refusal says why, is recorded, and sends nothing.
    for path, words in ((f"{P}/banking/batch/{run['id']}/download", "Bank payment files are not produced"), (f"{url}/submit/stp", "not connected"), (f"{url}/submit/super", "not submitted from here"),
                        (f"{url}/payslips/deliver", "not emailed or texted"), (f"{P}/reset", "There is no reset")):
        answer = client.post(path, headers=owner)
        assert answer.status_code == 409 and words in answer.json()["detail"], path
    assert client.post(f"{url}/submit/bank", headers=owner).status_code == 422
    assert client.delete(url, headers=owner).status_code == 405  # a run cannot be deleted
    assert len(db.scalars(select(AuditLog).where(AuditLog.action == "payroll.blocked")).all()) == 5 and db.scalars(select(OutboxMessage)).all() == []
    status = client.get(f"{P}/workbench/status", headers=owner).json()
    assert status["mode"] == "test" and status["tax_table"]["verified"] is False and set(status["not_available"]) == {"bank_file", "stp", "super", "deliver", "reset"}

    # Reopen: needs a reason, releases the days, keeps the event. Finalise again gives a new hash for the same figures plus history.
    assert client.post(f"{url}/reopen", json={"expected_revision": final["revision"]}, headers=owner).status_code == 422
    reopened = client.post(f"{url}/reopen", json={"expected_revision": final["revision"], "reason": "Wrong payday entered"}, headers=owner).json()
    assert reopened["status"] == "draft" and [h["event"] for h in reopened["history"]] == ["finalised", "reopened"] and reopened["history"][1]["by"] == "Olive Owner"
    db.expire_all()
    assert all(d.status == "approved" and d.pay_run_id is None for d in db.scalars(select(TimesheetDay)).all())
    final = client.post(f"{url}/finalise", json={"expected_revision": reopened["revision"]}, headers=owner).json()

    # Completion evidence is typed in by a person; it is labelled as such and never replaces an earlier record silently.
    today = dt.date.today()
    evidence = lambda kind, **body: client.post(f"{url}/completion/{kind}", json={"reference": "BANK-REF-001", "completed_on": PAYDAY, "confirm": True, **body}, headers=owner)  # noqa: E731
    assert evidence("payment", confirm=False).status_code == 422
    assert evidence("payment", completed_on="2026-10-01").status_code == 422 and evidence("payment", completed_on=(today + dt.timedelta(days=3)).isoformat()).status_code == 422
    assert evidence("bribe").status_code == 422
    recorded = evidence("payment", note="Paid from the business account by bank transfer").json()
    assert recorded["evidence"][0]["kind"] == "payment" and recorded["evidence"][0]["run_hash"] == final["hash"] and recorded["evidence"][0]["source"] == "recorded by hand"
    assert recorded["evidence"][0]["by"] == "Olive Owner" and "did not pay, lodge or submit" in recorded["evidence_note"]
    assert evidence("payment").status_code == 409
    corrected = evidence("payment", reference="BANK-REF-002", correction_reason="Typed the wrong reference").json()
    assert [(e["reference"], e["corrects"]) for e in corrected["evidence"]] == [("BANK-REF-001", None), ("BANK-REF-002", 1)]
    assert evidence("stp", reference="Lodged in the accounting package, receipt 77").status_code == 200
    # With a payment recorded the run can no longer be reopened.
    locked = client.post(f"{url}/reopen", json={"expected_revision": corrected["revision"], "reason": "Try again"}, headers=owner)
    assert locked.status_code == 409 and "extra run" in locked.json()["detail"]
    draft = client.post(f"{P}/runs", json={"week_start": (WEEK + dt.timedelta(days=7)).isoformat()}, headers=owner).json()
    assert client.post(f"{P}/runs/{draft['id']}/completion/payment", json={"reference": "X-1", "completed_on": PAYDAY, "confirm": True}, headers=owner).status_code == 409  # not finalised
    assert client.post(f"{P}/runs/{draft['id']}/payslips/release", json={"release": True}, headers=owner).status_code == 409


def test_payroll_routes_need_payroll_access_and_stay_inside_the_organisation(world, client, db):
    owner, admin, hseq, worker = world.auth("owner1"), world.auth("admin1"), world.auth("hseq1"), world.auth("worker1")
    tom = world.add_worker("Tom", "Okafor")
    theirs = world.add_worker("Tom", "Okafor", org=world.org2)
    profile(client, owner, tom)
    settings(client, owner)
    run = client.post(f"{P}/runs", json={"week_start": WEEK.isoformat()}, headers=owner).json()
    url = f"{P}/runs/{run['id']}"
    reads = [f"{P}/workbench/status", f"{P}/settings", f"{P}/profiles", f"{P}/profiles/{tom.id}", f"{P}/runs", url, f"{url}/report.csv", f"{url}/payslips/{tom.id}/pdf"]
    writes = [("put", f"{P}/settings", {"expected_revision": 1, "rules": RULES}), ("put", f"{P}/profiles/{tom.id}", {"expected_revision": 1}), ("put", f"{P}/profiles/{tom.id}/bank", {"account_name": "T Okafor", "bsb": "067-000", "account_number": "12345678"}),
              ("post", f"{P}/profiles/{tom.id}/bank/reveal", None), ("delete", f"{P}/profiles/{tom.id}/bank", None), ("post", f"{P}/runs", {"week_start": WEEK.isoformat()}), ("put", url, {"expected_revision": 1}),
              ("post", f"{url}/import", {"expected_revision": 1}), ("post", f"{url}/finalise", {"expected_revision": 1}), ("post", f"{url}/reopen", {"expected_revision": 1, "reason": "because"}),
              ("post", f"{url}/payslips/release", {"release": True}), ("post", f"{url}/completion/payment", {"reference": "abc", "completed_on": PAYDAY, "confirm": True}),
              ("post", f"{P}/banking/batch/{run['id']}/download", None), ("post", f"{url}/submit/stp", None), ("post", f"{url}/payslips/deliver", None), ("post", f"{P}/reset", None)]

    def write_codes(headers):
        return {getattr(client, m)(u, headers=headers, **({"json": b} if b is not None else {})).status_code for m, u, b in writes}

    # An ordinary admin, an HSEQ lead and a worker are refused everywhere.
    for headers in (admin, hseq, worker):
        assert {client.get(u, headers=headers).status_code for u in reads} == {403} and write_codes(headers) == {403}
    # A view grant reads (nothing sensitive is in what it reads) and changes nothing.
    client.put(f"/api/users/{world.id('admin1')}/payroll-access", json={"level": "view"}, headers=owner)
    assert all(client.get(u, headers=admin).status_code in (200, 409) for u in reads) and write_codes(admin) == {403}
    seen = client.get(f"{P}/profiles/{tom.id}", headers=admin).json()
    assert seen["can_edit"] is False and seen["hourly_rate"] == 30 and seen["bank"] == {"is_set": False, "hint": None}

    # Another organisation's payroll owner cannot reach this organisation's workers, profiles or runs.
    world.org2.payroll_owner_user_id = world.id("admin2")
    db.commit()
    other = world.auth("admin2")
    assert client.get(f"{P}/runs", headers=other).json()["items"] == [] and client.get(f"{P}/profiles", headers=other).json()["items"][0]["worker_id"] == theirs.id
    for method, path, body in [("get", url, None), ("get", f"{P}/profiles/{tom.id}", None), ("put", f"{P}/profiles/{tom.id}", {"expected_revision": 1}), ("post", f"{P}/profiles/{tom.id}/bank/reveal", None),
                               ("put", url, {"expected_revision": 1}), ("post", f"{url}/finalise", {"expected_revision": 1}), ("get", f"{url}/report.csv", None), ("get", f"{url}/payslips/{tom.id}/pdf", None),
                               ("post", f"{url}/completion/payment", {"reference": "abc", "completed_on": PAYDAY, "confirm": True}), ("post", f"{url}/payslips/release", {"release": True})]:
        assert getattr(client, method)(path, headers=other, **({"json": body} if body is not None else {})).status_code == 404, path
    assert client.put(url, json={"expected_revision": run["revision"], "rows": {theirs.id: {"manual": {"ordinary": 1}}}}, headers=owner).status_code == 404  # not this organisation's worker


def test_settings_profiles_and_bank_details(world, client, db):
    owner = world.auth("owner1")
    tom = world.add_worker("Tom", "Okafor", link="worker1")
    start = client.get(f"{P}/settings", headers=owner).json()
    assert start["revision"] == 0 and start["using_defaults"] is True and start["rules"]["super_percent"] == 12 and "not a ruling on any award" in start["defaults_note"]
    assert settings(client, owner, employer_abn="12345678901").status_code == 422  # fails the ABN check digit
    assert settings(client, owner, rules={"night_start": "18:00", "night_end": "18:00"}).status_code == 422
    assert settings(client, owner, rules={"night_start": "6pm"}).status_code == 422 and settings(client, owner, rules={"ot1_multiplier": 0.5}).status_code == 422
    two = [{"code": "TOOL", "name": "Tool allowance", "rate": 12.5, "taxable": True, "superable": False}, {"code": "tool", "name": "Again", "rate": 1}]
    assert settings(client, owner, rules={"earning_rules": two}).status_code == 422
    assert settings(client, owner, rules={"earning_rules": [{"code": "LH", "name": "Leading hand", "basis": "base_rate"}]}).status_code == 422
    saved = settings(client, owner, rules={"earning_rules": two[:1], "daily_ordinary_hours": 8}, week_start_day=3).json()
    assert saved["revision"] == 1 and saved["week_start_day"] == 3 and saved["employer_abn"] == ABN and saved["history"] == []
    assert settings(client, owner).status_code == 409  # saved from an out-of-date screen
    later = settings(client, owner, revision=1).json()
    assert later["revision"] == 2 and [(h["revision"], h["changed_by"]) for h in later["history"]] == [(1, "Olive Owner")]
    # The saved rules drive timesheets: with an 8 hour day, eight hours are all ordinary; then back to 7.6.
    settings(client, owner, revision=2, rules={"daily_ordinary_hours": 8})
    made = client.post(f"{P}/timesheets", json={"worker_id": tom.id, "work_date": WEEK.isoformat(), "shifts": [{"start": "07:00", "finish": "15:30", "break_minutes": 30}]}, headers=owner).json()
    assert made["hours"]["ordinary"] == 8.0 and made["hours"]["ot1"] == 0.0

    # Profiles: revision guard, casual rates only for casuals, and the audit trail names fields, not figures.
    assert client.put(f"{P}/profiles/nope", json={"expected_revision": 0}, headers=owner).status_code == 404
    assert client.put(f"{P}/profiles/{tom.id}", json={"expected_revision": 0, "hourly_rate": -1}, headers=owner).status_code == 422
    assert client.put(f"{P}/profiles/{tom.id}", json={"expected_revision": 0, "tax_mode": "guess"}, headers=owner).status_code == 422
    first = profile(client, owner, tom, casual_rates={"base_rate": 30, "loading_percent": 25})
    assert first["revision"] == 1 and first["casual_rates"] is None and first["base_rate"] == 30.0  # full time: casual rates are dropped
    assert client.put(f"{P}/profiles/{tom.id}", json={"expected_revision": 0, **PROFILE}, headers=owner).status_code == 409
    casual = profile(client, owner, tom, revision=1, employment_type="casual", casual_rates={"base_rate": 30, "loading_percent": 25, "ot1": 50})
    assert casual["base_rate"] == 37.5 and casual["casual_rates"]["ot1"] == 50
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "payroll.profile_changed").order_by(AuditLog.created_at.desc())).first()
    assert set(entry.detail) == {"fields"} and "casual_rates" in entry.detail["fields"] and "37.5" not in json.dumps(entry.detail)
    listed = client.get(f"{P}/profiles", headers=owner).json()["items"]
    assert [(p["name"], p["has_profile"]) for p in listed] == [("Tom Okafor", True)]

    # Bank details: validated, encrypted at rest, never in a profile or a list, revealed only on purpose and on the record.
    bank = f"{P}/profiles/{tom.id}/bank"
    for bad in ({"account_name": "T", "bsb": "067000", "account_number": "12345678"}, {"account_name": "Tom Okafor", "bsb": "0670", "account_number": "12345678"},
                {"account_name": "Tom Okafor", "bsb": "067-000", "account_number": "12ab"}):
        assert client.put(bank, json=bad, headers=owner).status_code == 422
    assert client.post(f"{bank}/reveal", headers=owner).status_code == 404
    stored = client.put(bank, json={"account_name": "Tom Okafor", "bsb": "067-000", "account_number": "12345678"}, headers=owner).json()
    assert stored == {"is_set": True, "hint": "Account ending 678"}
    row = db.get(PayProfile, tom.id)
    assert "12345678" not in row.bank and "067000" not in row.bank and "Okafor" not in row.bank
    for body in (client.get(f"{P}/profiles/{tom.id}", headers=owner).text, client.get(f"{P}/profiles", headers=owner).text):
        assert "12345678" not in body and "067000" not in body
    assert client.post(f"{bank}/reveal", headers=owner).json() == {"account_name": "Tom Okafor", "bsb": "067000", "account_number": "12345678"}
    trail = db.scalars(select(AuditLog).where(AuditLog.action.in_(("payroll.bank_changed", "payroll.bank_revealed")))).all()
    assert len(trail) == 2 and all("12345678" not in json.dumps(a.detail or {}) for a in trail)
    assert client.post(f"{bank}/reveal", headers=world.auth("worker1")).status_code == 403  # not even your own, through this route
    assert client.delete(bank, headers=owner).status_code == 204 and client.get(f"{P}/profiles/{tom.id}", headers=owner).json()["bank"]["is_set"] is False
    assert db.scalars(select(PayRun)).all() == []
