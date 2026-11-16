"""Fuel: weekly diesel rate, imports, attribution, reports and the anomaly inbox (stage 6c)."""
import datetime as dt
import io
import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app import fuel, integrations
from app.models import AuditLog, FuelAnomaly, FuelTransaction, FuelWeeklyPrice, OutboxMessage, ProviderCall
from app.people import today_for

F = "/api/fleet/fuel"
A = "/api/assets"
I = "/api/integrations"
HEAD = "Transaction Id,Date,Time,Card Number,Description,Registration,From,Litres,Fuel Type,Odometer,Total Price,Driver Authorisation,Unit Price"


def csv_file(rows, head=HEAD):
    return {"file": ("fuel.csv", io.BytesIO(("\n".join([head, *rows]) + "\n").encode()), "text/csv")}


def upload(client, headers, rows, *, dry_run=False, head=HEAD, timezone=""):
    return client.post(f"{F}/import-csv", files=csv_file(rows, head), data={"dry_run": str(dry_run).lower(), "timezone": timezone}, headers=headers)


def row(txn, day, clock="07:10", card="C1", litres="50", rego="", fuel_type="Diesel", odo="", total="", desc="Ute"):
    return f"{txn},{day.strftime('%d/%m/%Y')},{clock},{card},{desc},{rego},Depot,{litres},{fuel_type},{odo},{total},,"


def add_asset(client, headers, **body):
    r = client.post(A, json={"name": "Ute 01", "kind": "vehicle", **body}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def enter_week(client, headers, week_start, cents=(200, 201, 202, 203, 204, 205, 206), note="Typed from the FuelCheck page"):
    days = [{"date": d.isoformat(), "cents": c} for d, c in zip(fuel.source_week(week_start), cents)]
    return client.put(f"{F}/weekly-diesel", json={"week_start": week_start.isoformat(), "days": days, "note": note}, headers=headers)


@pytest.fixture
def weeks(world):
    today = today_for(world.org1)
    this = fuel.monday(today)
    return type("Weeks", (), {"today": today, "this": this, "last": this - dt.timedelta(days=7), "older": this - dt.timedelta(days=28)})


@pytest.fixture
def fuelcheck(monkeypatch):
    state = {"entries": [], "calls": 0, "down": False}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == fuel.FUELCHECK_URL and json.loads(request.content) == fuel.FUELCHECK_BODY
        state["calls"] += 1
        if state["down"]:
            return httpx.Response(503)
        return httpx.Response(200, json={"AveragePrices": state["entries"]})

    monkeypatch.setattr(integrations, "transport", httpx.MockTransport(handler))
    return state


def dl(day, price, code="DL", period="Month"):
    return {"Code": code, "Period": period, "Captured": f"{day.isoformat()}T00:00:00", "Price": price}


# ---------- the weekly rate (business rule B04) ----------

def test_weekly_rate_matches_the_fixture_and_is_fixed_for_good(world, client, db, weeks, fuelcheck):
    admin, hseq, sup, other = world.auth("admin1"), world.auth("hseq1"), world.auth("super1"), world.auth("admin2")
    week = weeks.older
    assert upload(client, admin, [row("T1", week, litres="10"), row("T2", week + dt.timedelta(days=6), litres="25"), row("T3", week + dt.timedelta(days=7), litres="40")]).status_code == 200
    pending = client.get(f"{F}/weekly-diesel", headers=sup).json()
    assert [(w["week_start"], w["fills"], w["litres"]) for w in pending["pending_weeks"]] == [((week + dt.timedelta(days=7)).isoformat(), 1, "40.0"), (week.isoformat(), 2, "35.0")]
    assert pending["this_week"]["price"] is None and pending["can_enter"] is False

    # Entering a week by hand: admin only, exactly the seven source days, a Monday that has begun.
    assert enter_week(client, hseq, week).status_code == 403
    assert enter_week(client, admin, week + dt.timedelta(days=1)).status_code == 422
    assert enter_week(client, admin, weeks.this + dt.timedelta(days=7)).status_code == 422
    wrong = [{"date": (week + dt.timedelta(days=i)).isoformat(), "cents": 200} for i in range(7)]  # the week itself, not the week before
    assert client.put(f"{F}/weekly-diesel", json={"week_start": week.isoformat(), "days": wrong, "note": "wrong days"}, headers=admin).status_code == 422
    assert enter_week(client, admin, week, cents=(200, 201, 202, 203, 204, 205, 20600)).status_code == 422  # not a believable price
    done = enter_week(client, admin, week)
    assert done.status_code == 200, done.text
    assert done.json()["rate"] == "2.0300" and done.json()["priced"] == 2 and done.json()["source"] == "manual"
    assert done.json()["source_week_start"] == (week - dt.timedelta(days=7)).isoformat() and done.json()["source_week_end"] == (week - dt.timedelta(days=1)).isoformat()
    fills = {t.source_id: t for t in db.scalars(select(FuelTransaction)).all()}
    assert (fills["T1"].report_cost, fills["T2"].report_cost, fills["T3"].report_cost) == (Decimal("20.30"), Decimal("50.75"), None)
    report = client.get(f"{F}/reports", params={"date_from": week.isoformat(), "date_to": (week + dt.timedelta(days=6)).isoformat()}, headers=sup).json()
    assert report["totals"]["litres"] == "35.0" and report["totals"]["cost"] == "71.05" and report["totals"]["complete"] is True

    # Fixed for good: entering it again is refused, and fresh source data for the same days does not change it.
    assert enter_week(client, admin, week, cents=(300, 300, 300, 300, 300, 300, 300)).status_code == 409
    client.put(f"{I}/fuelcheck", json={"mode": "live"}, headers=admin)
    fuelcheck["entries"] = [dl(d, 250) for d in fuel.source_week(week)]
    again = client.post(f"{F}/weekly-diesel/refresh", headers=hseq).json()
    assert again["ok"] is True and all(r["outcome"] != "locked" or r["week_start"] != week.isoformat() for r in again["results"])
    kept, outcome = fuel.lock_week(db, world.org1, week, {d: Decimal("250") for d in fuel.source_week(week)}, source="fuelcheck", source_url=None, source_note=None,
                                  fetched_at=None, actor_id=None)
    assert outcome == "already_locked" and kept.rate == Decimal("2.0300")
    db.commit()
    db.expire_all()
    assert db.scalars(select(FuelWeeklyPrice)).one().rate == Decimal("2.0300") and fills["T1"].report_cost == Decimal("20.30")
    # There is no route that edits or deletes a rate.
    price_id = done.json()["id"]
    for method in ("delete", "patch", "put"):
        assert getattr(client, method)(f"{F}/weekly-diesel/{price_id}", headers=admin).status_code in (404, 405)
    # The other organisation has no rate and sees none of this.
    theirs = client.get(f"{F}/weekly-diesel", headers=other).json()
    assert theirs["ledger"] == [] and theirs["pending_weeks"] == []
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "fuel.week_locked")).one()
    assert entry.detail["rate"] == "2.0300" and entry.detail["source"] == "manual"


