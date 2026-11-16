"""Payroll access, and timesheets: a worker's own days and the office review (stage 7b).

Payroll is opened only by the payroll owner or by a named grant the owner gives to an
administrator. A role or a permission override never opens it, and an administrator without a
grant is refused by every office route here.

A worker writes their own days as drafts and submits them. The office approves or sends them
back with a reason. Nobody approves their own day. An approved day that a finalised pay run has
used is locked. Nothing here pays anyone or sends anything.
"""
import datetime as dt
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import payroll_engine as engine
from ..db import get_db
from ..deps import Caller, get_caller, require_permission
from ..models import Organisation, PayrollGrant, Site, SiteAttendance, TimesheetDay, User, Worker, utcnow
from ..people import today_for, worker_for_user, worker_name
from ..services import audit, is_payroll_owner, iso, payroll_level

access_router = APIRouter(tags=["payroll"])
me_router = APIRouter(prefix="/api/me/payroll", tags=["timesheets"])
router = APIRouter(prefix="/api/payroll", tags=["timesheets"])

STATUSES = {"draft": "Not sent yet", "submitted": "Sent, waiting for the office", "approved": "Approved", "rejected": "Sent back", "locked": "Paid: locked"}
KINDS = {"work": "Worked", "rdo": "Rostered day off", "no_work": "Did not work"}
CATEGORIES = {"site": "On site", "yard": "Yard / Workshop", "travel": "Travel", "training": "Training", "office": "Office"}
WORKER_BACKDATE_DAYS = 35


# ---------- payroll access ----------

class AccessIn(BaseModel):
    level: Literal["none", "view", "edit"]


def _owner(caller: Caller, db: Session) -> None:
    if not is_payroll_owner(db, caller.user):
        raise HTTPException(403, "Only the payroll owner can manage payroll access")


