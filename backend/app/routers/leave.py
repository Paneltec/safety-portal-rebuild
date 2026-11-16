"""Leave requests: asking, deciding, cancelling (stage 7a).

  * a worker asks for their own leave; the office can enter one on a worker's behalf;
  * nobody decides their own request;
  * hours are worked out from the working days in the range unless the person gives them;
  * two requests for the same worker cannot cover the same day;
  * leave a finalised pay run has already used is not silently undone by cancelling: the request
    is marked cancelled and flagged for payroll to correct.

Nothing here sends a message to anyone.
"""
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Caller, get_caller, require_permission
from ..files import file_response, save_upload
from ..models import LeaveRequest, Organisation, StoredFile, User, Worker, utcnow
from ..people import today_for, worker_for_user, worker_name
from ..services import audit, iso

router = APIRouter(prefix="/api/leave", tags=["leave"])
me_router = APIRouter(prefix="/api/me/leave", tags=["leave"])

TYPES = {"annual": "Annual leave", "personal": "Personal (sick or carer's) leave", "unpaid": "Unpaid leave", "long_service": "Long service leave", "other": "Other leave"}
STATUSES = {"requested": "Waiting for a decision", "approved": "Approved", "rejected": "Not approved", "cancelled": "Cancelled"}
LeaveType = Literal["annual", "personal", "unpaid", "long_service", "other"]
ACTIVE = ("requested", "approved")
CERT_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
MAX_SPAN_DAYS = 92


class LeaveIn(BaseModel):
    leave_type: LeaveType
    start_date: dt.date
    end_date: dt.date
    hours: float | str | None = None
    reason: str | None = Field(default=None, max_length=2000)


class OfficeLeaveIn(LeaveIn):
    worker_id: str


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=600)


class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=600)


class SettingsIn(BaseModel):
    hours_per_day: float = Field(gt=0, le=24)
    backdate_days: int = Field(ge=0, le=366)
    work_days: list[int] = Field(min_length=1, max_length=7)


def _org(db: Session, org_id: str) -> Organisation:
    return db.get(Organisation, org_id)


def settings(org: Organisation) -> dict[str, Any]:
    raw = (org.details or {}).get("leave") or {}
    days = [d for d in raw.get("work_days", [0, 1, 2, 3, 4]) if isinstance(d, int) and 0 <= d <= 6]
    try:
        hours = Decimal(str(raw.get("hours_per_day", "7.6")))
    except InvalidOperation:
        hours = Decimal("7.6")
    return {"hours_per_day": hours if 0 < hours <= 24 else Decimal("7.6"), "backdate_days": int(raw.get("backdate_days", 14)), "work_days": sorted(set(days)) or [0, 1, 2, 3, 4]}