def test_refresh_reads_diesel_only_needs_all_seven_days_and_never_estimates(world, client, db, weeks, fuelcheck):
    admin, hseq, sup = world.auth("admin1"), world.auth("hseq1"), world.auth("super1")
    upload(client, admin, [row("T1", weeks.this, litres="100", clock="00:01")])
    off = client.post(f"{F}/weekly-diesel/refresh", headers=hseq).json()
    assert off["ok"] is False and "switched off" in off["error"] and fuelcheck["calls"] == 0
    client.put(f"{I}/fuelcheck", json={"mode": "live"}, headers=admin)
    assert client.post(f"{F}/weekly-diesel/refresh", headers=sup).status_code == 403
    assert client.post(f"{F}/weekly-diesel/refresh", headers=world.auth("worker1")).status_code == 403
    days = fuel.source_week(weeks.this)
    # Six of seven days, plus noise that must be ignored: petrol, a different period, an absurd price, a clash.
    fuelcheck["entries"] = [dl(d, 210.5) for d in days[:6]] + [dl(days[6], 150, code="U91"), dl(days[6], 150, period="Day"), dl(days[6], 9999),
                                                               dl(days[0] - dt.timedelta(days=3), 199), dl(days[0] - dt.timedelta(days=3), 201)]
    short = client.post(f"{F}/weekly-diesel/refresh", headers=hseq).json()
    assert short["ok"] is True and short["priced"] == 0
    assert short["results"] == [{"week_start": weeks.this.isoformat(), "outcome": "missing_days", "rate": None, "missing": [days[6].isoformat()]}]
    assert any("two different prices" in w for w in short["warnings"])
    assert db.scalars(select(FuelWeeklyPrice)).all() == []
    report = client.get(f"{F}/reports", headers=sup).json()
    assert report["totals"]["cost"] == "0.00" and report["totals"]["complete"] is False and report["totals"]["unpriced_litres"] == "100.0" and report["totals"]["unpriced_fills"] == 1
    assert "waiting for a weekly rate" in report["notes"][0] and report["items"][0]["cost"] is None  # unknown, not zero and not a guess
    stats = client.get(f"{F}/stats", headers=sup).json()
    assert stats["rate"] is None and stats["rate_pending"] is True and stats["fills_waiting_for_rate"] == 1

    fuelcheck["entries"].append(dl(days[6], 213.9))
    done = client.post(f"{F}/weekly-diesel/refresh", headers=hseq).json()
    expected = fuel.compute_rate([Decimal("210.5")] * 6 + [Decimal("213.9")])
    assert done["results"] == [{"week_start": weeks.this.isoformat(), "outcome": "locked", "rate": f"{expected:.4f}"}] and done["priced"] == 1
    price = db.scalars(select(FuelWeeklyPrice)).one()
    assert price.source == "fuelcheck" and price.source_url == fuel.FUELCHECK_URL and price.fuel_code == "DL" and len(price.daily_cents) == 7 and price.timezone == "Australia/Hobart"
    assert db.scalars(select(FuelTransaction)).one().report_cost == fuel.money(Decimal("100") * expected)
    fuelcheck["down"] = True
    failed = client.post(f"{F}/weekly-diesel/refresh", headers=hseq).json()
    assert failed["ok"] is False and "HTTP 503" in failed["error"]
    assert client.post(f"{I}/fuelcheck/test-connection", headers=admin).json()["ok"] is False
    fuelcheck["down"] = False
    assert "7 days" in client.post(f"{I}/fuelcheck/test-connection", headers=admin).json()["message"] or client.post(f"{I}/fuelcheck/test-connection", headers=admin).json()["ok"] is True


