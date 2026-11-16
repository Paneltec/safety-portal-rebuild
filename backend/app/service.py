"""Service schedules: working out what is due (stage 6b).

A schedule can run on days, kilometres and running hours at once; it is due on whichever comes
first. Each part is worked out separately and never mixed (kilometres are not hours):

  ok        not yet inside the warning margin
  due_soon  inside the warning margin
  overdue   at or past the due point
  unknown   cannot be worked out: there is no starting point, or the asset has no reading for
            that meter. Unknown is shown as unknown, never as "ok".

The starting point is the most recent service record linked to the schedule, or the baseline
entered on the schedule when no linked record exists. Nothing is stored about "next due", so a
corrected or removed record can never leave a stale due date behind.
"""
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Asset, AssetSchedule, AssetServiceRecord

RANK = {"ok": 0, "unknown": 1, "due_soon": 2, "overdue": 3}
LABELS = {"ok": "Up to date", "unknown": "Cannot tell", "due_soon": "Due soon", "overdue": "Overdue"}
PRESETS = [
    {"key": "plant_a", "name": "250 hour service (A)", "interval_hours": 250, "note": "Typical minor service for plant"},
    {"key": "plant_b", "name": "500 hour service (B)", "interval_hours": 500, "note": "Typical intermediate service for plant"},
    {"key": "plant_c", "name": "1,000 hour service (C)", "interval_hours": 1000, "note": "Typical major service for plant"},
    {"key": "plant_d", "name": "2,000 hour service (D)", "interval_hours": 2000, "note": "Typical full service for plant"},
    {"key": "vehicle", "name": "Vehicle service", "interval_km": 10000, "interval_days": 182, "note": "10,000 km or 6 months, whichever comes first"},
    {"key": "annual", "name": "Annual inspection", "interval_days": 365, "note": "Once a year"},
]


def last_done(db: Session, schedules: list[AssetSchedule]) -> dict[str, AssetServiceRecord]:
    """The latest linked record for each schedule."""
    ids = [s.id for s in schedules]
    out: dict[str, AssetServiceRecord] = {}
    if not ids:
        return out
    rows = db.scalars(select(AssetServiceRecord).where(AssetServiceRecord.schedule_id.in_(ids), AssetServiceRecord.deleted_at.is_(None))
                      .order_by(AssetServiceRecord.performed_on, AssetServiceRecord.created_at)).all()
    for row in rows:
        out[row.schedule_id] = row
    return out


def evaluate(schedule: AssetSchedule, asset: Asset, last: AssetServiceRecord | None, today: date) -> dict[str, Any]:
    start_date = last.performed_on if last else schedule.baseline_date
    start_km = (last.odometer_km if last and last.odometer_km is not None else None) if last else schedule.baseline_km
    start_hours = (last.engine_hours if last and last.engine_hours is not None else None) if last else schedule.baseline_hours
    parts: list[dict[str, Any]] = []
    if schedule.interval_days:
        if start_date is None:
            parts.append({"basis": "days", "status": "unknown", "text": "No date for the last service"})
        else:
            due = start_date + timedelta(days=schedule.interval_days)
            left = (due - today).days
            status = "overdue" if left < 0 else "due_soon" if left <= schedule.warn_days else "ok"
            text = f"{-left} day{'s' if left != -1 else ''} overdue" if left < 0 else "Due today" if left == 0 else f"Due in {left} day{'s' if left != 1 else ''}"
            parts.append({"basis": "days", "status": status, "due_date": due.isoformat(), "remaining": left, "text": text})
    for basis, interval, start, current, warn, unit in (("km", schedule.interval_km, start_km, asset.odometer_km, schedule.warn_km, "km"),
                                                        ("hours", schedule.interval_hours, start_hours, asset.engine_hours, schedule.warn_hours, "hours")):
        if not interval:
            continue
        meter = "odometer" if basis == "km" else "hour meter"
        if start is None:
            parts.append({"basis": basis, "status": "unknown", "text": f"No {meter} reading for the last service"})
        elif current is None:
            parts.append({"basis": basis, "status": "unknown", "due_at": start + interval, "text": f"Due at {start + interval:,.0f} {unit}, but the {meter} has not been recorded"})
        else:
            due_at = start + interval
            left = due_at - current
            status = "overdue" if left <= 0 else "due_soon" if left <= warn else "ok"
            text = f"{-left:,.0f} {unit} overdue" if left < 0 else f"Due now ({due_at:,.0f} {unit})" if left == 0 else f"Due in {left:,.0f} {unit} (at {due_at:,.0f})"
            parts.append({"basis": basis, "status": status, "due_at": due_at, "remaining": left, "text": text})
    status = max((p["status"] for p in parts), key=RANK.__getitem__) if parts else "unknown"
    worst = [p for p in parts if p["status"] == status]
    return {"status": status, "status_label": LABELS[status], "parts": parts, "summary": "; ".join(p["text"] for p in worst) or "No interval set",
            "last_done": {"date": start_date.isoformat() if start_date else None, "odometer_km": start_km, "engine_hours": start_hours, "from_record": last is not None}}


def marker(result: dict[str, Any]) -> str:
    """Identifies this particular due point, so one reminder is prepared for it and no more."""
    bits = [str(p.get("due_date") or p.get("due_at")) for p in result["parts"] if p["status"] == result["status"]]
    return "|".join(bits) or "none"


def rollup(db: Session, org_id: str, assets: list[Asset], today: date) -> dict[str, dict[str, Any]]:
    """Worst service status per asset, for the register list."""
    by_id = {a.id: a for a in assets}
    if not by_id:
        return {}
    schedules = db.scalars(select(AssetSchedule).where(AssetSchedule.org_id == org_id, AssetSchedule.asset_id.in_(by_id), AssetSchedule.deleted_at.is_(None),
                                                       AssetSchedule.active.is_(True))).all()
    last = last_done(db, list(schedules))
    out: dict[str, dict[str, Any]] = {}
    for s in schedules:
        result = evaluate(s, by_id[s.asset_id], last.get(s.id), today)
        current = out.get(s.asset_id)
        if current is None or RANK[result["status"]] > RANK[current["status"]]:
            out[s.asset_id] = {"status": result["status"], "status_label": result["status_label"], "summary": f"{s.name}: {result['summary']}"}
    return out
