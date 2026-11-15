"""Workers register and certifications (stage 4a).

A worker is the operational person record. It is separate from a login and linked to one
explicitly (users.worker_id). Imports upsert on the external source key, never on a name.

Privacy tiers on a worker record:
  directory   name, position, company                       anyone who can view workers (own record only without team view)
  work        work email and phone numbers                  team viewers, and the worker themself
  personal    home address, emergency contact, notes        workers.edit or hr_employees.view, and the worker themself
  protected   birth date, tax file number, USI              stored encrypted; listed masked to hr_employees.view;
                                                            full value only through an audited reveal (hr_employees.reveal_pii)
"""
import re
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import comms, people
from ..crypto import decrypt_json, encrypt_json, key_status
from ..db import get_db
from ..deps import Caller, ensure_can_grant, get_caller, require_admin, require_permission
from ..files import file_out, file_response, save_upload
from ..models import (
    AuditLog,
    Certification,
    CertificationKind,
    Company,
    Organisation,
    StoredFile,
    User,
    Worker,
    utcnow,
)
from ..services import audit, hold_message, iso, issue_auth_token, link_for, require_assignable_role

router = APIRouter(prefix="/api/workers", tags=["workers"])
cert_router = APIRouter(prefix="/api/certifications", tags=["certifications"])
kinds_router = APIRouter(prefix="/api/certification-kinds", tags=["certifications"])

IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
EVIDENCE_TYPES = IMAGE_TYPES | {"application/pdf"}
PHOTO_MAX_BYTES = 8 * 1024 * 1024

PERSONAL_FIELDS = ("street_address", "suburb", "state", "postal_code", "country", "additional_notes",
                   "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relationship")
OFFICE_FIELDS = ("employee_id", "date_employee_added")
PROTECTED_FIELDS = ("birth_date", "tax_file_number", "usi_number")
PROTECTED_LABELS = {"birth_date": "Date of birth", "tax_file_number": "Tax file number", "usi_number": "USI"}


# ---------- request bodies ----------

class WorkerIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field("", max_length=100)
    preferred_name: str | None = Field(None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=40)
    mobile: str | None = Field(None, max_length=40)
    position: str | None = Field(None, max_length=120)
    company_id: str | None = None
    active: bool = True
    street_address: str | None = Field(None, max_length=200)
    suburb: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=40)
    postal_code: str | None = Field(None, max_length=12)
    country: str | None = Field(None, max_length=60)
    additional_notes: str | None = Field(None, max_length=4000)
    emergency_contact_name: str | None = Field(None, max_length=160)
    emergency_contact_phone: str | None = Field(None, max_length=40)
    emergency_contact_relationship: str | None = Field(None, max_length=80)
    employee_id: str | None = Field(None, max_length=40)
    date_employee_added: date | None = None
    birth_date: date | None = None
    tax_file_number: str | None = Field(None, max_length=20)
    usi_number: str | None = Field(None, max_length=20)


class WorkerPatch(WorkerIn):
    first_name: str | None = Field(None, min_length=1, max_length=100)  # type: ignore[assignment]
    last_name: str | None = Field(None, max_length=100)  # type: ignore[assignment]
    active: bool | None = None  # type: ignore[assignment]


class SelfEdit(BaseModel):
    """The fields a worker may change on their own record. Everything else is office-only."""

    preferred_name: str | None = Field(None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=40)
    mobile: str | None = Field(None, max_length=40)
    street_address: str | None = Field(None, max_length=200)
    suburb: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=40)
    postal_code: str | None = Field(None, max_length=12)
    country: str | None = Field(None, max_length=60)
    emergency_contact_name: str | None = Field(None, max_length=160)
    emergency_contact_phone: str | None = Field(None, max_length=40)
    emergency_contact_relationship: str | None = Field(None, max_length=80)


class LinkUserIn(BaseModel):
    user_id: str


class ProvisionIn(BaseModel):
    role_id: str = "worker"
    email: EmailStr | None = None


class CertIn(BaseModel):
    kind: str = Field(min_length=1, max_length=160)
    number: str | None = Field(None, max_length=80)
    issue_date: date | None = None
    expiry_date: date | None = None
    no_expiry: bool = False
    notes: str | None = Field(None, max_length=2000)


class CertPatch(BaseModel):
    number: str | None = Field(None, max_length=80)
    issue_date: date | None = None
    expiry_date: date | None = None
    no_expiry: bool | None = None
    notes: str | None = Field(None, max_length=2000)


class RenewIn(BaseModel):
    number: str | None = Field(None, max_length=80)
    issue_date: date | None = None
    expiry_date: date | None = None
    no_expiry: bool = False
    notes: str | None = Field(None, max_length=2000)


class KindIn(BaseModel):
    label: str = Field(min_length=2, max_length=160)
    aliases: list[str] = Field(default_factory=list, max_length=30)


class KindPatch(BaseModel):
    label: str | None = Field(None, min_length=2, max_length=160)
    aliases: list[str] | None = Field(None, max_length=30)
    active: bool | None = None


class CertSettingsIn(BaseModel):
    expiry_window_days: int = Field(30, ge=1, le=365)
    expected_kinds: list[str] = Field(default_factory=list, max_length=60)
    gate_enabled: bool = False


# ---------- helpers ----------

def _org(db: Session, caller: Caller) -> Organisation:
    return db.get(Organisation, caller.org_id)


def _is_self(caller: Caller, worker: Worker) -> bool:
    return bool(caller.user.worker_id) and caller.user.worker_id == worker.id


