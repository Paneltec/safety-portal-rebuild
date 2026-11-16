"""The pay workbench: settings, worksheets and freezing a run (stage 7c).

This is a TEST workbench. It works pay out and keeps a record; it does not pay anyone, lodge
anything with the Tax Office or a super fund, or send anything to a worker. Those actions do not
exist in this code: the routes that would do them answer with a refusal.

  * A worksheet's hours come from approved timesheet days and approved leave for the week. They
    are copied in by an explicit import; if the source changes afterwards the worksheet is marked
    stale and cannot be finalised until it is imported again.
  * Finalising needs every row to be free of review issues. It freezes what was used and what was
    produced, with a hash; locks the timesheet days; and marks the leave as used.
  * Reopening needs a reason, is recorded, and releases the days and leave again. A run with
    payment evidence recorded cannot be reopened.
"""
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import payroll_engine as engine
from .models import LeaveRequest, PayProfile, PayRun, PaySettings, TimesheetDay, Worker
from .people import worker_name

HOUR_BUCKETS = ("ordinary", "ot1", "ot2", "night", "holiday_work", "saturday", "sunday", "penalty_ordinary")
MANUAL_BUCKETS = ("ordinary", "ot1", "ot2", "saturday", "sunday", "annual", "personal", "public_holiday", "night", "holiday_work", "penalty_ordinary")
ADJUST_KEYS = ("lafha", "lafha_taxable", "lafha_superable", "taxable_allowances", "reimbursements", "deductions", "extra_withholding", "manual_payg", "payg_reference",
               "qualifying_earnings_override", "allowances", "earnings")
PROFILE_KEYS = ("hourly_rate", "annual_salary", "employment_type", "pay_basis", "ordinary_weekly_hours", "conditions_reviewed", "tax_mode", "tax_declaration_reviewed", "annual_weeks",
                "personal_weeks", "leave_loading_percent", "casual_rates", "opening_annual", "opening_personal")
TEST_ONLY = "This is a test workbench. The portal does not pay anyone, lodge anything or send payslips."


def get_settings(db: Session, org_id: str) -> PaySettings | None:
    return db.get(PaySettings, org_id)


def rules_for(db: Session, org_id: str) -> dict[str, Any]:
    row = get_settings(db, org_id)
    return {**engine.DEFAULT_RULES, **((row.rules if row else None) or {})}


def collect_sources(db: Session, org_id: str, run: PayRun) -> dict[str, dict[str, Any]]:
    """What approved timesheets and approved leave say for the run's week, per worker.
    Days and leave already used by another run are left out."""
    start, end = run.week_start, run.week_start + timedelta(days=6)
    out: dict[str, dict[str, Any]] = {}

    def row(worker_id: str) -> dict[str, Any]:
        return out.setdefault(worker_id, {"hours": {b: Decimal("0") for b in HOUR_BUCKETS} | {"annual": Decimal("0"), "personal": Decimal("0")}, "meal_count": 0,
                                          "timesheets": [], "leave": [], "unpaid_leave": []})
    days = db.scalars(select(TimesheetDay).where(TimesheetDay.org_id == org_id, TimesheetDay.work_date >= start, TimesheetDay.work_date <= end,
                                                 TimesheetDay.status.in_(("approved", "locked"))).order_by(TimesheetDay.work_date)).all()
    for day in days:
        if day.pay_run_id not in (None, run.id) or (day.status == "locked" and day.pay_run_id != run.id):
            continue
        r = row(day.worker_id)
        for bucket in HOUR_BUCKETS:
            r["hours"][bucket] += Decimal(str((day.hours or {}).get(bucket, 0)))
        r["meal_count"] += int((day.hours or {}).get("meal_count", 0))
        r["timesheets"].append([day.id, day.revision])
    leave = db.scalars(select(LeaveRequest).where(LeaveRequest.org_id == org_id, LeaveRequest.status == "approved", LeaveRequest.start_date <= end, LeaveRequest.end_date >= start)).all()
    for req in leave:
        if req.pay_run_id not in (None, run.id):
            continue
        hours = sum((Decimal(str(d["hours"])) for d in req.days or [] if start.isoformat() <= d["date"] <= end.isoformat()), Decimal("0"))
        if hours <= 0:
            continue
        r = row(req.worker_id)
        if req.leave_type in ("annual", "personal"):
            r["hours"][req.leave_type] += hours
            r["leave"].append([req.id, str(hours)])
        else:
            r["unpaid_leave"].append([req.id, req.leave_type, str(hours)])
    for r in out.values():
        r["hours"] = {k: float(v) for k, v in r["hours"].items()}
        r["fingerprint"] = hashlib.sha256(json.dumps([sorted(r["timesheets"]), sorted(r["leave"]), sorted(r["unpaid_leave"])]).encode()).hexdigest()[:16]
    return out


def stale_workers(db: Session, org_id: str, run: PayRun) -> list[str]:
    """Workers whose approved hours or leave differ from what the worksheet holds."""
    if run.status != "draft" or run.sequence != 0:
        return []
    current = collect_sources(db, org_id, run)
    held = {w: (r.get("sources") or {}).get("fingerprint") for w, r in (run.rows or {}).items()}
    changed = [w for w, src in current.items() if held.get(w) != src["fingerprint"]]
    changed += [w for w, fp in held.items() if fp and w not in current]
    return sorted(set(changed))