# ---------- importing ----------

def test_csv_import_previews_upserts_and_reports_bad_rows(world, client, db, weeks):
    admin, hseq = world.auth("admin1"), world.auth("hseq1")
    day = weeks.last
    assert upload(client, hseq, [row("T1", day)]).status_code == 403  # importing is for admins
    assert upload(client, admin, ["1,2"], head="Card,Notes").status_code == 422
    assert upload(client, admin, [row("T1", day)], timezone="Mars/Olympus").status_code == 422
    rows = [row("T1", day, litres="52.4", odo="48000", total="110.04"), row("T2", day, litres="-5"), row("T3", day, litres="NaN"), row("T4", day, litres=""),
            f"T5,31/02/2026,07:00,C1,Ute,,Depot,40,Diesel,,,,", row("T6", weeks.today + dt.timedelta(days=3)), row("T7", day, litres="9999"), row("T1", day, litres="52.4"),
            row("T8", day, clock="", litres="20")]
    preview = upload(client, admin, rows, dry_run=True).json()
    assert preview["status"] == "previewed" and preview["counts"]["new"] == 1 and preview["counts"]["errors"] == 7 and preview["counts"]["unchanged"] == 1
    assert [e["row"] for e in preview["errors"]] == [3, 4, 5, 6, 7, 8, 10]
    assert "Litres" in preview["errors"][0]["error"] and "not understood" in preview["errors"][3]["error"] and "future" in preview["errors"][4]["error"]
    assert db.scalars(select(FuelTransaction)).all() == [] and client.get(f"{F}/batches", headers=admin).json()["items"] == []
    done = upload(client, admin, rows).json()
    assert done["status"] == "committed" and done["counts"]["new"] == 1 and done["timezone"] == "Australia/Hobart"
    txn = db.scalars(select(FuelTransaction)).one()
    assert (txn.source_system, txn.source_id, txn.litres, txn.source_total, txn.odometer_km) == ("smartfill", "T1", Decimal("52.400"), Decimal("110.04"), 48000)
    assert txn.report_cost is None  # the supplier's amount is evidence, never the reporting cost

    # The same file again adds nothing. A fuller copy fills gaps; a different value for something already known is counted, not applied.
    assert upload(client, admin, rows).json()["counts"]["new"] == 0
    enter_week(client, admin, day)
    db.expire_all()
    cost = txn.report_cost
    assert cost == Decimal("106.37")
    fuller = upload(client, admin, [row("T1", day, litres="99", odo="51000", total="110.04", rego="abc123")]).json()
    assert fuller["counts"]["updated"] == 1 and fuller["counts"]["conflicts"] == 1
    db.expire_all()
    assert txn.registration == "ABC123" and txn.odometer_km == 48000 and txn.litres == Decimal("52.400") and txn.report_cost == cost
    assert len(db.scalars(select(FuelTransaction)).all()) == 1
    batches = client.get(f"{F}/batches", headers=world.auth("super1")).json()["items"]
    assert len(batches) == 3 and batches[0]["created_by"] == "Alex Rowe" and batches[0]["filename"] == "fuel.csv"
    assert client.get(f"{F}/batches/{batches[0]['id']}", headers=world.auth("admin2")).status_code == 404

    # Times carry no offset, so the zone is stated and recorded: 23:30 in Brisbane is already the next day in Hobart in summer.
    late = upload(client, admin, [row("T9", day, clock="23:30")], timezone="Australia/Brisbane").json()
    assert late["timezone"] == "Australia/Brisbane" and late["counts"]["new"] == 1
    t9 = db.scalars(select(FuelTransaction).where(FuelTransaction.source_id == "T9")).one()
    assert t9.local_date == day + dt.timedelta(days=1) and t9.local_time == "00:30"
    # No transaction number column: a stable key is derived and the reader is warned.
    head = "Date,Time,Card Number,Litres"
    first = upload(client, admin, [f"{day.strftime('%d/%m/%Y')},09:00,C7,30"], head=head).json()
    assert first["counts"]["derived_ids"] == 1 and "no transaction number column" in first["warnings"][0]
    assert upload(client, admin, [f"{day.strftime('%d/%m/%Y')},09:00,C7,30"], head=head).json()["counts"]["new"] == 0