def _get_worker(db: Session, caller: Caller, worker_id: str, *, include_deleted: bool = False) -> Worker:
    """Load a worker the caller may see. Anything else is 'not found', including other organisations."""
    worker = db.get(Worker, worker_id)
    if worker is None or worker.org_id != caller.org_id:
        raise HTTPException(404, "Worker not found")
    if worker.deleted_at is not None and not include_deleted:
        raise HTTPException(404, "Worker not found")
    if not caller.can("workers", "team_view") and not _is_self(caller, worker):
        raise HTTPException(404, "Worker not found")
    return worker


def _status(worker: Worker) -> str:
    if worker.deleted_at is not None:
        return "deleted"
    if worker.archived_at is not None:
        return "archived"
    return "active" if worker.active else "inactive"


def _mask(field: str, value: str) -> str:
    if field == "birth_date":
        return "Recorded"
    tail = re.sub(r"\s", "", value)[-3:]
    return f"•••••• {tail}"


def _linked_user(db: Session, worker: Worker) -> User | None:
    return db.scalars(select(User).where(User.worker_id == worker.id, User.deleted_at.is_(None))).first()


def worker_out(db: Session, caller: Caller, worker: Worker, *, detail: bool = False) -> dict[str, Any]:
    me = _is_self(caller, worker)
    team = caller.can("workers", "team_view")
    personal = me or caller.can("workers", "edit") or caller.can("hr_employees", "view")
    details = worker.details or {}
    out: dict[str, Any] = {
        "id": worker.id,
        "name": people.worker_name(worker),
        "first_name": worker.first_name,
        "last_name": worker.last_name,
        "preferred_name": worker.preferred_name,
        "position": worker.position,
        "company_id": worker.company_id,
        "active": worker.active,
        "status": _status(worker),
        "is_me": me,
        "photo_url": f"/api/workers/{worker.id}/photo?v={worker.photo_file_id}" if worker.photo_file_id else None,
        "source": ({"system": worker.source_system, "company": worker.source_company, "id": worker.source_id}
                   if worker.source_system else None),
        "archived_at": iso(worker.archived_at),
        "deleted_at": iso(worker.deleted_at),
        "updated_at": iso(worker.updated_at),
    }
    if team or me:
        out.update({"email": worker.email, "phone": worker.phone, "mobile": worker.mobile})
    if detail:
        user = _linked_user(db, worker)
        out["linked_user"] = (
            {"id": user.id, "name": user.name, "email": user.email if (team or me) else None,
             "status": user.status, "role_id": user.role_id} if user else None
        )
        out["can_see_personal"] = personal
        if personal:
            out.update({key: details.get(key) for key in PERSONAL_FIELDS})
            out.update({key: details.get(key) for key in OFFICE_FIELDS})
        if me or caller.can("hr_employees", "view"):
            # Masked hints only. They are stored beside the record so a listing never needs the key.
            hints = details.get("protected_hints") or {}
            out["protected"] = [
                {"field": f, "label": PROTECTED_LABELS[f], "recorded": f in hints, "masked": hints.get(f)}
                for f in PROTECTED_FIELDS
            ]
            out["can_reveal_protected"] = caller.can("hr_employees", "reveal_pii")
    return out


def _company_check(db: Session, caller: Caller, company_id: str | None) -> None:
    if company_id is None:
        return
    company = db.get(Company, company_id)
    if company is None or company.org_id != caller.org_id:
        raise HTTPException(404, "Company not found")


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, date):
        return value.isoformat()
    return value


def _apply_fields(db: Session, caller: Caller, worker: Worker, data: dict[str, Any]) -> list[str]:
    """Write the supplied fields. Returns the names that changed (never the protected values)."""
    changed: list[str] = []
    for key in ("first_name", "last_name", "preferred_name", "email", "phone", "mobile", "position", "company_id", "active"):
        if key not in data:
            continue
        value = data[key]
        if key == "email" and value is not None:
            value = str(value).strip().lower()
        elif key in ("first_name", "last_name"):
            value = (value or "").strip()
            if key == "first_name" and not value:
                raise HTTPException(422, "First name is required")
        elif key not in ("active", "company_id"):
            value = _clean(value)
        if key == "active" and value is None:
            continue
        if key == "company_id":
            _company_check(db, caller, value)
        if getattr(worker, key) != value:
            setattr(worker, key, value)
            changed.append(key)

    details = dict(worker.details or {})
    for key in (*PERSONAL_FIELDS, *OFFICE_FIELDS):
        if key in data:
            value = _clean(data[key])
            if details.get(key) != value:
                if value is None:
                    details.pop(key, None)
                else:
                    details[key] = value
                changed.append(key)

    touched = [f for f in PROTECTED_FIELDS if f in data]
    if touched:
        if not caller.can("hr_employees", "edit"):
            raise HTTPException(403, "Only people with HR edit access can change the date of birth, tax file number or USI")
        secret = decrypt_json(worker.protected)
        hints = dict(details.get("protected_hints") or {})
        for field in touched:
            value = _clean(data[field])
            if field == "tax_file_number" and value is not None:
                digits = re.sub(r"[\s-]", "", value)
                if not digits.isdigit() or len(digits) not in (8, 9):
                    raise HTTPException(422, "A tax file number has 8 or 9 digits")
                value = digits
            if secret.get(field) == value:
                continue
            if value is None:
                secret.pop(field, None)
                hints.pop(field, None)
            else:
                secret[field] = value
                hints[field] = _mask(field, value)
            changed.append(field)
        worker.protected = encrypt_json(secret) if secret else None
        if hints:
            details["protected_hints"] = hints
        else:
            details.pop("protected_hints", None)
    worker.details = details
    return changed