def _hours(value: Any, span_days: int) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        hours = Decimal(str(value))
    except InvalidOperation:
        raise HTTPException(422, "Hours must be a number") from None
    if not hours.is_finite() or hours <= 0 or hours > Decimal(24) * span_days:
        raise HTTPException(422, "Hours must be more than 0 and no more than 24 for each day")
    return hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build(db: Session, org: Organisation, worker: Worker, body: LeaveIn, *, office: bool, exclude_id: str | None = None) -> dict[str, Any]:
    """Validate a request and work out its days and hours."""
    conf = settings(org)
    today = today_for(org)
    if body.end_date < body.start_date:
        raise HTTPException(422, "The last day can't be before the first day.")
    span = (body.end_date - body.start_date).days + 1
    if span > MAX_SPAN_DAYS:
        raise HTTPException(422, f"One request can cover at most {MAX_SPAN_DAYS} days. Split longer leave into more than one request.")
    if not office and body.start_date < today - dt.timedelta(days=conf["backdate_days"]):
        raise HTTPException(422, f"That's more than {conf['backdate_days']} days ago. Talk to the office.")
    if body.start_date > today + dt.timedelta(days=550):
        raise HTTPException(422, "That is too far ahead to book.")
    working = [body.start_date + dt.timedelta(days=i) for i in range(span) if (body.start_date + dt.timedelta(days=i)).weekday() in conf["work_days"]]
    given = _hours(body.hours, span)
    if given is None and not working:
        raise HTTPException(422, "Those dates are not working days. Enter the hours you need.")
    if given is not None:
        target = working or [body.start_date + dt.timedelta(days=i) for i in range(span)]
        each = (given / len(target)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        days = [{"date": d.isoformat(), "hours": str(each)} for d in target]
        days[-1]["hours"] = str(given - each * (len(target) - 1))  # the last day takes any rounding remainder
        total = given
    else:
        days = [{"date": d.isoformat(), "hours": str(conf["hours_per_day"].quantize(Decimal("0.01")))} for d in working]
        total = (conf["hours_per_day"] * len(working)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    clash = select(LeaveRequest).where(LeaveRequest.worker_id == worker.id, LeaveRequest.status.in_(ACTIVE), LeaveRequest.start_date <= body.end_date,
                                       LeaveRequest.end_date >= body.start_date)
    if exclude_id:
        clash = clash.where(LeaveRequest.id != exclude_id)
    if db.scalars(clash).first() is not None:
        raise HTTPException(409, "There is already leave booked over some of those days.")
    return {"leave_type": body.leave_type, "start_date": body.start_date, "end_date": body.end_date, "hours": total, "days": days, "reason": (body.reason or "").strip() or None}


def leave_out(r: LeaveRequest, workers: dict[str, str], users: dict[str, str], caller: Caller, own_worker_id: str | None) -> dict[str, Any]:
    own = r.worker_id == own_worker_id
    can_decide = r.status == "requested" and not own and caller.can("workers", "edit") and caller.can("workers", "team_view")
    return {"id": r.id, "worker_id": r.worker_id, "worker": workers.get(r.worker_id, ""), "leave_type": r.leave_type, "leave_type_label": TYPES.get(r.leave_type, r.leave_type),
            "start_date": r.start_date.isoformat(), "end_date": r.end_date.isoformat(), "hours": f"{r.hours:.2f}", "days": r.days or [], "reason": r.reason,
            "status": r.status, "status_label": STATUSES.get(r.status, r.status), "source": r.source, "requested_by": users.get(r.requested_by or ""),
            "decided_by": users.get(r.decided_by or ""), "decided_at": iso(r.decided_at), "decision_note": r.decision_note, "cancelled_by": users.get(r.cancelled_by or ""),
            "cancelled_at": iso(r.cancelled_at), "cancel_reason": r.cancel_reason, "has_certificate": r.certificate_file_id is not None,
            "used_by_payroll": r.pay_run_id is not None, "needs_payroll_correction": r.needs_payroll_correction, "is_own": own, "can_decide": can_decide,
            "can_cancel": r.status in ACTIVE and (own or (caller.can("workers", "edit") and caller.can("workers", "team_view"))), "created_at": iso(r.created_at)}


def _names(db: Session, rows: list[LeaveRequest]) -> tuple[dict[str, str], dict[str, str]]:
    worker_ids = {r.worker_id for r in rows}
    user_ids = {u for r in rows for u in (r.requested_by, r.decided_by, r.cancelled_by) if u}
    workers = {w.id: worker_name(w) for w in db.scalars(select(Worker).where(Worker.id.in_(worker_ids))).all()} if worker_ids else {}
    users = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
    return workers, users


def _out(db: Session, caller: Caller, row: LeaveRequest) -> dict[str, Any]:
    workers, users = _names(db, [row])
    own = worker_for_user(db, caller.user)
    return leave_out(row, workers, users, caller, own.id if own else None)


def _get(db: Session, caller: Caller, leave_id: str) -> LeaveRequest:
    """A request the caller may see: their own, or anyone's in their organisation with team access."""
    row = db.get(LeaveRequest, leave_id)
    if row is None or row.org_id != caller.org_id:
        raise HTTPException(404, "Leave request not found")
    own = worker_for_user(db, caller.user)
    if not (caller.can("workers", "view") and caller.can("workers", "team_view")) and not (own is not None and own.id == row.worker_id):
        raise HTTPException(404, "Leave request not found")
    return row


def _cancel(db: Session, request: Request, caller: Caller, row: LeaveRequest, reason: str | None) -> None:
    if row.status not in ACTIVE:
        raise HTTPException(409, f"This request is already {STATUSES[row.status].lower()}.")
    if row.pay_run_id is not None:
        if not (reason and reason.strip()):
            raise HTTPException(422, "This leave has already been used in a pay run. Say why it is being cancelled so payroll can correct it.")
        row.needs_payroll_correction = True
    row.status, row.cancelled_by, row.cancelled_at, row.cancel_reason = "cancelled", caller.user.id, utcnow(), (reason or "").strip() or None
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="leave.cancelled", target_type="leave_request", target_id=row.id,
          detail={"needs_payroll_correction": row.needs_payroll_correction})


# ---------- my leave ----------

def _me(db: Session, caller: Caller) -> Worker:
    worker = worker_for_user(db, caller.user)
    if worker is None:
        raise HTTPException(409, "Your login is not linked to a worker record, so leave cannot be requested here. Ask the office to link it.")
    return worker


@me_router.get("")
def my_leave(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    worker = worker_for_user(db, caller.user)
    conf = settings(_org(db, caller.org_id))
    if worker is None:
        return {"linked": False, "requests": [], "hours_per_day": str(conf["hours_per_day"]), "types": TYPES, "balances": None}
    rows = db.scalars(select(LeaveRequest).where(LeaveRequest.worker_id == worker.id).order_by(LeaveRequest.start_date.desc()).limit(200)).all()
    workers, users = _names(db, list(rows))
    return {"linked": True, "requests": [leave_out(r, workers, users, caller, worker.id) for r in rows], "hours_per_day": str(conf["hours_per_day"]), "types": TYPES,
            "balances": None, "balances_note": "Leave balances are kept by payroll and are not shown here yet."}


@me_router.post("", status_code=201)
def request_leave(body: LeaveIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    worker = _me(db, caller)
    row = LeaveRequest(org_id=caller.org_id, worker_id=worker.id, source="portal", requested_by=caller.user.id, **build(db, _org(db, caller.org_id), worker, body, office=False))
    db.add(row)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="leave.requested", target_type="leave_request", target_id=row.id,
          detail={"leave_type": row.leave_type, "start_date": row.start_date.isoformat(), "end_date": row.end_date.isoformat(), "hours": str(row.hours)})
    db.commit()
    return _out(db, caller, row)


def _mine(db: Session, caller: Caller, leave_id: str) -> LeaveRequest:
    worker = _me(db, caller)
    row = db.get(LeaveRequest, leave_id)
    if row is None or row.org_id != caller.org_id or row.worker_id != worker.id:
        raise HTTPException(404, "Leave request not found")
    return row


@me_router.post("/{leave_id}/cancel")
def cancel_mine(leave_id: str, body: CancelIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _mine(db, caller, leave_id)
    _cancel(db, request, caller, row, body.reason)
    db.commit()
    return _out(db, caller, row)


@me_router.post("/{leave_id}/certificate")
def upload_certificate(leave_id: str, request: Request, file: UploadFile = File(...), caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Attach a medical certificate or other evidence to your own request."""
    row = _mine(db, caller, leave_id)
    if row.status not in ACTIVE:
        raise HTTPException(409, "Evidence can only be added to a request that is waiting or approved.")
    stored = save_upload(db, org_id=caller.org_id, upload=file, purpose="leave_certificate", created_by=caller.user.id, allowed_mimes=CERT_TYPES)
    row.certificate_file_id = stored.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="leave.certificate_added", target_type="leave_request", target_id=row.id)
    db.commit()
    return _out(db, caller, row)


# ---------- the office ----------

def _team(caller: Caller = Depends(require_permission("workers", "view"))) -> Caller:
    if not caller.can("workers", "team_view"):
        raise HTTPException(403, "You do not have permission to do this")
    return caller


def _decider(caller: Caller = Depends(require_permission("workers", "edit"))) -> Caller:
    if not caller.can("workers", "team_view"):
        raise HTTPException(403, "You do not have permission to do this")
    return caller


@router.get("")
def list_leave(status: str | None = None, worker_id: str | None = None, date_from: dt.date | None = None, date_to: dt.date | None = None,
               caller: Caller = Depends(_team), db: Session = Depends(get_db)) -> dict[str, Any]:
    query = select(LeaveRequest).where(LeaveRequest.org_id == caller.org_id)
    if status:
        query = query.where(LeaveRequest.status == status)
    if worker_id:
        query = query.where(LeaveRequest.worker_id == worker_id)
    if date_from:
        query = query.where(LeaveRequest.end_date >= date_from)
    if date_to:
        query = query.where(LeaveRequest.start_date <= date_to)
    rows = db.scalars(query.order_by(LeaveRequest.start_date.desc(), LeaveRequest.created_at.desc()).limit(500)).all()
    workers, users = _names(db, list(rows))
    own = worker_for_user(db, caller.user)
    return {"items": [leave_out(r, workers, users, caller, own.id if own else None) for r in rows], "types": TYPES, "statuses": STATUSES,
            "can_edit": caller.can("workers", "edit")}


@router.get("/summary")
def summary(caller: Caller = Depends(_team), db: Session = Depends(get_db)) -> dict[str, Any]:
    today = today_for(_org(db, caller.org_id))
    base = select(func.count()).select_from(LeaveRequest).where(LeaveRequest.org_id == caller.org_id)
    return {"waiting": db.scalar(base.where(LeaveRequest.status == "requested")) or 0,
            "on_leave_today": db.scalar(base.where(LeaveRequest.status == "approved", LeaveRequest.start_date <= today, LeaveRequest.end_date >= today)) or 0,
            "starting_within_14_days": db.scalar(base.where(LeaveRequest.status == "approved", LeaveRequest.start_date > today, LeaveRequest.start_date <= today + dt.timedelta(days=14))) or 0,
            "needs_payroll_correction": db.scalar(base.where(LeaveRequest.needs_payroll_correction.is_(True))) or 0}


@router.get("/on-date")
def on_date(date: dt.date | None = None, caller: Caller = Depends(_team), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Who is on approved leave on a day (today when no date is given)."""
    day = date or today_for(_org(db, caller.org_id))
    rows = db.scalars(select(LeaveRequest).where(LeaveRequest.org_id == caller.org_id, LeaveRequest.status == "approved", LeaveRequest.start_date <= day,
                                                 LeaveRequest.end_date >= day)).all()
    workers, _ = _names(db, list(rows))
    return {"date": day.isoformat(), "items": sorted(({"worker_id": r.worker_id, "worker": workers.get(r.worker_id, ""), "leave_type_label": TYPES.get(r.leave_type, r.leave_type),
                                                       "until": r.end_date.isoformat()} for r in rows), key=lambda x: x["worker"].lower())}


@router.get("/settings")
def get_settings(caller: Caller = Depends(require_permission("workers", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    conf = settings(_org(db, caller.org_id))
    return {"hours_per_day": str(conf["hours_per_day"]), "backdate_days": conf["backdate_days"], "work_days": conf["work_days"], "can_edit": caller.can("workers", "edit") and caller.is_admin}


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request, caller: Caller = Depends(require_permission("workers", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    if not caller.is_admin:
        raise HTTPException(403, "Admin only")
    if any(d < 0 or d > 6 for d in body.work_days):
        raise HTTPException(422, "Work days are numbered 0 (Monday) to 6 (Sunday)")
    org = _org(db, caller.org_id)
    org.details = {**(org.details or {}), "leave": {"hours_per_day": str(Decimal(str(body.hours_per_day)).quantize(Decimal("0.01"))), "backdate_days": body.backdate_days,
                                                    "work_days": sorted(set(body.work_days))}}
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="leave.settings_changed", target_type="organisation", target_id=org.id, detail=org.details["leave"])
    db.commit()
    return get_settings(caller, db)


@router.post("", status_code=201)
def enter_leave(body: OfficeLeaveIn, request: Request, caller: Caller = Depends(_decider), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Enter a request for a worker (a phone call, a paper form). It still waits for a decision,
    and the person who entered their own cannot decide it."""
    worker = db.get(Worker, body.worker_id)
    if worker is None or worker.org_id != caller.org_id or worker.deleted_at is not None:
        raise HTTPException(404, "Worker not found")
    row = LeaveRequest(org_id=caller.org_id, worker_id=worker.id, source="office", requested_by=caller.user.id, **build(db, _org(db, caller.org_id), worker, body, office=True))
    db.add(row)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="leave.entered", target_type="leave_request", target_id=row.id,
          detail={"worker_id": worker.id, "leave_type": row.leave_type, "start_date": row.start_date.isoformat(), "end_date": row.end_date.isoformat()})
    db.commit()
    return _out(db, caller, row)


@router.get("/{leave_id}")
def get_leave(leave_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _out(db, caller, _get(db, caller, leave_id))


@router.post("/{leave_id}/decision")
def decide(leave_id: str, body: DecisionIn, request: Request, caller: Caller = Depends(_decider), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _get(db, caller, leave_id)
    own = worker_for_user(db, caller.user)
    if own is not None and own.id == row.worker_id:
        raise HTTPException(403, "You cannot decide your own leave request. Someone else needs to.")
    if row.status != "requested":
        raise HTTPException(409, f"This request is already {STATUSES[row.status].lower()}.")
    note = (body.note or "").strip() or None
    if body.decision == "reject" and not note:
        raise HTTPException(422, "Say why the request is not approved, so the person knows.")
    row.status, row.decided_by, row.decided_at, row.decision_note = ("approved" if body.decision == "approve" else "rejected"), caller.user.id, utcnow(), note
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"leave.{row.status}", target_type="leave_request", target_id=row.id, detail={"note": note})
    db.commit()
    return _out(db, caller, row)


@router.post("/{leave_id}/cancel")
def cancel(leave_id: str, body: CancelIn, request: Request, caller: Caller = Depends(_decider), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _get(db, caller, leave_id)
    _cancel(db, request, caller, row, body.reason)
    db.commit()
    return _out(db, caller, row)


@router.get("/{leave_id}/certificate")
def certificate(leave_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    """The evidence attached to a request: for the person themselves, or the office with edit access."""
    row = _get(db, caller, leave_id)
    own = worker_for_user(db, caller.user)
    if not (own is not None and own.id == row.worker_id) and not (caller.can("workers", "edit") and caller.can("workers", "team_view")):
        raise HTTPException(403, "You do not have permission to do this")
    stored = db.get(StoredFile, row.certificate_file_id) if row.certificate_file_id else None
    if stored is None:
        raise HTTPException(404, "No evidence is attached to this request")
    return file_response(stored, inline=True)