@access_router.put("/api/users/{user_id}/payroll-access")
def set_payroll_access(user_id: str, body: AccessIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The payroll owner gives a named administrator view or edit access to payroll, or takes it away."""
    _owner(caller, db)
    user = db.get(User, user_id)
    if user is None or user.org_id != caller.org_id or user.deleted_at is not None:
        raise HTTPException(404, "User not found")
    if is_payroll_owner(db, user):
        raise HTTPException(400, "The payroll owner retains access")
    if body.level != "none" and user.role_id != "admin":
        raise HTTPException(400, "Payroll access is restricted to administrators")
    grant = db.get(PayrollGrant, user.id)
    before = grant.level if grant else "none"
    if body.level == "none":
        if grant is not None:
            db.delete(grant)
    elif grant is None:
        db.add(PayrollGrant(user_id=user.id, org_id=caller.org_id, level=body.level, granted_by=caller.user.id))
    else:
        grant.level, grant.granted_by, grant.granted_at = body.level, caller.user.id, utcnow()
    if before != body.level:
        # Permissions are worked out afresh on every request, so a change takes effect at once.
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="payroll.access_changed", target_type="user", target_id=user.id, detail={"before": before, "after": body.level})
    db.commit()
    return {"user_id": user.id, "level": body.level}


@access_router.get("/api/payroll/workbench/access/list")
def payroll_access_list(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    _owner(caller, db)
    admins = db.scalars(select(User).where(User.org_id == caller.org_id, User.role_id == "admin", User.deleted_at.is_(None)).order_by(User.name)).all()
    return {"users": [{"id": u.id, "name": u.name, "email": u.email, "status": u.status, "is_owner": is_payroll_owner(db, u),
                       "level": "owner" if is_payroll_owner(db, u) else (payroll_level(db, u) or "none")} for u in admins],
            "note": "Only administrators can be given payroll access. A role or a permission override never opens payroll."}


# ---------- shared ----------

class ShiftIn(BaseModel):
    start: str = Field(max_length=5)
    finish: str = Field(max_length=5)
    next_day: bool = False
    break_minutes: int = Field(default=0, ge=0, le=720)
    break_start: str | None = Field(default=None, max_length=5)
    site_id: str | None = None
    category: Literal["site", "yard", "travel", "training", "office"] = "site"
    note: str | None = Field(default=None, max_length=300)


class OfficeShiftIn(ShiftIn):
    public_holiday: bool = False
    replacement_day_shift: bool = False


class DayIn(BaseModel):
    kind: Literal["work", "rdo", "no_work"] = "work"
    shifts: list[ShiftIn] = Field(default_factory=list, max_length=10)
    notes: str | None = Field(default=None, max_length=1000)
    expected_revision: int | None = None


class OfficeDayIn(BaseModel):
    kind: Literal["work", "rdo", "no_work"] = "work"
    shifts: list[OfficeShiftIn] = Field(default_factory=list, max_length=10)
    notes: str | None = Field(default=None, max_length=1000)
    expected_revision: int | None = None


class OfficeCreateIn(OfficeDayIn):
    worker_id: str
    work_date: dt.date


class IdsIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    reason: str | None = Field(default=None, max_length=600)


class WeekIn(BaseModel):
    week_start: dt.date | None = None


def _org(db: Session, org_id: str) -> Organisation:
    return db.get(Organisation, org_id)


def week_start_day(org: Organisation) -> int:
    day = ((org.details or {}).get("payroll") or {}).get("week_start_day", 0)
    return day if isinstance(day, int) and 0 <= day <= 6 else 0


def current_week(org: Organisation, given: dt.date | None) -> dt.date:
    """The first day of the seven-day pay week: the one given, or the most recent configured start day."""
    if given is not None:
        return given
    today = today_for(org)
    return today - dt.timedelta(days=(today.weekday() - week_start_day(org)) % 7)


def apply_day(db: Session, org_id: str, row: TimesheetDay, kind: str, shifts: list[Any], notes: str | None) -> None:
    """Validate the shifts through the calculation rules and store them with the hours they produce."""
    stored = []
    for s in shifts:
        item = s.model_dump()
        if item.get("site_id"):
            site = db.get(Site, item["site_id"])
            if site is None or site.org_id != org_id or site.deleted_at is not None:
                raise HTTPException(422, "Site not found")
        item["note"] = (item.get("note") or "").strip() or None
        item["break_start"] = (item.get("break_start") or "").strip() or None
        stored.append(item)
    if kind != "work" and stored:
        raise HTTPException(422, "A day off cannot have shifts. Remove the shifts or mark the day as worked.")
    try:
        hours = engine.allocate_shifts([{**s, "date": row.work_date} for s in stored]) if stored else {}
    except engine.EngineError as exc:
        raise HTTPException(422, str(exc)) from None
    row.kind, row.shifts, row.hours, row.notes = kind, stored, hours, (notes or "").strip() or None
    row.paid_hours = engine.paid_hours(hours) if hours else Decimal("0")
    row.rule_version = engine.RULE_VERSION
    row.revision = (row.revision or 0) + 1


def day_out(row: TimesheetDay, workers: dict[str, str], sites: dict[str, str], users: dict[str, str]) -> dict[str, Any]:
    return {"id": row.id, "worker_id": row.worker_id, "worker": workers.get(row.worker_id, ""), "work_date": row.work_date.isoformat(), "kind": row.kind,
            "kind_label": KINDS.get(row.kind, row.kind), "shifts": [{**s, "site": sites.get(s.get("site_id") or ""), "category_label": CATEGORIES.get(s.get("category", "site"))} for s in row.shifts or []],
            "hours": row.hours or {}, "paid_hours": f"{row.paid_hours:.2f}", "notes": row.notes, "status": row.status, "status_label": STATUSES.get(row.status, row.status),
            "source": row.source, "revision": row.revision, "reject_reason": row.reject_reason, "reviewed_by": users.get(row.reviewed_by or ""), "reviewed_at": iso(row.reviewed_at),
            "submitted_at": iso(row.submitted_at), "locked": row.status == "locked"}


def _lookups(db: Session, rows: list[TimesheetDay]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    worker_ids = {r.worker_id for r in rows}
    site_ids = {s.get("site_id") for r in rows for s in (r.shifts or []) if s.get("site_id")}
    user_ids = {r.reviewed_by for r in rows if r.reviewed_by}
    workers = {w.id: worker_name(w) for w in db.scalars(select(Worker).where(Worker.id.in_(worker_ids))).all()} if worker_ids else {}
    sites = {s.id: s.name for s in db.scalars(select(Site).where(Site.id.in_(site_ids))).all()} if site_ids else {}
    users = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
    return workers, sites, users


def _check_revision(row: TimesheetDay, expected: int | None) -> None:
    if expected is not None and row.revision != expected:
        raise HTTPException(409, "This day changed in another window. Reload before saving.")


# ---------- my timesheet ----------

def _me(db: Session, caller: Caller) -> Worker:
    worker = worker_for_user(db, caller.user)
    if worker is None:
        raise HTTPException(409, "Your login is not linked to a worker record, so there is no timesheet here. Ask the office to link it.")
    return worker


@me_router.get("/time-catalog")
def my_catalog(caller: Caller = Depends(get_caller)) -> dict[str, Any]:
    return {"categories": CATEGORIES, "kinds": KINDS}


@me_router.get("/timesheets")
def my_timesheets(week_start: dt.date | None = None, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    org = _org(db, caller.org_id)
    start = current_week(org, week_start)
    today = today_for(org)
    worker = worker_for_user(db, caller.user)
    days = [start + dt.timedelta(days=i) for i in range(7)]
    if worker is None:
        return {"linked": False, "week_start": start.isoformat(), "days": [], "today": today.isoformat()}
    rows = {r.work_date: r for r in db.scalars(select(TimesheetDay).where(TimesheetDay.worker_id == worker.id, TimesheetDay.work_date >= days[0], TimesheetDay.work_date <= days[6])).all()}
    workers, sites, users = _lookups(db, list(rows.values()))
    out = []
    for day in days:
        row = rows.get(day)
        editable = day <= today and day >= today - dt.timedelta(days=WORKER_BACKDATE_DAYS) and (row is None or row.status in ("draft", "rejected"))
        out.append({"date": day.isoformat(), "entry": day_out(row, workers, sites, users) if row else None, "editable": editable,
                    "why_not": None if editable else "This day is in the future." if day > today else "This day is too long ago to change here. Ask the office." if row is None or row.status in ("draft", "rejected")
                    else "This day has been sent. Ask the office to send it back if it needs changing."})
    total = sum((r.paid_hours for r in rows.values()), Decimal("0"))
    to_send = sum(1 for r in rows.values() if r.status in ("draft", "rejected") and (r.paid_hours > 0 or r.kind != "work"))
    return {"linked": True, "week_start": start.isoformat(), "week_end": days[6].isoformat(), "today": today.isoformat(), "days": out, "total_paid_hours": f"{total:.2f}",
            "ready_to_send": to_send, "categories": CATEGORIES}


@me_router.put("/timesheets/{day}")
def save_my_day(day: dt.date, body: DayIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """A worker writes their own day as a draft. A day that has been sent, approved or paid cannot be changed here."""
    worker = _me(db, caller)
    today = today_for(_org(db, caller.org_id))
    if day > today:
        raise HTTPException(422, "A day that has not happened yet cannot be filled in")
    if day < today - dt.timedelta(days=WORKER_BACKDATE_DAYS):
        raise HTTPException(422, "That day is too long ago to change here. Ask the office.")
    row = db.scalars(select(TimesheetDay).where(TimesheetDay.worker_id == worker.id, TimesheetDay.work_date == day)).first()
    if row is not None and row.status not in ("draft", "rejected"):
        raise HTTPException(409, "This day has been sent. Ask the office to send it back if it needs changing.")
    if row is None:
        row = TimesheetDay(org_id=caller.org_id, worker_id=worker.id, work_date=day, source="self", created_by=caller.user.id, revision=0, status="draft")
        db.add(row)
    else:
        _check_revision(row, body.expected_revision)
    apply_day(db, caller.org_id, row, body.kind, body.shifts, body.notes)
    db.commit()
    return day_out(row, *_lookups(db, [row]))


@me_router.post("/submit")
def submit_my_week(body: WeekIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Send every filled-in day of the week that has not been sent (or was sent back) to the office."""
    worker = _me(db, caller)
    start = current_week(_org(db, caller.org_id), body.week_start)
    rows = db.scalars(select(TimesheetDay).where(TimesheetDay.worker_id == worker.id, TimesheetDay.work_date >= start, TimesheetDay.work_date <= start + dt.timedelta(days=6),
                                                 TimesheetDay.status.in_(("draft", "rejected")))).all()
    sent = empty = 0
    for row in rows:
        if row.kind == "work" and row.paid_hours <= 0:
            empty += 1
            continue
        row.status, row.submitted_at, row.reject_reason, row.revision = "submitted", utcnow(), None, row.revision + 1
        sent += 1
    if sent:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="timesheet.submitted", target_type="worker", target_id=worker.id,
              detail={"week_start": start.isoformat(), "days": sent})
    db.commit()
    return {"submitted": sent, "skipped_empty": empty}


