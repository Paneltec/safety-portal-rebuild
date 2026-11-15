"""Daily jobs (stage 4d): the office issues a job to a worker for a day; the worker sees it,
accepts or declines it, and marks it done.

Rules carried from the baseline:
  * a worker has one active job per day; issuing another needs an explicit override, which cancels
    the earlier one rather than leaving two;
  * "my job today" never falls back to an earlier day's job;
  * issuing a job prepares a text message, which is held in the outbox. Nothing is sent.
"""
import datetime as dt
import re
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import people
from ..db import get_db
from ..deps import Caller, get_caller, require_admin
from ..models import DailyJob, FormTemplate, Organisation, OutboxMessage, Site, Worker, new_id, utcnow
from ..services import audit, hold_message, iso

router = APIRouter(prefix="/api/daily-jobs", tags=["daily jobs"])

ACTIVE = ("issued", "accepted")
STATUS_LABELS = {"issued": "Waiting for the worker", "accepted": "Accepted", "declined": "Declined", "completed": "Done", "cancelled": "Cancelled"}


class JobFields(BaseModel):
    date: dt.date | None = None  # dt.date: a field called "date" would otherwise shadow the type
    site_id: str | None = None
    site_name: str | None = Field(None, max_length=200)
    address: str | None = Field(None, max_length=300)
    customer: str | None = Field(None, max_length=200)
    truck: str | None = Field(None, max_length=120)
    start_time: str | None = Field(None, max_length=5)
    staff: list[str] = Field(default_factory=list, max_length=30)
    notes: str | None = Field(None, max_length=4000)
    form_template_ids: list[str] = Field(default_factory=list, max_length=10)
    override: bool = False


class JobIn(JobFields):
    worker_id: str


class BulkIn(JobFields):
    worker_ids: list[str] = Field(min_length=1, max_length=100)
    trial_run: bool = False


class JobPatch(BaseModel):
    site_id: str | None = None
    site_name: str | None = Field(None, max_length=200)
    address: str | None = Field(None, max_length=300)
    customer: str | None = Field(None, max_length=200)
    truck: str | None = Field(None, max_length=120)
    start_time: str | None = Field(None, max_length=5)
    staff: list[str] | None = Field(None, max_length=30)
    notes: str | None = Field(None, max_length=4000)
    form_template_ids: list[str] | None = Field(None, max_length=10)


class DeclineIn(BaseModel):
    reason: str = Field(min_length=3, max_length=400)


def _text(value: str | None) -> str | None:
    return (value or "").strip() or None


def _org(db: Session, caller: Caller) -> Organisation:
    return db.get(Organisation, caller.org_id)


def _resolve(db: Session, caller: Caller, body: Any) -> dict[str, Any]:
    """Check and normalise the shared job details."""
    out: dict[str, Any] = {}
    data = body.model_dump(exclude_unset=True)
    site = None
    if "site_id" in data or "site_name" in data:
        if data.get("site_id"):
            site = db.get(Site, data["site_id"])
            if site is None or site.org_id != caller.org_id or site.deleted_at is not None:
                raise HTTPException(404, "Site not found")
        out["site_id"] = site.id if site else None
        out["site_name"] = site.name if site else _text(data.get("site_name"))
    # A typed address wins; otherwise a chosen site supplies its own.
    if _text(data.get("address")):
        out["address"] = _text(data["address"])
    elif site is not None:
        out["address"] = ", ".join(x for x in (site.address, site.suburb) if x) or None
    elif "address" in data:
        out["address"] = None
    for key in ("customer", "truck", "notes"):
        if key in data:
            out[key] = _text(data[key])
    if "start_time" in data:
        value = _text(data["start_time"])
        if value is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise HTTPException(422, "Give the start time as HH:MM, for example 06:30")
        out["start_time"] = value
    if data.get("staff") is not None:
        out["staff"] = list(dict.fromkeys(s.strip() for s in data["staff"] if s and s.strip()))
    if data.get("form_template_ids") is not None:
        ids = list(dict.fromkeys(data["form_template_ids"]))
        for template_id in ids:
            template = db.get(FormTemplate, template_id)
            if template is None or template.org_id != caller.org_id or template.deleted_at is not None or template.status != "published":
                raise HTTPException(422, "A linked form was not found or is not published")
        out["form_template_ids"] = ids
    return out