def test_attribution_is_labelled_and_unmatched_fuel_still_counts(world, client, db, weeks):
    admin, hseq, sup, other = world.auth("admin1"), world.auth("hseq1"), world.auth("super1"), world.auth("admin2")
    ute = add_asset(client, admin, registration="ABC123")
    tipper = add_asset(client, admin, name="Tipper 02")
    add_asset(client, admin, name="Ute", registration="DUP1")
    add_asset(client, admin, name="Ute spare", registration="DUP1")  # two assets, one registration: not a match
    theirs = add_asset(client, other, name="Ute 01", registration="ABC123")
    driver = world.add_worker("Kim", "Nguyen")
    day = weeks.last
    upload(client, admin, [row("T1", day, card="C1", rego="abc 123", litres="50"), row("T2", day, card="C2", litres="30", desc="Tipper 02"),
                           row("T3", day, card="C2", litres="20", desc="Tipper 02"), row("T4", day, card="C3", rego="DUP1", litres="10")])
    listed = {t["source_id"]: t for t in client.get(f"{F}/transactions", headers=sup).json()["items"]}
    assert (listed["T1"]["attribution"], listed["T1"]["asset"]) == ("direct", "Ute 01 (ABC123)") and listed["T1"]["asset_id"] == ute["id"] != theirs["id"]
    assert listed["T2"]["attribution"] == "unlinked" and listed["T2"]["asset"] is None  # a matching description is not a match
    assert listed["T4"]["attribution"] == "unlinked"
    by_vehicle = client.get(f"{F}/reports", params={"date_from": day.isoformat(), "date_to": day.isoformat()}, headers=sup).json()
    assert [(r["label"], r["litres"]) for r in by_vehicle["items"]] == [("Ute 01 (ABC123)", "50.0"), ("Not matched to a vehicle", "60.0")] and by_vehicle["totals"]["litres"] == "110.0"

    cards = {c["card_number"]: c for c in client.get(f"{F}/cards", headers=sup).json()["items"]}
    assert cards["C2"]["fills"] == 2 and cards["C2"]["unmatched_fills"] == 2 and cards["C2"]["label"] == "Tipper 02" and cards["C2"]["asset"] is None
    assert client.patch(f"{F}/cards/C2", json={"asset_id": tipper["id"]}, headers=sup).status_code == 403
    assert client.patch(f"{F}/cards/C2", json={"asset_id": theirs["id"]}, headers=admin).status_code == 404
    assert client.patch(f"{F}/cards/C2", json={"asset_id": tipper["id"]}, headers=other).status_code == 404
    kept = client.patch(f"{F}/cards/C2", json={"asset_id": tipper["id"], "worker_id": driver.id}, headers=hseq).json()
    assert kept["fills_matched"] == 0  # existing fills are only re-matched when asked
    done = client.patch(f"{F}/cards/C2", json={"asset_id": tipper["id"], "worker_id": driver.id, "apply_to_unmatched": True}, headers=hseq).json()
    assert done["fills_matched"] == 2
    t2 = client.get(f"{F}/transactions/{listed['T2']['id']}", headers=sup).json()
    assert t2["attribution"] == "card" and t2["attribution_label"] == "From the fuel card's assignment" and t2["asset"] == "Tipper 02" and t2["worker"] == "Kim Nguyen"
    unlinked = next(r for r in t2["reasons"] if r["rule"] == "unlinked")
    assert unlinked["status"] == "resolved" and unlinked["note"].startswith("Corrected") and unlinked["acted_by"] == "the portal"
    # A later fill on the card is matched as it arrives, still labelled as an inference.
    upload(client, admin, [row("T5", day, card="C2", litres="15")])
    assert db.scalars(select(FuelTransaction).where(FuelTransaction.source_id == "T5")).one().attribution == "card"

    # A person's match records who, why and what it was before, and survives a re-import.
    assert client.post(f"{F}/transactions/{listed['T4']['id']}/match", json={"asset_id": ute["id"], "reason": "x"}, headers=hseq).status_code == 422
    assert client.post(f"{F}/transactions/{listed['T4']['id']}/match", json={"asset_id": ute["id"], "reason": "Driver confirmed"}, headers=sup).status_code == 403
    assert client.post(f"{F}/transactions/{listed['T4']['id']}/match", json={"asset_id": ute["id"], "reason": "Driver confirmed"}, headers=other).status_code == 404
    matched = client.post(f"{F}/transactions/{listed['T4']['id']}/match", json={"asset_id": ute["id"], "reason": "Driver confirmed"}, headers=hseq).json()
    assert matched["attribution"] == "manual" and matched["attributed_by"] == "Sam Keating" and matched["attribution_note"] == "Driver confirmed"
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "fuel.attributed")).one()
    assert entry.detail["before"] == {"asset_id": None, "worker_id": None, "attribution": "unlinked"} and entry.detail["reason"] == "Driver confirmed"
    upload(client, admin, [row("T4", day, card="C3", rego="DUP1", litres="10", odo="5")])
    db.expire_all()
    assert db.get(FuelTransaction, listed["T4"]["id"]).attribution == "manual"
    by_driver = client.get(f"{F}/reports", params={"group": "driver", "date_from": day.isoformat(), "date_to": day.isoformat()}, headers=sup).json()
    assert [(r["label"], r["litres"]) for r in by_driver["items"]] == [("Kim Nguyen", "65.0"), ("No driver recorded", "60.0")]