# ---------- the office (payroll access only) ----------

def _get_day(db: Session, org_id: str, entry_id: str) -> TimesheetDay:
    row = db.get(TimesheetDay, entry_id)
    if row is None or row.org_id != org_id:
        raise HTTPException(404, "Timesheet day not found")
    return row


@router.get("/time-catalog")
def catalog(caller: Caller = Depends(require_permission("payroll", "view"))) -> dict[str, Any]:
    return {"categories": CATEGORIES, "kinds": KINDS, "statuses": STATUSES}


@router.get("/timesheets")
def list_timesheets(week_start: dt.date | None = None, status: str | None = None, worker_id: str | None = None,
                    caller: Caller = Depends(require_permission("payroll", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Every worker's days for one seven-day week, with each worker's totals."""
    org = _org(db, caller.org_id)
    start = current_week(org, week_start)
    query = select(TimesheetDay).where(TimesheetDay.org_id == caller.org_id, TimesheetDay.work_date >= start, TimesheetDay.work_date <= start + dt.timedelta(days=6))
    if status:
        query = query.where(TimesheetDay.status == status)
    if worker_id:
        query = query.where(TimesheetDay.worker_id == worker_id)
    rows = db.scalars(query.order_by(TimesheetDay.work_date)).all()
    workers, sites, users = _lookups(db, list(rows))
    own = worker_for_user(db, caller.user)
    totals: dict[str, dict[str, Any]] = {}
    for r in rows:
        t = totals.setdefault(r.worker_id, {"worker_id": r.worker_id, "worker": workers.get(r.worker_id, ""), "paid_hours": Decimal("0"), "days": 0, "by_status": {}, "is_own": own is not None and own.id == r.worker_id})
        t["paid_hours"] += r.paid_hours
        t["days"] += 1
        t["by_status"][r.status] = t["by_status"].get(r.status, 0) + 1
    return {"week_start": start.isoformat(), "week_end": (start + dt.timedelta(days=6)).isoformat(), "items": [day_out(r, workers, sites, users) for r in rows],
            "workers": sorted(({**t, "paid_hours": f"{t['paid_hours']:.2f}"} for t in totals.values()), key=lambda t: t["worker"].lower()),
            "can_edit": caller.can("payroll", "edit"), "statuses": STATUSES, "rule_version": engine.RULE_VERSION}


@router.get("/summary")
def summary(week_start: dt.date | None = None, caller: Caller = Depends(require_permission("payroll", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    start = current_week(_org(db, caller.org_id), week_start)
    rows = db.scalars(select(TimesheetDay).where(TimesheetDay.org_id == caller.org_id, TimesheetDay.work_date >= start, TimesheetDay.work_date <= start + dt.timedelta(days=6))).all()
    out = {s: {"days": 0, "paid_hours": Decimal("0")} for s in STATUSES}
    for r in rows:
        out[r.status]["days"] += 1
        out[r.status]["paid_hours"] += r.paid_hours
    return {"week_start": start.isoformat(), "by_status": {s: {"days": v["days"], "paid_hours": f"{v['paid_hours']:.2f}"} for s, v in out.items()}, "workers": len({r.worker_id for r in rows})}


@router.post("/timesheets", status_code=201)
def enter_day(body: OfficeCreateIn, request: Request, caller: Caller = Depends(require_permission("payroll", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Enter a day for a worker who has none yet. It still needs approving, by someone other than that worker."""
    worker = db.get(Worker, body.worker_id)
    if worker is None or worker.org_id != caller.org_id or worker.deleted_at is not None:
        raise HTTPException(404, "Worker not found")
    if body.work_date > today_for(_org(db, caller.org_id)):
        raise HTTPException(422, "A day that has not happened yet cannot be filled in")
    if db.scalars(select(TimesheetDay).where(TimesheetDay.worker_id == worker.id, TimesheetDay.work_date == body.work_date)).first() is not None:
        raise HTTPException(409, "This worker already has a timesheet for that day. Open it to change it.")
    row = TimesheetDay(org_id=caller.org_id, worker_id=worker.id, work_date=body.work_date, source="office", created_by=caller.user.id, revision=0, status="submitted", submitted_at=utcnow())
    db.add(row)
    apply_day(db, caller.org_id, row, body.kind, body.shifts, body.notes)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="timesheet.entered", target_type="timesheet_day", target_id=row.id,
          detail={"worker_id": worker.id, "work_date": body.work_date.isoformat()})
    db.commit()
    return day_out(row, *_lookups(db, [row]))


@router.patch("/timesheets/{entry_id}")
def edit_day(entry_id: str, body: OfficeDayIn, request: Request, caller: Caller = Depends(require_permission("payroll", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Correct a day (times, a public holiday, a night shift that replaced a day shift). A locked day
    cannot be changed. Changing an approved day puts it back to waiting, so it is approved again as it now stands."""
    row = _get_day(db, caller.org_id, entry_id)
    if row.status == "locked":
        raise HTTPException(409, "This day has been paid in a finalised pay run and is locked.")
    _check_revision(row, body.expected_revision)
    was = row.status
    apply_day(db, caller.org_id, row, body.kind, body.shifts, body.notes)
    if was in ("approved", "draft", "rejected"):
        row.status, row.submitted_at, row.reviewed_by, row.reviewed_at, row.reject_reason = "submitted", utcnow(), None, None, None
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="timesheet.edited", target_type="timesheet_day", target_id=row.id, detail={"status_before": was})
    db.commit()
    return day_out(row, *_lookups(db, [row]))


def _review(db: Session, request: Request, caller: Caller, ids: list[str], target: str, reason: str | None) -> dict[str, int]:
    own = worker_for_user(db, caller.user)
    counts = {"changed": 0, "unchanged": 0, "not_found": 0, "own_skipped": 0}
    allowed = ("submitted",) if target == "approved" else ("submitted", "approved")
    for entry_id in dict.fromkeys(ids):
        row = db.get(TimesheetDay, entry_id)
        if row is None or row.org_id != caller.org_id:
            counts["not_found"] += 1
        elif own is not None and own.id == row.worker_id:
            counts["own_skipped"] += 1  # nobody reviews their own hours
        elif row.status not in allowed:
            counts["unchanged"] += 1
        else:
            row.status, row.reviewed_by, row.reviewed_at, row.reject_reason, row.revision = target, caller.user.id, utcnow(), reason, row.revision + 1
            audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"timesheet.{target}", target_type="timesheet_day", target_id=row.id,
                  detail={"worker_id": row.worker_id, "work_date": row.work_date.isoformat(), "reason": reason})
            counts["changed"] += 1
    db.commit()
    return counts


@router.post("/timesheets/approve")
def approve(body: IdsIn, request: Request, caller: Caller = Depends(require_permission("payroll", "edit")), db: Session = Depends(get_db)) -> dict[str, int]:
    return _review(db, request, caller, body.ids, "approved", None)


@router.post("/timesheets/reject")
def reject(body: IdsIn, request: Request, caller: Caller = Depends(require_permission("payroll", "edit")), db: Session = Depends(get_db)) -> dict[str, int]:
    """Send days back to the worker with a reason. A locked day cannot be sent back."""
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(422, "Say why the day is being sent back, so the worker knows what to fix")
    return _review(db, request, caller, body.ids, "rejected", reason)


@router.delete("/timesheets/{entry_id}", status_code=204)
def delete_day(entry_id: str, request: Request, caller: Caller = Depends(require_permission("payroll", "edit")), db: Session = Depends(get_db)) -> Response:
    """Remove a day that was never approved. An approved or paid day is kept."""
    row = _get_day(db, caller.org_id, entry_id)
    if row.status in ("approved", "locked"):
        raise HTTPException(409, "An approved or paid day cannot be removed. Send it back first if it is wrong.")
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="timesheet.deleted", target_type="timesheet_day", target_id=row.id,
          detail={"worker_id": row.worker_id, "work_date": row.work_date.isoformat(), "status": row.status, "paid_hours": str(row.paid_hours)})
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/timesheets/prefill")
def prefill(body: WeekIn, request: Request, caller: Caller = Depends(require_permission("payroll", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Start drafts from site sign-ons: for each worker and day in the week with completed site visits
    and no timesheet yet. An existing day is never touched, and the worker still has to check and send it."""
    org = _org(db, caller.org_id)
    start = current_week(org, body.week_start)
    try:
        zone = ZoneInfo(org.timezone or "Australia/Hobart")
    except Exception:
        zone = ZoneInfo("Australia/Hobart")
    lower = dt.datetime.combine(start, dt.time.min, tzinfo=zone)
    visits = db.scalars(select(SiteAttendance).where(SiteAttendance.org_id == caller.org_id, SiteAttendance.kind == "worker", SiteAttendance.worker_id.is_not(None),
                                                     SiteAttendance.signed_out_at.is_not(None), SiteAttendance.signed_in_at >= lower,
                                                     SiteAttendance.signed_in_at < lower + dt.timedelta(days=7)).order_by(SiteAttendance.signed_in_at)).all()
    grouped: dict[tuple[str, dt.date], list[dict[str, Any]]] = {}
    for v in visits:
        begin, end = v.signed_in_at.astimezone(zone), v.signed_out_at.astimezone(zone)
        if (end - begin).total_seconds() < 60:
            continue
        grouped.setdefault((v.worker_id, begin.date()), []).append({"start": begin.strftime("%H:%M"), "finish": end.strftime("%H:%M"), "next_day": end.date() > begin.date(), "break_minutes": 0,
                                                                    "break_start": None, "site_id": v.site_id, "category": "site", "note": "From the site sign-on"})
    counts = {"created": 0, "already_there": 0, "could_not_use": 0}
    for (worker_id, day), shifts in grouped.items():
        if db.scalars(select(TimesheetDay).where(TimesheetDay.worker_id == worker_id, TimesheetDay.work_date == day)).first() is not None:
            counts["already_there"] += 1
            continue
        try:
            hours = engine.allocate_shifts([{**s, "date": day} for s in shifts])
        except engine.EngineError:
            counts["could_not_use"] += 1  # overlapping or impossible sign-on times: left for a person to enter
            continue
        db.add(TimesheetDay(org_id=caller.org_id, worker_id=worker_id, work_date=day, kind="work", shifts=shifts, hours=hours, paid_hours=engine.paid_hours(hours), status="draft",
                            source="prefill", revision=1, rule_version=engine.RULE_VERSION, created_by=caller.user.id, notes="Started from site sign-ons. Check the times and breaks."))
        counts["created"] += 1
    if counts["created"]:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="timesheet.prefilled", target_type="organisation", target_id=caller.org_id, detail={"week_start": start.isoformat(), **counts})
    db.commit()
    return {"week_start": start.isoformat(), **counts}