def _duplicates(db: Session, worker: Worker) -> list[dict[str, Any]]:
    """Other records that look like the same person. Shown for review; never merged automatically."""
    tests = [func.lower(Worker.first_name + " " + Worker.last_name) == people.worker_name(worker).lower()]
    if worker.email:
        tests.append(func.lower(Worker.email) == worker.email.lower())
    rows = db.scalars(
        select(Worker).where(Worker.org_id == worker.org_id, Worker.id != worker.id, Worker.deleted_at.is_(None), or_(*tests)).limit(10)
    ).all()
    return [{"id": w.id, "name": people.worker_name(w), "email": w.email, "status": _status(w),
             "match": "email" if worker.email and w.email and w.email.lower() == worker.email.lower() else "name"} for w in rows]


# ---------- workers: list, directory, own record ----------

@router.get("")
def list_workers(
    q: str | None = Query(None, max_length=100),
    status: Literal["active", "inactive", "archived", "all"] = "active",
    company_id: str | None = None,
    linked: Literal["yes", "no"] | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    caller: Caller = Depends(require_permission("workers", "view")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """The roster. Without team view a person sees only their own record."""
    stmt = select(Worker).where(Worker.org_id == caller.org_id, Worker.deleted_at.is_(None))
    if not caller.can("workers", "team_view"):
        stmt = stmt.where(Worker.id == (caller.user.worker_id or ""))
    base = stmt
    if status == "active":
        stmt = stmt.where(Worker.archived_at.is_(None), Worker.active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(Worker.archived_at.is_(None), Worker.active.is_(False))
    elif status == "archived":
        stmt = stmt.where(Worker.archived_at.is_not(None))
    if company_id:
        stmt = stmt.where(Worker.company_id == company_id)
    linked_ids = select(User.worker_id).where(User.worker_id.is_not(None), User.deleted_at.is_(None))
    if linked == "yes":
        stmt = stmt.where(Worker.id.in_(linked_ids))
    elif linked == "no":
        stmt = stmt.where(Worker.id.not_in(linked_ids))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(
            func.lower(Worker.first_name + " " + Worker.last_name).like(like),
            func.lower(func.coalesce(Worker.preferred_name, "")).like(like),
            func.lower(func.coalesce(Worker.email, "")).like(like),
            func.lower(func.coalesce(Worker.position, "")).like(like),
        ))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(func.lower(Worker.last_name), func.lower(Worker.first_name), Worker.id)
                      .limit(limit).offset(offset)).all()

    org = _org(db, caller)
    settings = people.cert_settings(org)
    today = people.today_for(org)
    with_login = set(db.scalars(select(User.worker_id).where(User.worker_id.in_([w.id for w in rows]), User.deleted_at.is_(None))).all())
    items = []
    for worker in rows:
        item = worker_out(db, caller, worker)
        counts = {"valid": 0, "expiring": 0, "expired": 0, "unknown": 0}
        for cert in people.current_certs(db, worker.id):
            counts[people.cert_status(cert, today, settings["expiry_window_days"])[0]] += 1
        item["certifications"] = {**counts, "missing": len(people.missing_expected(db, org, worker))}
        item["has_login"] = worker.id in with_login
        items.append(item)

    def count(*conds: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(base.where(*conds).subquery())) or 0)

    return {
        "items": items, "total": int(total), "limit": limit, "offset": offset,
        "counts": {
            "active": count(Worker.archived_at.is_(None), Worker.active.is_(True)),
            "inactive": count(Worker.archived_at.is_(None), Worker.active.is_(False)),
            "archived": count(Worker.archived_at.is_not(None)),
        },
    }