# ---------- anomalies ----------

def test_anomaly_inbox_one_row_per_fill_with_reasons_and_recorded_actions(world, client, db, weeks):
    admin, hseq, sup, other = world.auth("admin1"), world.auth("hseq1"), world.auth("super1"), world.auth("admin2")
    add_asset(client, admin, registration="ABC123", tank_capacity_l=80)
    day = weeks.older
    steady = [row(f"S{i}", day + dt.timedelta(days=i), rego="ABC123", litres=str(50 + i % 2), odo=str(10000 + i * 500)) for i in range(6)]
    odd = [row("X1", day + dt.timedelta(days=7), rego="ABC123", litres="95", odo="9000", clock="22:15"),      # too much, at night, odometer backwards
           row("X2", day + dt.timedelta(days=8), rego="ABC123", litres="50", odo=""),                         # no odometer
           row("X3", day + dt.timedelta(days=9), rego="ABC123", litres="12", fuel_type="ULP 91", odo="14000"),  # not diesel
           row("X4", day + dt.timedelta(days=9), card="C9", litres="40", clock="07:20")]                                     # nobody's card
    done = upload(client, admin, steady + odd).json()
    assert done["counts"]["new"] == 10 and done["counts"]["new_flags"] == 7 and done["counts"]["not_diesel"] == 1
    assert db.scalars(select(OutboxMessage)).all() == []  # detection tells nobody

    inbox = client.get(f"{F}/anomalies", headers=sup).json()
    assert inbox["total"] == 4 and inbox["can_edit"] is False and [i["source_id"] for i in inbox["items"]] == ["X4", "X3", "X2", "X1"]
    x1 = inbox["items"][3]
    assert [r["rule"] for r in x1["reasons"]] == ["capacity_exceed", "reading_regress", "stat_spike", "unusual_hour"]  # worst first, each once
    assert x1["reasons"][0]["evidence"] == ["95.0 litres into a 80 litre tank"] and "but it read 12,500 at the fill on" in x1["reasons"][1]["evidence"][0]
    assert "the last 6 fills averaged 50.5" in x1["reasons"][2]["evidence"][0] and "22:15" in x1["reasons"][3]["evidence"][0]
    assert inbox["items"][2]["reasons"][0]["label"] == "No odometer reading" and inbox["items"][1]["reasons"][0]["label"] == "Not diesel"
    assert inbox["items"][1]["pricing"] == "not_diesel" and inbox["items"][1]["report_cost"] is None
    assert "Card C9 is not assigned to a vehicle" in inbox["items"][0]["reasons"][0]["evidence"][0]
    # Importing again updates the same flags; it never adds copies.
    assert upload(client, admin, steady + odd).json()["counts"]["new_flags"] == 0
    assert len(db.scalars(select(FuelAnomaly)).all()) == 7
    assert [i["source_id"] for i in client.get(f"{F}/anomalies", params={"rule": "unusual_hour"}, headers=sup).json()["items"]] == ["X1"]
    assert client.get(f"{F}/anomalies", headers=other).json()["total"] == 0 and client.get(f"{F}/anomalies", headers=world.auth("worker1")).status_code == 403
    ids = client.get(f"{F}/anomalies/matching-ids", headers=sup).json()
    assert len(ids["ids"]) == 4 and ids["truncated"] is False

    # Actions need the edit right, are recorded, and never touch the measurements.
    assert client.post(f"{F}/anomalies/{x1['id']}/resolve", json={}, headers=sup).status_code == 403
    assert client.post(f"{F}/anomalies/{x1['id']}/dismiss", json={}, headers=hseq).status_code == 422  # say why
    assert client.post(f"{F}/anomalies/{x1['id']}/dismiss", json={"note": "Mine now"}, headers=other).status_code == 404
    assert client.post(f"{F}/anomalies/{x1['id']}/dismiss", json={"note": "Night shift fill", "rules": ["unusual_hour"]}, headers=hseq).json() == {"changed": 1, "unchanged": 0, "not_found": 0}
    detail = client.get(f"{F}/transactions/{x1['id']}", headers=sup).json()
    hour = next(r for r in detail["reasons"] if r["rule"] == "unusual_hour")
    assert (hour["status"], hour["note"], hour["acted_by"]) == ("dismissed", "Night shift fill", "Sam Keating") and detail["litres"] == "95"
    assert [r["status"] for r in detail["reasons"]].count("open") == 3
    theirs_id = add_asset(client, other)["id"]
    bulk = client.post(f"{F}/anomalies/bulk-resolve", json={"txn_ids": [x1["id"], x1["id"], inbox["items"][2]["id"], "no-such-fill", theirs_id], "note": "Checked with the driver"}, headers=hseq).json()
    assert bulk == {"changed": 2, "unchanged": 0, "not_found": 2}
    assert client.post(f"{F}/anomalies/bulk-resolve", json={"txn_ids": [x1["id"]]}, headers=hseq).json() == {"changed": 0, "unchanged": 1, "not_found": 0}  # doing it twice changes nothing
    assert client.get(f"{F}/anomalies", headers=sup).json()["total"] == 2
    assert client.get(f"{F}/anomalies", params={"status": "resolved"}, headers=sup).json()["total"] == 2 and client.get(f"{F}/anomalies", params={"status": "all"}, headers=sup).json()["total"] == 4
    assert client.post(f"{F}/anomalies/bulk-reopen", json={"txn_ids": [x1["id"]], "rules": ["capacity_exceed"]}, headers=hseq).json()["changed"] == 1
    # A person's decision is not undone by the next import.
    upload(client, admin, steady + odd)
    states = {a.rule: a.status for a in db.scalars(select(FuelAnomaly).where(FuelAnomaly.transaction_id == x1["id"])).all()}
    assert states == {"capacity_exceed": "open", "reading_regress": "resolved", "stat_spike": "resolved", "unusual_hour": "dismissed"}
    # Matching the stray card's fill to a vehicle clears "not matched", saying why.
    tipper = add_asset(client, admin, name="Tipper 02")
    moved = client.post(f"{F}/anomalies/bulk-attribute", json={"txn_ids": [inbox["items"][0]["id"], "nope"], "asset_id": tipper["id"], "reason": "Spare card used on the tipper"}, headers=hseq).json()
    assert moved == {"changed": 1, "unchanged": 0, "not_found": 1}
    x4 = client.get(f"{F}/transactions/{inbox['items'][0]['id']}", headers=sup).json()
    assert x4["attribution"] == "manual" and x4["reasons"][0]["status"] == "resolved" and x4["reasons"][0]["note"].startswith("Corrected")
    assert {a.action for a in db.scalars(select(AuditLog).where(AuditLog.target_type == "fuel_transaction")).all()} == {"fuel.anomaly_dismissed", "fuel.anomaly_resolved", "fuel.anomaly_open", "fuel.attributed"}
    assert db.scalars(select(OutboxMessage)).all() == []