def opening_balances(db: Session, org_id: str, worker_id: str, run: PayRun, profile: dict[str, Any]) -> tuple[float, float]:
    """Leave hours at the start of this run: the closing figures of the latest earlier finalised run, else the profile's opening figures."""
    earlier = db.scalars(select(PayRun).where(PayRun.org_id == org_id, PayRun.status == "finalised", PayRun.id != run.id,
                                              (PayRun.week_start < run.week_start) | ((PayRun.week_start == run.week_start) & (PayRun.sequence < run.sequence)))
                         .order_by(PayRun.week_start.desc(), PayRun.sequence.desc())).all()
    for prior in earlier:
        for row in (prior.frozen or {}).get("rows", []):
            if row["worker_id"] == worker_id:
                return row["result"]["annual_closing"], row["result"]["personal_closing"]
    return float(profile.get("opening_annual") or 0), float(profile.get("opening_personal") or 0)


def entry_for(row: dict[str, Any], opening: tuple[float, float]) -> dict[str, Any]:
    hours = dict(row.get("hours") or {})
    for bucket, extra in (row.get("manual") or {}).items():
        hours[bucket] = float(Decimal(str(hours.get(bucket, 0))) + Decimal(str(extra or 0)))
    entry = {**hours, "meal_count": row.get("meal_count", 0), "hours_reviewed": bool(row.get("hours_reviewed")), "super_reviewed": bool(row.get("super_reviewed")),
             "opening_annual": opening[0], "opening_personal": opening[1]}
    entry.update({k: v for k, v in (row.get("adjust") or {}).items() if k in ADJUST_KEYS})
    return entry


def compute(db: Session, org_id: str, run: PayRun) -> dict[str, Any]:
    """The worksheet worked out with the current rules and profiles. Changes nothing."""
    rules = rules_for(db, org_id)
    worker_ids = list((run.rows or {}).keys())
    workers = {w.id: w for w in db.scalars(select(Worker).where(Worker.id.in_(worker_ids))).all()} if worker_ids else {}
    profiles = {p.worker_id: p for p in db.scalars(select(PayProfile).where(PayProfile.worker_id.in_(worker_ids))).all()} if worker_ids else {}
    rows = []
    totals = {"gross": Decimal("0"), "payg": Decimal("0"), "net": Decimal("0"), "super": Decimal("0")}
    unknown = {"payg": 0, "net": 0, "super": 0}
    for worker_id in sorted(worker_ids, key=lambda w: worker_name(workers[w]).lower() if w in workers else w):
        row = run.rows[worker_id]
        profile = {k: v for k, v in ((profiles[worker_id].data if worker_id in profiles else {}) or {}).items() if k in PROFILE_KEYS}
        entry = entry_for(row, opening_balances(db, org_id, worker_id, run, profile))
        item: dict[str, Any] = {"worker_id": worker_id, "name": worker_name(workers[worker_id]) if worker_id in workers else "Unknown worker", "has_profile": worker_id in profiles,
                                "profile": profile, "entry": entry, "sources": row.get("sources") or {}, "manual": row.get("manual") or {}, "adjust": row.get("adjust") or {},
                                "hours_reviewed": bool(row.get("hours_reviewed")), "super_reviewed": bool(row.get("super_reviewed")), "result": None, "error": None}
        if run.payday is None:
            item["error"] = "Set the payday first"
        else:
            try:
                item["result"] = engine.calculate_row(profile, entry, rules, run.payday)
            except engine.EngineError as exc:
                item["error"] = str(exc)
        if item["result"]:
            totals["gross"] += Decimal(str(item["result"]["gross"]))
            for key in ("payg", "net", "super"):
                if item["result"][key] is None:
                    unknown[key] += 1
                else:
                    totals[key] += Decimal(str(item["result"][key]))
        rows.append(item)
    ready = bool(rows) and all(r["result"] is not None and r["result"]["review_ready"] for r in rows)
    return {"rows": rows, "rules": rules, "ready": ready,
            "totals": {"gross": f"{totals['gross']:.2f}", **{k: (f"{totals[k]:.2f}" if unknown[k] == 0 else None) for k in ("payg", "net", "super")},
                       "rows": len(rows), "rows_with_issues": sum(1 for r in rows if r["result"] is None or not r["result"]["review_ready"])}}


def freeze(run: PayRun, computed: dict[str, Any], settings: PaySettings) -> tuple[dict[str, Any], str]:
    frozen = {"rule_version": engine.RULE_VERSION, "tax_source": engine.TAX_SOURCE, "settings_revision": settings.revision, "rules": computed["rules"],
              "employer": {"name": settings.employer_name, "abn": settings.employer_abn}, "week_start": run.week_start.isoformat(), "sequence": run.sequence,
              "payday": run.payday.isoformat() if run.payday else None, "totals": computed["totals"],
              "rows": [{"worker_id": r["worker_id"], "name": r["name"], "profile": r["profile"], "entry": r["entry"], "sources": r["sources"], "result": r["result"]} for r in computed["rows"]]}
    return frozen, hashlib.sha256(json.dumps(frozen, sort_keys=True, default=str).encode()).hexdigest()


def lock_sources(db: Session, run: PayRun, *, lock: bool) -> None:
    """Tie the timesheet days and leave a run used to that run (finalise), or release them (reopen)."""
    for row in (run.rows or {}).values():
        sources = row.get("sources") or {}
        for day_id, _ in sources.get("timesheets") or []:
            day = db.get(TimesheetDay, day_id)
            if day is not None:
                day.status, day.pay_run_id = ("locked", run.id) if lock else ("approved", None)
        for leave_id, _ in sources.get("leave") or []:
            leave = db.get(LeaveRequest, leave_id)
            if leave is not None:
                leave.pay_run_id = run.id if lock else None


def default_payday(week_start: date) -> date:
    """A starting suggestion only: the Thursday after the week ends."""
    end = week_start + timedelta(days=6)
    return end + timedelta(days=(3 - end.weekday()) % 7 or 7)