def job_out(db: Session, job: DailyJob, *, forms: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": job.id, "date": job.job_date.isoformat(), "worker_id": job.worker_id, "worker_name": job.worker_name,
        "site_id": job.site_id, "site_name": job.site_name, "address": job.address, "customer": job.customer, "truck": job.truck,
        "start_time": job.start_time, "staff": list(job.staff or []), "notes": job.notes,
        "status": job.status, "status_label": STATUS_LABELS[job.status], "decline_reason": job.decline_reason,
        "issued_by": job.issued_by_name, "issued_at": iso(job.issued_at), "responded_at": iso(job.responded_at),
        "completed_at": iso(job.completed_at), "cancelled_at": iso(job.cancelled_at), "batch_id": job.batch_id,
    }
    message = db.get(OutboxMessage, job.outbox_message_id) if job.outbox_message_id else None
    # Whether the worker was actually told. Until SMS is connected this is "held" or "none".
    out["delivery"] = message.status if message else "none"
    if forms:
        templates = [db.get(FormTemplate, tid) for tid in (job.form_template_ids or [])]
        out["forms"] = [{"id": t.id, "name": t.name, "available": t.status == "published" and t.deleted_at is None} for t in templates if t is not None]
    return out


def _worker(db: Session, caller: Caller, worker_id: str) -> Worker | None:
    worker = db.get(Worker, worker_id)
    if worker is None or worker.org_id != caller.org_id or worker.deleted_at is not None:
        return None
    return worker


def _active(db: Session, worker_id: str, day: date) -> DailyJob | None:
    return db.scalars(select(DailyJob).where(DailyJob.worker_id == worker_id, DailyJob.job_date == day,
                                             DailyJob.status.in_(ACTIVE), DailyJob.deleted_at.is_(None))).first()


def _issue(db: Session, request: Request, caller: Caller, worker: Worker, day: date, details: dict[str, Any], *, override: bool, batch_id: str | None) -> tuple[DailyJob | None, str | None]:
    """Create one job. Returns (job, None) or (None, reason it was skipped)."""
    if worker.archived_at is not None or not worker.active:
        return None, "inactive"
    existing = _active(db, worker.id, day)
    if existing is not None:
        if not override:
            return None, "already_has_job"
        existing.status, existing.cancelled_at = "cancelled", utcnow()
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="daily_job.replaced", target_type="daily_job", target_id=existing.id)
        db.flush()
    job = DailyJob(id=new_id(), org_id=caller.org_id, job_date=day, worker_id=worker.id, worker_name=people.worker_name(worker),
                   issued_by=caller.user.id, issued_by_name=caller.user.name, batch_id=batch_id, **details)
    db.add(job)
    db.flush()
    recipient = worker.mobile or worker.phone
    if recipient:
        held = hold_message(db, org_id=caller.org_id, channel="sms", purpose="daily_job", recipient=recipient,
                            subject=f"Job for {day.strftime('%d/%m/%Y')}", related_type="daily_job", related_id=job.id, created_by=caller.user.id)
        db.flush()
        job.outbox_message_id = held.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="daily_job.issued", target_type="daily_job", target_id=job.id,
          detail={"worker_id": worker.id, "date": day.isoformat(), "site_name": job.site_name})
    return job, None


def _day(db: Session, caller: Caller, value: date | None) -> date:
    today = people.today_for(_org(db, caller))
    day = value or today
    if day < today:
        raise HTTPException(422, "A job cannot be issued for a day that has passed")
    return day


def _need_site(details: dict[str, Any]) -> None:
    if not details.get("site_name"):
        raise HTTPException(422, "Choose a site or type where the job is")


# ---------- the worker's own jobs (declared before /{job_id}) ----------

def _own_worker(db: Session, caller: Caller) -> Worker | None:
    return people.worker_for_user(db, caller.user)