# ---------- SmartFill ----------

SECRET = "smartfill-secret-ABC123"


@pytest.fixture
def smartfill(monkeypatch):
    state = {"pages": [], "ignore_paging": False, "error": False, "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        state["requests"].append(body)
        if body["method"] == "Tank:Level":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": {}, "id": 1})
        if state["error"]:
            return httpx.Response(200, json={"jsonrpc": "2.0", "error": {"code": -1, "message": "Not permitted"}, "id": 1})
        index = 0 if state["ignore_paging"] else body["parameters"]["range"]["offset"] // 100
        rows = state["pages"][index] if index < len(state["pages"]) else []
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"columns": ["Transaction ID", "Date", "Time", "Card Number", "Description", "Litres", "Fuel Type", "Odometer"], "rows": rows}})

    monkeypatch.setattr(integrations, "transport", httpx.MockTransport(handler))
    return state


def test_smartfill_sync_envelope_table_answers_paging_guard_and_call_limit(world, client, db, weeks, smartfill):
    admin, hseq = world.auth("admin1"), world.auth("hseq1")
    sync = lambda **body: client.post(f"{F}/sync-smartfill", json=body, headers=admin)  # noqa: E731
    assert sync().status_code == 409 and client.post(f"{F}/sync-smartfill", json={}, headers=hseq).status_code == 403
    client.put(f"{I}/smartfill", json={"settings": {"api_key": "CLIENT-REF"}, "secrets": {"api_secret": SECRET}, "mode": "live"}, headers=admin)
    assert "Test the SmartFill connection" in sync().json()["detail"]
    assert client.post(f"{I}/smartfill/test-connection", headers=admin).json()["ok"] is True
    day = weeks.last.strftime("%d/%m/%Y")
    page = [[f"SF-{i}", day, "07:00", "C1", "Ute 01", 40 + i % 3, "Diesel", 1000 + i] for i in range(100)]
    smartfill["pages"] = [page, [["SF-100", day, "08:00", "C1", "Ute 01", 44, "Diesel", 2000]]]
    preview = sync().json()
    assert preview["status"] == "previewed" and preview["counts"]["new"] == 101 and db.scalars(select(FuelTransaction)).all() == []
    sent = smartfill["requests"][-1]
    assert sent["method"] == "Transactions:Read" and "params" not in sent and sent["parameters"]["clientReference"] == "CLIENT-REF" and sent["parameters"]["clientSecret"] == SECRET
    assert sent["parameters"]["range"] == {"offset": 100, "length": 100} and "From Timestamp" in sent["parameters"]
    done = sync(dry_run=False).json()
    assert done["status"] == "committed" and done["via"] == "api" and done["counts"]["new"] == 101 and SECRET not in json.dumps(done)
    assert db.scalars(select(FuelTransaction).where(FuelTransaction.source_id == "SF-100")).one().source_system == "smartfill"
    status = client.get(f"{F}/smartfill-status", headers=world.auth("super1")).json()
    assert status["last_synced_at"] and status["schedule"].startswith("Manual only") and status["can_sync"] is False and status["calls_left"]["minute"] == 2

    # A service that ignores paging and keeps answering with the same page is stopped, not followed for ever.
    smartfill["ignore_paging"] = True
    stuck = sync(dry_run=False).json()
    assert stuck["counts"]["new"] == 0 and any("same page" in w for w in stuck["warnings"])
    assert len(db.scalars(select(ProviderCall)).all()) == 6
    # Six calls in the minute: the seventh is refused before it is made, and says why.
    before = len(smartfill["requests"])
    limited = sync(dry_run=False).json()
    assert limited["status"] == "failed" and "6 times a minute" in limited["error"] and len(smartfill["requests"]) == before
    db.query(ProviderCall).delete()
    db.commit()
    smartfill["ignore_paging"], smartfill["error"] = False, True
    refused = sync(dry_run=False).json()
    assert refused["status"] == "failed" and "refused the request" in refused["error"] and SECRET not in json.dumps(refused)
    assert len(db.scalars(select(FuelTransaction)).all()) == 101