@router.post("", status_code=201)
def create_worker(
    body: WorkerIn,
    request: Request,
    caller: Caller = Depends(require_permission("workers", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Add a worker by hand. Workers imported from Simpro arrive through the sync instead (stage 5)."""
    worker = Worker(org_id=caller.org_id, first_name=body.first_name.strip(), created_by=caller.user.id)
    _apply_fields(db, caller, worker, body.model_dump(exclude_unset=True))
    db.add(worker)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.created",
          target_type="worker", target_id=worker.id, detail={"name": people.worker_name(worker)})
    db.commit()
    return {**worker_out(db, caller, worker, detail=True), "possible_duplicates": _duplicates(db, worker)}


@router.get("/directory")
def directory(
    q: str | None = Query(None, max_length=100),
    caller: Caller = Depends(get_caller),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Names only, for pickers. Open to anyone who can view workers or fill in forms."""
    if not (caller.can("workers", "view") or caller.can("forms", "view")):
        raise HTTPException(403, "You do not have permission to do this")
    stmt = select(Worker).where(Worker.org_id == caller.org_id, Worker.deleted_at.is_(None),
                                Worker.archived_at.is_(None), Worker.active.is_(True))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(Worker.first_name + " " + Worker.last_name).like(like))
    rows = db.scalars(stmt.order_by(func.lower(Worker.first_name), func.lower(Worker.last_name), Worker.id).limit(200)).all()
    return {"items": [{"id": w.id, "label": people.worker_name(w), "position": w.position} for w in rows]}


def _own(db: Session, caller: Caller) -> Worker:
    worker = people.worker_for_user(db, caller.user)
    if worker is None:
        raise HTTPException(404, "Your login is not linked to a worker record yet. Ask the office to link it.")
    return worker


@router.get("/me")
def my_record(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    worker = _own(db, caller)
    return {**worker_out(db, caller, worker, detail=True), "certifications": _certs_for(db, caller, worker)}


@router.patch("/me")
def edit_my_record(
    body: SelfEdit, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)
) -> dict[str, Any]:
    worker = _own(db, caller)
    changed = _apply_fields(db, caller, worker, body.model_dump(exclude_unset=True))
    if changed:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.self_updated",
              target_type="worker", target_id=worker.id, detail={"fields": changed})
    db.commit()
    return worker_out(db, caller, worker, detail=True)


# ---------- certifications: register-wide routes (declared before /{worker_id}) ----------

def _cert_out(db: Session, cert: Certification, *, worker: Worker | None, labels: dict[str, str],
              today: date, window: int) -> dict[str, Any]:
    state, days = people.cert_status(cert, today, window)
    evidence = db.get(StoredFile, cert.evidence_file_id) if cert.evidence_file_id else None
    return {
        "id": cert.id,
        "worker": {"id": worker.id, "name": people.worker_name(worker), "status": _status(worker)} if worker else {"id": cert.worker_id},
        "kind": {"slug": cert.kind_slug, "label": labels.get(cert.kind_slug, cert.kind_slug)},
        "number": cert.number,
        "issue_date": cert.issue_date.isoformat() if cert.issue_date else None,
        "expiry_date": cert.expiry_date.isoformat() if cert.expiry_date else None,
        "no_expiry": cert.no_expiry,
        "status": state,
        "status_label": people.STATUS_LABELS[state],
        "days_left": days,
        "notes": cert.notes,
        "evidence": file_out(evidence) if evidence else None,
        "evidence_url": f"/api/workers/{cert.worker_id}/certifications/{cert.id}/file" if evidence else None,
        "current": cert.superseded_by_id is None,
        "supersedes_id": cert.supersedes_id,
        "superseded_by_id": cert.superseded_by_id,
        "archived_at": iso(cert.archived_at),
        "created_at": iso(cert.created_at),
        "updated_at": iso(cert.updated_at),
    }


def _ctx(db: Session, caller: Caller) -> tuple[dict[str, str], date, int]:
    org = _org(db, caller)
    return people.kind_labels(db, org.id), people.today_for(org), people.cert_settings(org)["expiry_window_days"]


def _certs_for(db: Session, caller: Caller, worker: Worker, *, history: bool = False) -> list[dict[str, Any]]:
    labels, today, window = _ctx(db, caller)
    stmt = select(Certification).where(Certification.worker_id == worker.id, Certification.deleted_at.is_(None))
    if not history:
        stmt = stmt.where(Certification.superseded_by_id.is_(None), Certification.archived_at.is_(None))
    rows = db.scalars(stmt.order_by(Certification.kind_slug, Certification.created_at.desc())).all()
    return [_cert_out(db, c, worker=worker, labels=labels, today=today, window=window) for c in rows]


def _scoped_certs(db: Session, caller: Caller):
    """Current certificates of non-deleted workers that the caller may see."""
    stmt = (
        select(Certification, Worker).join(Worker, Worker.id == Certification.worker_id)
        .where(Certification.org_id == caller.org_id, Certification.deleted_at.is_(None), Worker.deleted_at.is_(None))
    )
    if not caller.can("workers", "team_view"):
        stmt = stmt.where(Worker.id == (caller.user.worker_id or ""))
    return stmt


@router.get("/certifications/all")
def all_certifications(
    status: Literal["valid", "expiring", "expired", "unknown"] | None = None,
    kind: str | None = None,
    worker_id: str | None = None,
    q: str | None = Query(None, max_length=100),
    include_archived: bool = False,
    include_inactive_workers: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    caller: Caller = Depends(require_permission("certifications", "view")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """The certification register. `summary` counts use exactly the same filters as the rows
    (apart from the status filter itself), so the totals always match what is listed."""
    labels, today, window = _ctx(db, caller)
    stmt = _scoped_certs(db, caller).where(Certification.superseded_by_id.is_(None))
    if not include_archived:
        stmt = stmt.where(Certification.archived_at.is_(None))
    if not include_inactive_workers:
        stmt = stmt.where(Worker.archived_at.is_(None), Worker.active.is_(True))
    if kind:
        stmt = stmt.where(Certification.kind_slug == kind)
    if worker_id:
        stmt = stmt.where(Certification.worker_id == worker_id)
    if q and q.strip():
        needle = q.strip().lower()
        slugs = [slug for slug, label in labels.items() if needle in label.lower()]
        like = f"%{needle}%"
        stmt = stmt.where(or_(
            func.lower(Worker.first_name + " " + Worker.last_name).like(like),
            func.lower(func.coalesce(Certification.number, "")).like(like),
            Certification.kind_slug.in_(slugs),
        ))
    pairs = db.execute(stmt).all()
    rows = [(_cert_out(db, c, worker=w, labels=labels, today=today, window=window)) for c, w in pairs]
    summary = {"valid": 0, "expiring": 0, "expired": 0, "unknown": 0}
    for row in rows:
        summary[row["status"]] += 1
    if status:
        rows = [r for r in rows if r["status"] == status]
    order = {"expired": 0, "expiring": 1, "unknown": 2, "valid": 3}
    rows.sort(key=lambda r: (order[r["status"]], r["expiry_date"] or "9999", r["worker"]["name"].lower()))
    return {"items": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset,
            "summary": summary, "window_days": window}


@router.post("/certifications/{cert_id}/renew", status_code=201)
def renew_certification(
    cert_id: str,
    body: RenewIn,
    request: Request,
    caller: Caller = Depends(require_permission("certifications", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Record a renewal. The old certificate and its evidence are kept as history."""
    old, worker = _get_cert(db, caller, cert_id)
    if old.superseded_by_id is not None:
        raise HTTPException(409, "This certificate has already been renewed. Renew the current one instead.")
    _check_dates(body.issue_date, body.expiry_date, body.no_expiry)
    new = Certification(
        org_id=caller.org_id, worker_id=worker.id, kind_slug=old.kind_slug, number=_clean(body.number) or old.number,
        issue_date=body.issue_date, expiry_date=body.expiry_date, no_expiry=body.no_expiry, notes=_clean(body.notes),
        supersedes_id=old.id, created_by=caller.user.id,
    )
    db.add(new)
    db.flush()
    old.superseded_by_id = new.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="certification.renewed",
          target_type="worker", target_id=worker.id, detail={"certification_id": new.id, "replaces": old.id, "kind": old.kind_slug})
    db.commit()
    labels, today, window = _ctx(db, caller)
    return _cert_out(db, new, worker=worker, labels=labels, today=today, window=window)


@router.patch("/certifications/{cert_id}")
def update_certification(
    cert_id: str,
    body: CertPatch,
    request: Request,
    caller: Caller = Depends(require_permission("certifications", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    cert, worker = _get_cert(db, caller, cert_id)
    data = body.model_dump(exclude_unset=True)
    issue = data.get("issue_date", cert.issue_date)
    expiry = data.get("expiry_date", cert.expiry_date)
    never = data["no_expiry"] if data.get("no_expiry") is not None else cert.no_expiry
    _check_dates(issue, expiry, never)
    changed = []
    for key, value in (("number", _clean(data.get("number")) if "number" in data else cert.number),
                       ("issue_date", issue), ("expiry_date", expiry), ("no_expiry", never),
                       ("notes", _clean(data.get("notes")) if "notes" in data else cert.notes)):
        if getattr(cert, key) != value:
            setattr(cert, key, value)
            changed.append(key)
    if changed:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="certification.updated",
              target_type="worker", target_id=worker.id, detail={"certification_id": cert.id, "fields": changed})
    db.commit()
    labels, today, window = _ctx(db, caller)
    return _cert_out(db, cert, worker=worker, labels=labels, today=today, window=window)


@router.delete("/certifications/{cert_id}")
def delete_certification(
    cert_id: str,
    request: Request,
    caller: Caller = Depends(require_permission("certifications", "delete")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Remove a certificate entered by mistake. If it replaced an earlier one, that one becomes current again."""
    cert, worker = _get_cert(db, caller, cert_id)
    if cert.superseded_by_id is not None:
        raise HTTPException(409, "This certificate has a renewal recorded after it. Delete the renewal first.")
    cert.deleted_at = utcnow()
    if cert.supersedes_id:
        previous = db.get(Certification, cert.supersedes_id)
        if previous is not None:
            previous.superseded_by_id = None
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="certification.deleted",
          target_type="worker", target_id=worker.id, detail={"certification_id": cert.id, "kind": cert.kind_slug})
    db.commit()
    return {"ok": True}


@router.post("/certifications/{cert_id}/archive")
def archive_certification(
    cert_id: str, request: Request,
    caller: Caller = Depends(require_permission("certifications", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    cert, worker = _get_cert(db, caller, cert_id)
    if cert.archived_at is None:
        cert.archived_at = utcnow()
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="certification.archived",
              target_type="worker", target_id=worker.id, detail={"certification_id": cert.id})
        db.commit()
    return {"ok": True, "archived_at": iso(cert.archived_at)}


@router.post("/certifications/{cert_id}/restore")
def restore_certification(
    cert_id: str, request: Request,
    caller: Caller = Depends(require_permission("certifications", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    cert, worker = _get_cert(db, caller, cert_id)
    if cert.archived_at is not None:
        cert.archived_at = None
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="certification.restored",
              target_type="worker", target_id=worker.id, detail={"certification_id": cert.id})
        db.commit()
    return {"ok": True}


def _get_cert(db: Session, caller: Caller, cert_id: str) -> tuple[Certification, Worker]:
    cert = db.get(Certification, cert_id)
    if cert is None or cert.org_id != caller.org_id or cert.deleted_at is not None:
        raise HTTPException(404, "Certificate not found")
    try:
        worker = _get_worker(db, caller, cert.worker_id)
    except HTTPException:
        raise HTTPException(404, "Certificate not found") from None
    return cert, worker


def _check_dates(issue: date | None, expiry: date | None, never: bool) -> None:
    if never and expiry is not None:
        raise HTTPException(422, "Leave the expiry date empty for a certificate that does not expire")
    if issue is not None and expiry is not None and expiry < issue:
        raise HTTPException(422, "The expiry date is before the issue date")


# ---------- one worker ----------

@router.get("/{worker_id}")
def get_worker(
    worker_id: str, caller: Caller = Depends(require_permission("workers", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    worker = _get_worker(db, caller, worker_id)
    org = _org(db, caller)
    labels = people.kind_labels(db, org.id)
    return {
        **worker_out(db, caller, worker, detail=True),
        "missing_certifications": [{"slug": s, "label": labels.get(s, s)} for s in people.missing_expected(db, org, worker)],
        "possible_duplicates": _duplicates(db, worker) if caller.can("workers", "edit") else [],
    }


@router.patch("/{worker_id}")
def update_worker(
    worker_id: str,
    body: WorkerPatch,
    request: Request,
    caller: Caller = Depends(require_permission("workers", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    worker = _get_worker(db, caller, worker_id)
    changed = _apply_fields(db, caller, worker, body.model_dump(exclude_unset=True))
    if changed:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.updated",
              target_type="worker", target_id=worker.id, detail={"fields": changed})
    db.commit()
    return worker_out(db, caller, worker, detail=True)


@router.post("/{worker_id}/archive")
def archive_worker(
    worker_id: str, request: Request,
    caller: Caller = Depends(require_permission("workers", "archive")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Archive keeps the record and its history but takes the person off the roster and pickers."""
    worker = _get_worker(db, caller, worker_id)
    if worker.archived_at is None:
        worker.archived_at = utcnow()
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.archived",
              target_type="worker", target_id=worker.id)
        db.commit()
    return worker_out(db, caller, worker, detail=True)


@router.post("/{worker_id}/unarchive")
def unarchive_worker(
    worker_id: str, request: Request,
    caller: Caller = Depends(require_permission("workers", "archive")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    worker = _get_worker(db, caller, worker_id)
    if worker.archived_at is not None:
        worker.archived_at = None
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.unarchived",
              target_type="worker", target_id=worker.id)
        db.commit()
    return worker_out(db, caller, worker, detail=True)


@router.delete("/{worker_id}")
def delete_worker(
    worker_id: str, request: Request,
    caller: Caller = Depends(require_permission("workers", "delete")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Soft delete. The login (if any) is unlinked but not removed; restore brings the record back."""
    worker = _get_worker(db, caller, worker_id)
    worker.deleted_at = utcnow()
    user = _linked_user(db, worker)
    if user is not None:
        user.worker_id = None
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.deleted",
          target_type="worker", target_id=worker.id, detail={"unlinked_user_id": user.id if user else None})
    db.commit()
    return {"ok": True}


@router.post("/{worker_id}/restore")
def restore_worker(
    worker_id: str, request: Request,
    caller: Caller = Depends(require_permission("workers", "delete")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    worker = _get_worker(db, caller, worker_id, include_deleted=True)
    if worker.deleted_at is not None:
        worker.deleted_at = None
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.restored",
              target_type="worker", target_id=worker.id)
        db.commit()
    return worker_out(db, caller, worker, detail=True)


@router.post("/{worker_id}/reveal")
def reveal_protected(
    worker_id: str, request: Request,
    caller: Caller = Depends(require_permission("hr_employees", "reveal_pii")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Show the protected values in full. Every reveal is written to the audit trail (never the values)."""
    worker = _get_worker(db, caller, worker_id)
    secret = decrypt_json(worker.protected)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.protected_revealed",
          target_type="worker", target_id=worker.id, detail={"fields": sorted(secret)})
    db.commit()
    return {"fields": [{"field": f, "label": PROTECTED_LABELS[f], "value": secret.get(f)} for f in PROTECTED_FIELDS]}


@router.get("/{worker_id}/history")
def worker_history(
    worker_id: str, caller: Caller = Depends(require_permission("workers", "audit_view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    worker = _get_worker(db, caller, worker_id, include_deleted=True)
    rows = db.scalars(
        select(AuditLog).where(AuditLog.org_id == caller.org_id, AuditLog.target_type == "worker", AuditLog.target_id == worker.id)
        .order_by(AuditLog.created_at.desc()).limit(200)
    ).all()
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_({r.actor_user_id for r in rows if r.actor_user_id}))).all()}
    return {"items": [{"id": r.id, "action": r.action, "at": iso(r.created_at),
                       "actor": names.get(r.actor_user_id, "System") if r.actor_user_id else "System",
                       "detail": r.detail or {}} for r in rows]}


# ---------- login link ----------

@router.post("/{worker_id}/link-user")
def link_user(
    worker_id: str, body: LinkUserIn, request: Request,
    caller: Caller = Depends(require_permission("users", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    worker = _get_worker(db, caller, worker_id)
    user = db.get(User, body.user_id)
    if user is None or user.org_id != caller.org_id or user.deleted_at is not None:
        raise HTTPException(404, "User not found")
    existing = _linked_user(db, worker)
    if existing is not None and existing.id != user.id:
        raise HTTPException(409, f"This worker is already linked to the login for {existing.name}. Unlink it first.")
    if user.worker_id and user.worker_id != worker.id:
        raise HTTPException(409, "That login is already linked to a different worker. Unlink it first.")
    user.worker_id = worker.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.user_linked",
          target_type="worker", target_id=worker.id, detail={"user_id": user.id})
    db.commit()
    return worker_out(db, caller, worker, detail=True)


@router.delete("/{worker_id}/link-user")
def unlink_user(
    worker_id: str, request: Request,
    caller: Caller = Depends(require_permission("users", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    worker = _get_worker(db, caller, worker_id)
    user = _linked_user(db, worker)
    if user is not None:
        user.worker_id = None
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.user_unlinked",
              target_type="worker", target_id=worker.id, detail={"user_id": user.id})
        db.commit()
    return worker_out(db, caller, worker, detail=True)


@router.post("/{worker_id}/provision-user", status_code=201)
def provision_user(
    worker_id: str, body: ProvisionIn, request: Request,
    caller: Caller = Depends(require_permission("users", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a login for this worker and an invitation link. Nothing is emailed; the link is shown once."""
    worker = _get_worker(db, caller, worker_id)
    if _linked_user(db, worker) is not None:
        raise HTTPException(409, "This worker already has a login")
    email = (str(body.email) if body.email else worker.email or "").strip().lower()
    if not email:
        raise HTTPException(422, "Add an email address for this worker first. It becomes their sign-in name.")
    if db.scalars(select(User.id).where(User.email == email)).first():
        raise HTTPException(409, "A login with this email already exists. Link that login to the worker instead.")
    role = require_assignable_role(db, caller.org_id, body.role_id)
    if role.role_id == "admin" and not caller.is_admin:
        raise HTTPException(403, "Only an admin can grant or remove the admin role")
    ensure_can_grant(caller, role.permissions)
    user = User(org_id=caller.org_id, email=email, name=people.worker_name(worker), role_id=role.role_id,
                company_id=worker.company_id, status="invited", worker_id=worker.id)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This worker already has a login") from None
    row, token = issue_auth_token(db, user, "invite", created_by=caller.user.id)
    link = link_for(request, "/accept-invite", token)
    org_name = _org(db, caller).name
    message = hold_message(db, org_id=caller.org_id, channel="email", purpose="invitation", recipient=user.email,
                           subject=f"Your {org_name} Safety Portal invitation", related_type="auth_token", related_id=row.id,
                           created_by=caller.user.id, request=request, store_body=False,
                           body=f"Hello {user.name},\n\nYou have been invited to the {org_name} Safety Portal. Choose your password here:\n{link}\n\nThis link works once.")
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.user_provisioned",
          target_type="worker", target_id=worker.id, detail={"user_id": user.id, "role_id": role.role_id})
    db.commit()
    return {
        "worker": worker_out(db, caller, worker, detail=True),
        "user_id": user.id,
        "delivery": message.status,
        "delivery_note": comms.link_note(message),
        "expires_at": iso(row.expires_at),
        "link": link,
    }


# ---------- photo ----------

def _may_edit(caller: Caller, worker: Worker) -> None:
    if not (caller.can("workers", "edit") or _is_self(caller, worker)):
        raise HTTPException(403, "You do not have permission to do this")


@router.post("/{worker_id}/photo")
def upload_photo(
    worker_id: str, request: Request, file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("workers", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    worker = _get_worker(db, caller, worker_id)
    _may_edit(caller, worker)
    stored = save_upload(db, org_id=caller.org_id, upload=file, purpose="worker_photo", created_by=caller.user.id,
                         max_bytes=PHOTO_MAX_BYTES, allowed_mimes=IMAGE_TYPES)
    worker.photo_file_id = stored.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.photo_changed",
          target_type="worker", target_id=worker.id)
    db.commit()
    return worker_out(db, caller, worker, detail=True)


@router.delete("/{worker_id}/photo")
def delete_photo(
    worker_id: str, request: Request,
    caller: Caller = Depends(require_permission("workers", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    worker = _get_worker(db, caller, worker_id)
    _may_edit(caller, worker)
    if worker.photo_file_id:
        worker.photo_file_id = None
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="worker.photo_removed",
              target_type="worker", target_id=worker.id)
        db.commit()
    return worker_out(db, caller, worker, detail=True)


@router.get("/{worker_id}/photo")
def get_photo(
    worker_id: str, caller: Caller = Depends(require_permission("workers", "view")), db: Session = Depends(get_db),
):
    worker = _get_worker(db, caller, worker_id)
    stored = db.get(StoredFile, worker.photo_file_id) if worker.photo_file_id else None
    if stored is None or stored.org_id != caller.org_id:
        raise HTTPException(404, "No photo")
    return file_response(stored, inline=True)


# ---------- a worker's certifications ----------

@router.get("/{worker_id}/certifications")
def list_certifications(
    worker_id: str, history: bool = False,
    caller: Caller = Depends(require_permission("certifications", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    worker = _get_worker(db, caller, worker_id)
    org = _org(db, caller)
    labels = people.kind_labels(db, org.id)
    return {
        "items": _certs_for(db, caller, worker, history=history),
        "missing": [{"slug": s, "label": labels.get(s, s)} for s in people.missing_expected(db, org, worker)],
    }


@router.post("/{worker_id}/certifications", status_code=201)
def add_certification(
    worker_id: str, body: CertIn, request: Request,
    caller: Caller = Depends(require_permission("certifications", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    worker = _get_worker(db, caller, worker_id)
    kind = people.resolve_kind(db, caller.org_id, body.kind)
    if kind is None or not kind.active:
        raise HTTPException(422, "Unknown certification type. Choose one from the list, or add it under certification types first.")
    _check_dates(body.issue_date, body.expiry_date, body.no_expiry)
    if any(c.kind_slug == kind.slug for c in people.current_certs(db, worker.id)):
        raise HTTPException(409, f"{people.worker_name(worker)} already has a current {kind.label} certificate. Renew that one instead.")
    cert = Certification(org_id=caller.org_id, worker_id=worker.id, kind_slug=kind.slug, number=_clean(body.number),
                         issue_date=body.issue_date, expiry_date=body.expiry_date, no_expiry=body.no_expiry,
                         notes=_clean(body.notes), created_by=caller.user.id)
    db.add(cert)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="certification.added",
          target_type="worker", target_id=worker.id, detail={"certification_id": cert.id, "kind": kind.slug})
    db.commit()
    labels, today, window = _ctx(db, caller)
    return _cert_out(db, cert, worker=worker, labels=labels, today=today, window=window)


@router.post("/{worker_id}/certifications/{cert_id}/upload")
def upload_evidence(
    worker_id: str, cert_id: str, request: Request, file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("certifications", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Attach the certificate itself (PDF or photo). Replacing it is recorded in the worker's history."""
    cert, worker = _get_cert(db, caller, cert_id)
    if worker.id != worker_id:
        raise HTTPException(404, "Certificate not found")
    stored = save_upload(db, org_id=caller.org_id, upload=file, purpose="certification_evidence",
                         created_by=caller.user.id, allowed_mimes=EVIDENCE_TYPES)
    replaced = cert.evidence_file_id
    cert.evidence_file_id = stored.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="certification.evidence_uploaded",
          target_type="worker", target_id=worker.id,
          detail={"certification_id": cert.id, "file_id": stored.id, "replaced_file_id": replaced})
    db.commit()
    labels, today, window = _ctx(db, caller)
    return _cert_out(db, cert, worker=worker, labels=labels, today=today, window=window)


@router.get("/{worker_id}/certifications/{cert_id}/file")
def evidence_file(
    worker_id: str, cert_id: str, download: bool = False,
    caller: Caller = Depends(require_permission("certifications", "view")), db: Session = Depends(get_db),
):
    cert, worker = _get_cert(db, caller, cert_id)
    if worker.id != worker_id:
        raise HTTPException(404, "Certificate not found")
    stored = db.get(StoredFile, cert.evidence_file_id) if cert.evidence_file_id else None
    if stored is None or stored.org_id != caller.org_id:
        raise HTTPException(404, "No file is attached to this certificate")
    return file_response(stored, inline=not download)


# ---------- certification totals and settings ----------

@cert_router.get("/expiry-count")
def expiry_count(
    caller: Caller = Depends(require_permission("certifications", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Badge numbers. Same population as the register's default view: current certificates of active workers."""
    _, today, window = _ctx(db, caller)
    stmt = _scoped_certs(db, caller).where(
        Certification.superseded_by_id.is_(None), Certification.archived_at.is_(None),
        Worker.archived_at.is_(None), Worker.active.is_(True),
    )
    counts = {"expired": 0, "expiring": 0, "unknown": 0, "valid": 0}
    for cert, _worker in db.execute(stmt).all():
        counts[people.cert_status(cert, today, window)[0]] += 1
    return {"expired": counts["expired"], "expiring_soon": counts["expiring"], "unknown": counts["unknown"], "window_days": window}


@cert_router.get("/settings")
def get_cert_settings(
    caller: Caller = Depends(require_permission("certifications", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return {**people.cert_settings(_org(db, caller)), "encryption": key_status() if caller.is_admin else None}


@cert_router.put("/settings")
def put_cert_settings(
    body: CertSettingsIn, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Warning window, the certificates every worker is expected to hold, and the organisation-wide
    half of the form gate. A form is only gated when its own switch is on as well."""
    org = _org(db, caller)
    known = {k.slug for k in people.ensure_kinds(db, org.id)}
    unknown = [s for s in body.expected_kinds if s not in known]
    if unknown:
        raise HTTPException(422, f"Unknown certification type: {', '.join(unknown)}")
    before = people.cert_settings(org)
    after = {"expiry_window_days": body.expiry_window_days, "expected_kinds": list(dict.fromkeys(body.expected_kinds)),
             "gate_enabled": body.gate_enabled}
    org.details = {**(org.details or {}), "certifications": after}
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="certifications.settings_changed",
          target_type="organisation", target_id=org.id, detail={"before": before, "after": after})
    db.commit()
    return after


# ---------- certification types ----------

def _kind_out(kind: CertificationKind) -> dict[str, Any]:
    return {"id": kind.id, "slug": kind.slug, "label": kind.label, "aliases": list(kind.aliases or []),
            "is_system": kind.is_system, "active": kind.active, "category": kind.category}


@kinds_router.get("")
def list_kinds(
    include_inactive: bool = False,
    caller: Caller = Depends(get_caller), db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not (caller.can("certifications", "view") or caller.can("forms", "view")):
        raise HTTPException(403, "You do not have permission to do this")
    rows = people.ensure_kinds(db, caller.org_id)
    db.commit()
    return {"items": [_kind_out(k) for k in rows if include_inactive or k.active]}


def _aliases(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip().lower() for v in values if v and v.strip()))[:30]


@kinds_router.post("", status_code=201)
def create_kind(
    body: KindIn, request: Request,
    caller: Caller = Depends(require_permission("certifications", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    if people.resolve_kind(db, caller.org_id, body.label) is not None:
        raise HTTPException(409, "A certification type with this name or alias already exists")
    kind = CertificationKind(org_id=caller.org_id, slug=people.new_kind_slug(db, caller.org_id, body.label),
                             label=body.label.strip(), aliases=_aliases(body.aliases))
    db.add(kind)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="certification_kind.created",
          target_type="certification_kind", target_id=kind.id, detail={"slug": kind.slug})
    db.commit()
    return _kind_out(kind)


@kinds_router.patch("/{kind_id}")
def update_kind(
    kind_id: str, body: KindPatch, request: Request,
    caller: Caller = Depends(require_permission("certifications", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Rename, add aliases or retire a type. The slug never changes, so old certificates keep their type."""
    kind = db.get(CertificationKind, kind_id)
    if kind is None or kind.org_id != caller.org_id:
        raise HTTPException(404, "Certification type not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("label"):
        other = people.resolve_kind(db, caller.org_id, data["label"])
        if other is not None and other.id != kind.id:
            raise HTTPException(409, "Another certification type already uses this name")
        kind.label = data["label"].strip()
    if data.get("aliases") is not None:
        kind.aliases = _aliases(data["aliases"])
    if data.get("active") is not None:
        kind.active = data["active"]
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="certification_kind.updated",
          target_type="certification_kind", target_id=kind.id, detail={"fields": sorted(data)})
    db.commit()
    return _kind_out(kind)