@router.get("/mine/today")
def my_job_today(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The caller's active job for today, or none. Never an earlier day's job."""
    worker = _own_worker(db, caller)
    if worker is None:
        return {"assignment": None, "status": "no_worker_record"}
    job = _active(db, worker.id, people.today_for(_org(db, caller)))
    return {"assignment": job_out(db, job) if job else None, "status": job.status if job else "no_job"}


@router.get("/mine")
def my_jobs(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Today's and upcoming jobs, then the last 30 finished ones."""
    worker = _own_worker(db, caller)
    if worker is None:
        return {"upcoming": [], "recent": [], "status": "no_worker_record"}
    today = people.today_for(_org(db, caller))
    base = select(DailyJob).where(DailyJob.worker_id == worker.id, DailyJob.deleted_at.is_(None))
    upcoming = db.scalars(base.where(DailyJob.job_date >= today, DailyJob.status.in_(ACTIVE)).order_by(DailyJob.job_date, DailyJob.start_time)).all()
    recent = db.scalars(base.where((DailyJob.job_date < today) | DailyJob.status.not_in(ACTIVE)).order_by(DailyJob.job_date.desc(), DailyJob.issued_at.desc()).limit(30)).all()
    return {"upcoming": [job_out(db, j) for j in upcoming], "recent": [job_out(db, j) for j in recent], "status": "ok"}


# ---------- office ----------

@router.get("")
def list_jobs(date: date | None = None, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: A002
    """Jobs for one day (today by default), newest first, with a count by status."""
    day = date or people.today_for(_org(db, caller))
    rows = db.scalars(select(DailyJob).where(DailyJob.org_id == caller.org_id, DailyJob.job_date == day, DailyJob.deleted_at.is_(None))
                      .order_by(DailyJob.issued_at.desc())).all()
    counts = {key: 0 for key in STATUS_LABELS}
    for job in rows:
        counts[job.status] += 1
    return {"date": day.isoformat(), "items": [job_out(db, j) for j in rows], "counts": counts}


@router.post("", status_code=201)
def create_job(body: JobIn, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    worker = _worker(db, caller, body.worker_id)
    if worker is None:
        raise HTTPException(404, "Worker not found")
    day = _day(db, caller, body.date)
    details = _resolve(db, caller, body)
    _need_site(details)
    try:
        job, skipped = _issue(db, request, caller, worker, day, details, override=body.override, batch_id=None)
        if job is None:
            db.rollback()
            if skipped == "inactive":
                raise HTTPException(409, f"{people.worker_name(worker)} is inactive or archived, so cannot be given a job")
            raise HTTPException(409, f"{people.worker_name(worker)} already has a job for that day. Tick “replace their current job” to issue this one instead.")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"{people.worker_name(worker)} already has a job for that day.") from None
    return job_out(db, job)


@router.post("/bulk-create")
def bulk_create(body: BulkIn, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Issue the same job to several workers. `trial_run` reports what would happen and saves nothing."""
    day = _day(db, caller, body.date)
    details = _resolve(db, caller, body)
    _need_site(details)
    batch_id = new_id()
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for worker_id in dict.fromkeys(body.worker_ids):
        worker = _worker(db, caller, worker_id)
        if worker is None:
            skipped.append({"worker_id": worker_id, "name": None, "reason": "not_found"})
            continue
        name = people.worker_name(worker)
        if body.trial_run:
            existing = _active(db, worker.id, day)
            if worker.archived_at is not None or not worker.active:
                skipped.append({"worker_id": worker.id, "name": name, "reason": "inactive"})
            elif existing is not None and not body.override:
                skipped.append({"worker_id": worker.id, "name": name, "reason": "already_has_job"})
            else:
                created.append({"worker_id": worker.id, "worker_name": name, "replaces_existing": existing is not None})
            continue
        job, reason = _issue(db, request, caller, worker, day, dict(details), override=body.override, batch_id=batch_id)
        if job is None:
            skipped.append({"worker_id": worker.id, "name": name, "reason": reason})
        else:
            created.append(job_out(db, job, forms=False))
    if body.trial_run:
        db.rollback()
    else:
        db.commit()
    return {"trial_run": body.trial_run, "date": day.isoformat(), "batch_id": None if body.trial_run else batch_id, "created": created, "skipped": skipped}


def _job(db: Session, caller: Caller, job_id: str) -> DailyJob:
    job = db.get(DailyJob, job_id)
    if job is None or job.org_id != caller.org_id or job.deleted_at is not None:
        raise HTTPException(404, "Job not found")
    return job


def _mine(db: Session, caller: Caller, job_id: str) -> DailyJob:
    """A job the caller was given. Someone else's job is 'not found', whatever the caller's role."""
    job = _job(db, caller, job_id)
    worker = _own_worker(db, caller)
    if worker is None or worker.id != job.worker_id:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/{job_id}")
def get_job(job_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    return job_out(db, _job(db, caller, job_id) if caller.is_admin else _mine(db, caller, job_id))


@router.patch("/{job_id}")
def update_job(job_id: str, body: JobPatch, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    job = _job(db, caller, job_id)
    if job.status not in ACTIVE:
        raise HTTPException(409, "Only a job that is still open can be changed")
    details = _resolve(db, caller, body)
    if "site_name" in details and not details["site_name"]:
        raise HTTPException(422, "Choose a site or type where the job is")
    for key, value in details.items():
        setattr(job, key, value)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="daily_job.updated", target_type="daily_job", target_id=job.id, detail={"fields": sorted(details)})
    db.commit()
    return job_out(db, job)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    job = _job(db, caller, job_id)
    if job.status in ACTIVE:
        job.status, job.cancelled_at = "cancelled", utcnow()
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="daily_job.cancelled", target_type="daily_job", target_id=job.id)
        db.commit()
    return job_out(db, job)


@router.post("/{job_id}/accept")
def accept_job(job_id: str, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    job = _mine(db, caller, job_id)
    if job.status == "accepted":
        return job_out(db, job)  # a repeated tap is harmless
    if job.status != "issued":
        raise HTTPException(409, f"This job is {STATUS_LABELS[job.status].lower()} and can no longer be accepted")
    job.status, job.responded_at = "accepted", utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="daily_job.accepted", target_type="daily_job", target_id=job.id)
    db.commit()
    return job_out(db, job)


@router.post("/{job_id}/decline")
def decline_job(job_id: str, body: DeclineIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    job = _mine(db, caller, job_id)
    if job.status == "declined":
        return job_out(db, job)
    if job.status not in ACTIVE:
        raise HTTPException(409, f"This job is {STATUS_LABELS[job.status].lower()} and can no longer be declined")
    job.status, job.responded_at, job.decline_reason = "declined", utcnow(), body.reason.strip()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="daily_job.declined", target_type="daily_job", target_id=job.id)
    db.commit()
    return job_out(db, job)


@router.post("/{job_id}/complete")
def complete_job(job_id: str, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    job = _mine(db, caller, job_id)
    if job.status == "completed":
        return job_out(db, job)
    if job.status != "accepted":
        raise HTTPException(409, "Accept the job before marking it done")
    job.status, job.completed_at = "completed", utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="daily_job.completed", target_type="daily_job", target_id=job.id)
    db.commit()
    return job_out(db, job)


def picker_items(db: Session, caller: Caller) -> list[dict[str, Any]]:
    """Jobs a person can attach a form to: their own from the last week onward; admins see the organisation's."""
    org = db.get(Organisation, caller.org_id)
    today = people.today_for(org)
    stmt = select(DailyJob).where(DailyJob.org_id == caller.org_id, DailyJob.deleted_at.is_(None),
                                  DailyJob.job_date >= today - timedelta(days=7), DailyJob.status.in_(("issued", "accepted", "completed")))
    if not caller.is_admin:
        worker = people.worker_for_user(db, caller.user)
        stmt = stmt.where(DailyJob.worker_id == (worker.id if worker else ""))
    rows = db.scalars(stmt.order_by(DailyJob.job_date.desc(), func.lower(DailyJob.site_name)).limit(100)).all()
    return [{"id": j.id, "label": f"{j.site_name} · {j.job_date.strftime('%d/%m/%Y')}" + (f" · {j.worker_name}" if caller.is_admin else "")} for j in rows]