def test_simulator_fuel_is_labelled_and_kept_out_of_real_totals(world, client, db, weeks, fuelcheck):
    admin, sup = world.auth("admin1"), world.auth("super1")
    upload(client, admin, [row("REAL-1", weeks.last, litres="70")])
    real = client.get(f"{F}/reports", headers=sup).json()
    assert real["simulated"] is False and real["totals"]["litres"] == "70.0"
    client.put(f"{I}/smartfill", json={"mode": "simulator"}, headers=admin)
    client.put(f"{I}/fuelcheck", json={"mode": "simulator"}, headers=admin)
    done = client.post(f"{F}/sync-smartfill", json={"dry_run": False, "days": 14}, headers=admin).json()
    assert done["via"] == "simulator" and done["via_label"] == "Simulator (not real)" and done["counts"]["new"] == 7 and done["counts"]["not_diesel"] == 1
    sims = db.scalars(select(FuelTransaction).where(FuelTransaction.source_system == "simulator")).all()
    assert len(sims) == 7 and all(t.source_id.startswith("SIM-") for t in sims)
    locked = client.post(f"{F}/weekly-diesel/refresh", headers=admin).json()
    assert locked["simulated"] is True and all(r["outcome"] == "locked" for r in locked["results"]) and fuelcheck["calls"] == 0  # no real request
    prices = db.scalars(select(FuelWeeklyPrice)).all()
    assert prices and all(p.simulated and p.source == "simulator" for p in prices)
    db.expire_all()
    assert db.scalars(select(FuelTransaction).where(FuelTransaction.source_id == "REAL-1")).one().report_cost is None  # a simulated rate never prices a real fill
    sim_report = client.get(f"{F}/reports", params={"date_from": (weeks.today - dt.timedelta(days=20)).isoformat()}, headers=sup).json()
    assert sim_report["simulated"] is True and sim_report["totals"]["fills"] == 7
    export = client.get(f"{F}/reports/export", params={"date_from": (weeks.today - dt.timedelta(days=20)).isoformat()}, headers=sup)
    assert export.status_code == 200 and "SIMULATOR DATA" in export.text
    # Back to real: the simulator's fills and rates are out of the totals, and the real week can still get its own rate.
    client.put(f"{I}/smartfill", json={"mode": "off"}, headers=admin)
    back = client.get(f"{F}/reports", headers=sup).json()
    assert back["simulated"] is False and back["totals"]["fills"] == 1
    assert enter_week(client, admin, weeks.last).status_code == 200
    assert client.get(f"{F}/weekly-diesel", headers=sup).json()["ledger"][0]["source"] == "manual"


# ---------- reports ----------

def test_reports_group_export_and_email_through_the_outbox(world, client, db, weeks):
    admin, hseq, sup = world.auth("admin1"), world.auth("hseq1"), world.auth("super1")
    add_asset(client, admin, name="=HYPERLINK(1)", registration="ABC123")
    day = weeks.older
    upload(client, admin, [row("T1", day, rego="ABC123", litres="40", total="88.00"), row("T2", day + dt.timedelta(days=1), rego="ABC123", litres="60"),
                           row("T3", day + dt.timedelta(days=1), card="C5", litres="25"), row("T4", day + dt.timedelta(days=8), card="C5", litres="33")])
    enter_week(client, admin, day)
    params = {"date_from": day.isoformat(), "date_to": (day + dt.timedelta(days=13)).isoformat()}
    default = client.get(f"{F}/reports", params=params, headers=sup).json()
    assert default["group"] == "vehicle" and default["can_email"] is False  # per-vehicle is the default view
    assert [(r["label"], r["fills"], r["litres"], r["cost"], r["complete"]) for r in default["items"]] == [
        ("=HYPERLINK(1) (ABC123)", 2, "100.0", "203.00", True), ("Not matched to a vehicle", 2, "58.0", "50.75", False)]
    t = default["totals"]
    assert (t["fills"], t["litres"], t["cost"], t["complete"], t["unpriced_litres"], t["average_rate"], t["supplier_total"], t["supplier_fills"]) == (4, "158.0", "253.75", False, "33.0", "2.0300", "88.00", 1)
    assert "reporting figure, not what the supplier invoiced" in default["cost_meaning"]
    by_day = client.get(f"{F}/reports", params={**params, "group": "day"}, headers=sup).json()
    assert [(r["key"], r["litres"]) for r in by_day["items"]] == [((day + dt.timedelta(days=8)).isoformat(), "33.0"), ((day + dt.timedelta(days=1)).isoformat(), "85.0"), (day.isoformat(), "40.0")]
    assert [r["label"] for r in client.get(f"{F}/reports", params={**params, "group": "card"}, headers=sup).json()["items"]] == ["C1", "C5"]
    assert client.get(f"{F}/reports", params={"date_from": "2026-05-01", "date_to": "2026-04-01"}, headers=sup).status_code == 422
    assert client.get(f"{F}/reports", params={"group": "moon"}, headers=sup).status_code == 422
    assert client.get(f"{F}/reports", headers=world.auth("worker1")).status_code == 403
    assert client.get(f"{F}/reports", params=params, headers=world.auth("admin2")).json()["totals"]["fills"] == 0
    export = client.get(f"{F}/reports/export", params=params, headers=sup)
    assert export.headers["content-type"].startswith("text/csv") and "'=HYPERLINK(1) (ABC123),2,100.0,203.00" in export.text and "Total (cost not complete),4,158.0,253.75" in export.text

    assert client.post(f"{F}/reports/email", json={"to": ["boss@one.example"], **params}, headers=sup).status_code == 403
    assert client.post(f"{F}/reports/email", json={"to": ["not-an-email"], **params}, headers=hseq).status_code == 422
    assert client.post(f"{F}/reports/email", json={"to": [], **params}, headers=hseq).status_code == 422
    sent = client.post(f"{F}/reports/email", json={"to": ["Boss@One.Example", "boss@one.example"], "note": "For Monday's meeting", **params}, headers=hseq).json()
    message = db.scalars(select(OutboxMessage)).one()  # one per person, and held: no email connection is set up
    assert len(sent["messages"]) == 1 and message.recipient == "boss@one.example" and message.purpose == "fuel_report" and message.status == "held"
    assert "Total: 4 fills, 158.0 L diesel, $253.75 (not complete)" in message.body_text and "For Monday's meeting" in message.body_text and "Sent by Sam Keating" in message.body_text
    assert db.scalars(select(AuditLog).where(AuditLog.action == "fuel.report_emailed")).one().detail["recipients"] == 1

    # Weekly pricing can be given a start date; fills before it get no reporting cost and say so.
    assert client.put(f"{F}/price-settings", json={"weekly_start": (day + dt.timedelta(days=1)).isoformat()}, headers=admin).status_code == 422  # not a Monday
    assert client.put(f"{F}/price-settings", json={"weekly_start": (day + dt.timedelta(days=14)).isoformat()}, headers=hseq).status_code == 403
    assert client.put(f"{F}/price-settings", json={"weekly_start": (day + dt.timedelta(days=14)).isoformat()}, headers=admin).status_code == 200
    after = client.get(f"{F}/reports", params=params, headers=sup).json()
    assert after["totals"]["cost"] == "253.75" and after["totals"]["complete"] is True and "before weekly pricing began" in after["notes"][0]  # costs already written stay
