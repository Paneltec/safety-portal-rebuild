"""The activity log and audit packs (stage 9b).

Activity log: who changed what and when, read-only, for administrators and people given the
audit permission. Payroll and timesheet entries are only shown to people with payroll access.

Audit pack: a ZIP of records for an outside auditor, with a manifest listing every file in it and
its SHA-256. A pack is whole-organisation, so it needs organisation-wide view of each register it
includes; a person limited to their own records cannot build one from that register.
"""
import csv
import hashlib
import io
import json
import zipfile
from datetime import date
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import people
from .. import safety as safety_kinds
from ..config import get_settings
from ..db import get_db
from ..deps import Caller, get_caller, require_permission
from ..files import file_exists, file_response, save_bytes
from ..models import (AuditExport, AuditLog, Certification, FormSubmission, FormTemplateVersion, Organisation, SafetyRecord, Site, SiteAttendance, StoredFile, User,
                      Worker, utcnow)
from ..permissions import TEAM_SCOPED_RESOURCES
from ..services import audit, iso
from .forms import _can_see_all as forms_team
from .sites import _day_range

router = APIRouter(tags=["audit"])

MAX_ROWS = 20000  # per file; more than this must be narrowed by date
PAYROLL_PREFIXES = ("payroll.", "timesheet.")
HR_PREFIXES = ("hr_document.",)


def _cell(value: Any) -> str:
    """Text for a spreadsheet cell. A cell must never be run as a formula when the file is opened."""
    text = "" if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) if isinstance(value, (dict, list)) else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv_bytes(header: list[str], rows: list[list[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows([[_cell(v) for v in row] for row in rows])
    return ("﻿" + buffer.getvalue()).encode("utf-8")  # the mark makes Excel read accents correctly


# ---------- activity log ----------

def _log_reader(caller: Caller = Depends(get_caller)) -> Caller:
    if not (caller.is_admin or caller.can("audit_exports", "audit_view")):
        raise HTTPException(403, "You do not have permission to do this")
    return caller


def _log_stmt(caller: Caller, org: Organisation, *, action: str | None, target_type: str | None, target_id: str | None, actor_id: str | None,
              date_from: date | None, date_to: date | None):
    stmt = select(AuditLog).where(AuditLog.org_id == caller.org_id)
    if not caller.can("payroll", "view"):
        stmt = stmt.where(*[~AuditLog.action.like(f"{p}%") for p in PAYROLL_PREFIXES])
    if not caller.can("hr_employees", "view"):
        stmt = stmt.where(*[~AuditLog.action.like(f"{p}%") for p in HR_PREFIXES])
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action.strip().lower(), autoescape=True))
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == target_id)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_user_id == actor_id)
    start, end = _day_range(org, date_from, date_to)
    if start is not None:
        stmt = stmt.where(AuditLog.created_at >= start)
    if end is not None:
        stmt = stmt.where(AuditLog.created_at < end)
    return stmt


def _names(db: Session, org_id: str, ids: set[str | None]) -> dict[str, str]:
    wanted = {i for i in ids if i}
    return {u.id: u.name for u in db.scalars(select(User).where(User.org_id == org_id, User.id.in_(wanted))).all()} if wanted else {}


def _entry(row: AuditLog, names: dict[str, str]) -> dict[str, Any]:
    return {"id": row.id, "at": iso(row.created_at), "actor_id": row.actor_user_id,
            "who": names.get(row.actor_user_id or "", "The portal" if row.actor_user_id is None else "Someone no longer listed"),
            "action": row.action, "target_type": row.target_type, "target_id": row.target_id, "detail": row.detail or {}, "request_id": row.request_id}


@router.get("/api/audit-log")
def activity_log(action: str | None = Query(None, max_length=80), target_type: str | None = Query(None, max_length=40), target_id: str | None = Query(None, max_length=80),
                 actor_id: str | None = Query(None, max_length=36), date_from: date | None = None, date_to: date | None = None,
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), caller: Caller = Depends(_log_reader), db: Session = Depends(get_db)) -> dict[str, Any]:
    org = db.get(Organisation, caller.org_id)
    if date_from and date_to and date_to < date_from:
        raise HTTPException(422, "The end date is before the start date")
    stmt = _log_stmt(caller, org, action=action, target_type=target_type, target_id=target_id, actor_id=actor_id, date_from=date_from, date_to=date_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditLog.created_at.desc(), AuditLog.id).limit(limit).offset(offset)).all()
    names = _names(db, caller.org_id, {r.actor_user_id for r in rows})
    kinds = [t for (t,) in db.execute(select(AuditLog.target_type).where(AuditLog.org_id == caller.org_id).distinct().order_by(AuditLog.target_type)).all()]
    hidden = [label for label, shown in (("pay and timesheet entries", caller.can("payroll", "view")), ("HR document entries", caller.can("hr_employees", "view"))) if not shown]
    return {"items": [_entry(r, names) for r in rows], "total": int(total), "limit": limit, "offset": offset, "target_types": kinds, "hidden": hidden,
            "note": "Entries are written by the portal as things happen. They cannot be edited or removed here."}


@router.get("/api/audit-log/export.csv")
def activity_log_csv(request: Request, action: str | None = Query(None, max_length=80), target_type: str | None = Query(None, max_length=40),
                     target_id: str | None = Query(None, max_length=80), actor_id: str | None = Query(None, max_length=36), date_from: date | None = None,
                     date_to: date | None = None, caller: Caller = Depends(_log_reader), db: Session = Depends(get_db)) -> Response:
    org = db.get(Organisation, caller.org_id)
    stmt = _log_stmt(caller, org, action=action, target_type=target_type, target_id=target_id, actor_id=actor_id, date_from=date_from, date_to=date_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if total > MAX_ROWS:
        raise HTTPException(422, f"That is {total} entries. A file holds at most {MAX_ROWS}: choose a shorter date range.")
    rows = db.scalars(stmt.order_by(AuditLog.created_at, AuditLog.id)).all()
    names = _names(db, caller.org_id, {r.actor_user_id for r in rows})
    data = _csv_bytes(["When (UTC)", "Who", "Action", "Record type", "Record id", "Detail", "Request id"],
                      [[iso(r.created_at), _entry(r, names)["who"], r.action, r.target_type, r.target_id, r.detail or {}, r.request_id] for r in rows])
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="audit_log.exported", target_type="organisation", target_id=caller.org_id,
          detail={"entries": len(rows), "date_from": date_from.isoformat() if date_from else None, "date_to": date_to.isoformat() if date_to else None})
    db.commit()
    return Response(data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="activity-log.csv"'})


# ---------- audit packs ----------

def _whole_org(caller: Caller, resource: str) -> bool:
    return caller.can(resource, "view") and (resource not in TEAM_SCOPED_RESOURCES or caller.can(resource, "team_view"))


def _registers(caller: Caller) -> dict[str, dict[str, Any]]:
    """Every register a pack can hold: its label and whether this caller may include it."""
    out: dict[str, dict[str, Any]] = {}
    for kind in safety_kinds.KINDS.values():
        out[kind.key] = {"label": kind.plural, "allowed": _whole_org(caller, kind.resource), "dated_by": "the record's date"}
    out["certifications"] = {"label": "Certifications", "allowed": caller.can("certifications", "view") and caller.can("workers", "team_view"),
                             "dated_by": "nothing: every current certificate is included, with its state on the day the pack is made"}
    out["site_attendance"] = {"label": "Site sign-on and visitor log", "allowed": caller.can("sites_visitors", "view") and caller.can("sites", "view"), "dated_by": "the sign-in time"}
    out["form_submissions"] = {"label": "Submitted forms", "allowed": caller.can("forms", "view") and forms_team(caller), "dated_by": "the time the form was submitted"}
    out["activity_log"] = {"label": "Activity log", "allowed": caller.is_admin or caller.can("audit_exports", "audit_view"), "dated_by": "the time of the entry"}
    return out


def _collect(db: Session, caller: Caller, org: Organisation, key: str, date_from: date | None, date_to: date | None) -> tuple[list[str], list[list[Any]]]:
    """Header and rows for one register, organisation-scoped. Raises 422 when there are too many rows for one file."""
    start, end = _day_range(org, date_from, date_to)

    def guard(stmt) -> None:
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        if total > MAX_ROWS:
            raise HTTPException(422, f"{_registers(caller)[key]['label']}: {total} records in that period. A pack holds at most {MAX_ROWS} per register: choose a shorter date range.")

    if key in safety_kinds.KINDS:
        stmt = select(SafetyRecord).where(SafetyRecord.org_id == org.id, SafetyRecord.kind == key, SafetyRecord.deleted_at.is_(None))
        if date_from:
            stmt = stmt.where(or_(SafetyRecord.record_date >= date_from, (SafetyRecord.record_date.is_(None)) & (SafetyRecord.created_at >= start)))
        if date_to:
            stmt = stmt.where(or_(SafetyRecord.record_date <= date_to, (SafetyRecord.record_date.is_(None)) & (SafetyRecord.created_at < end)))
        guard(stmt)
        rows = db.scalars(stmt.order_by(SafetyRecord.record_date, SafetyRecord.created_at, SafetyRecord.id)).all()
        names = _names(db, org.id, {r.created_by for r in rows})
        return (["Id", "Title", "Status", "Record date", "Version", "Revision", "Created by", "Created (UTC)", "Submitted (UTC)", "Archived (UTC)", "Superseded by", "Answers"],
                [[r.id, r.title, safety_kinds.STATUS_LABELS.get(r.status, r.status), r.record_date, r.version_no, r.revision, names.get(r.created_by or "", ""), iso(r.created_at),
                  iso(r.submitted_at), iso(r.archived_at), r.superseded_by_id, r.values or {}] for r in rows])
    if key == "certifications":
        today, window = people.today_for(org), people.cert_settings(org)["expiry_window_days"]
        labels = people.kind_labels(db, org.id)
        stmt = (select(Certification, Worker).join(Worker, Worker.id == Certification.worker_id)
                .where(Certification.org_id == org.id, Certification.deleted_at.is_(None), Certification.superseded_by_id.is_(None), Certification.archived_at.is_(None),
                       Worker.deleted_at.is_(None)))
        guard(stmt)
        pairs = db.execute(stmt.order_by(Worker.last_name, Worker.first_name, Certification.kind_slug, Certification.id)).all()
        return (["Worker", "Worker active", "Certificate", "Number", "Issued", "Expires", "Never expires", "State on the day of this pack", "Evidence on file"],
                [[people.worker_name(w), "yes" if w.active and w.archived_at is None else "no", labels.get(c.kind_slug, c.kind_slug), c.number, c.issue_date, c.expiry_date,
                  "yes" if c.no_expiry else "no", people.STATUS_LABELS.get(people.cert_status(c, today, window)[0], people.cert_status(c, today, window)[0]),
                  "yes" if c.evidence_file_id else "no"] for c, w in pairs])
    if key == "site_attendance":
        stmt = select(SiteAttendance).where(SiteAttendance.org_id == org.id, SiteAttendance.deleted_at.is_(None))
        if start is not None:
            stmt = stmt.where(SiteAttendance.signed_in_at >= start)
        if end is not None:
            stmt = stmt.where(SiteAttendance.signed_in_at < end)
        guard(stmt)
        rows = db.scalars(stmt.order_by(SiteAttendance.signed_in_at, SiteAttendance.id)).all()
        site_names = {s.id: s.name for s in db.scalars(select(Site).where(Site.org_id == org.id, Site.id.in_({r.site_id for r in rows}))).all()} if rows else {}
        return (["Site", "Who", "Type", "Company", "Purpose", "Host", "Signed in (UTC)", "Signed out (UTC)", "How they signed out", "Declaration acknowledged", "Answers"],
                [[site_names.get(r.site_id, ""), r.name, "Visitor" if r.kind == "visitor" else "Worker", r.company, r.purpose, r.host, iso(r.signed_in_at), iso(r.signed_out_at),
                  r.sign_out_source, "yes" if r.declaration_acknowledged else "no", r.answers or {}] for r in rows])
    if key == "form_submissions":
        stmt = select(FormSubmission).where(FormSubmission.org_id == org.id, FormSubmission.deleted_at.is_(None), FormSubmission.status == "submitted")
        if start is not None:
            stmt = stmt.where(FormSubmission.submitted_at >= start)
        if end is not None:
            stmt = stmt.where(FormSubmission.submitted_at < end)
        guard(stmt)
        rows = db.scalars(stmt.order_by(FormSubmission.submitted_at, FormSubmission.id)).all()
        versions = {v.id: v for v in db.scalars(select(FormTemplateVersion).where(FormTemplateVersion.id.in_({r.template_version_id for r in rows}))).all()} if rows else {}
        names = _names(db, org.id, {r.submitted_by for r in rows})
        return (["Id", "Form", "Form version", "Category", "Submitted by", "Submitted (UTC)", "Answers"],
                [[r.id, getattr(versions.get(r.template_version_id), "name", ""), getattr(versions.get(r.template_version_id), "version", ""),
                  getattr(versions.get(r.template_version_id), "category", ""), names.get(r.submitted_by or "", ""), iso(r.submitted_at), r.values or {}] for r in rows])
    if key == "activity_log":
        stmt = _log_stmt(caller, org, action=None, target_type=None, target_id=None, actor_id=None, date_from=date_from, date_to=date_to)
        guard(stmt)
        rows = db.scalars(stmt.order_by(AuditLog.created_at, AuditLog.id)).all()
        names = _names(db, org.id, {r.actor_user_id for r in rows})
        return (["When (UTC)", "Who", "Action", "Record type", "Record id", "Detail", "Request id"],
                [[iso(r.created_at), _entry(r, names)["who"], r.action, r.target_type, r.target_id, r.detail or {}, r.request_id] for r in rows])
    raise HTTPException(422, f"Unknown register: {key}")


class PackIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    registers: list[str] = Field(min_length=1, max_length=20)
    date_from: date | None = None
    date_to: date | None = None


def _pack_out(db: Session, row: AuditExport, names: dict[str, str], labels: dict[str, str]) -> dict[str, Any]:
    stored = db.get(StoredFile, row.file_id) if row.file_id else None
    return {"id": row.id, "title": row.title, "registers": [{"key": k, "label": labels.get(k, k), "records": (row.counts or {}).get(k)} for k in row.registers or []],
            "date_from": row.date_from.isoformat() if row.date_from else None, "date_to": row.date_to.isoformat() if row.date_to else None, "status": row.status, "error": row.error,
            "sha256": row.sha256, "size_bytes": row.size_bytes, "file_available": bool(stored is not None and file_exists(stored)), "created_by": names.get(row.created_by or "", ""),
            "created_at": iso(row.created_at)}


@router.get("/api/audit-exports/registers")
def pack_registers(caller: Caller = Depends(require_permission("audit_exports", "view"))) -> dict[str, Any]:
    return {"items": [{"key": k, **v} for k, v in _registers(caller).items()], "max_records_per_register": MAX_ROWS, "can_create": caller.can("audit_exports", "edit"),
            "not_included": "Attached files, photos, signatures and PDFs are not put in a pack. It holds the records and their answers."}


@router.get("/api/audit-exports")
def list_packs(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), caller: Caller = Depends(require_permission("audit_exports", "view")),
               db: Session = Depends(get_db)) -> dict[str, Any]:
    stmt = select(AuditExport).where(AuditExport.org_id == caller.org_id, AuditExport.deleted_at.is_(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditExport.created_at.desc(), AuditExport.id).limit(limit).offset(offset)).all()
    names = _names(db, caller.org_id, {r.created_by for r in rows})
    labels = {k: v["label"] for k, v in _registers(caller).items()}
    return {"items": [_pack_out(db, r, names, labels) for r in rows], "total": int(total), "limit": limit, "offset": offset, "can_create": caller.can("audit_exports", "edit")}


@router.post("/api/audit-exports", status_code=201)
def create_pack(body: PackIn, request: Request, caller: Caller = Depends(require_permission("audit_exports", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Build the pack now. Nothing is emailed or sent anywhere: it is kept in the portal to download."""
    org = db.get(Organisation, caller.org_id)
    available = _registers(caller)
    wanted = list(dict.fromkeys(body.registers))
    unknown = [k for k in wanted if k not in available]
    if unknown:
        raise HTTPException(422, f"Unknown register: {', '.join(unknown)}")
    refused = [available[k]["label"] for k in wanted if not available[k]["allowed"]]
    if refused:
        raise HTTPException(403, f"You can only include registers you can see for the whole organisation. Not allowed: {', '.join(refused)}.")
    if body.date_from and body.date_to and body.date_to < body.date_from:
        raise HTTPException(422, "The end date is before the start date")

    made_at = utcnow()
    files: dict[str, bytes] = {}
    counts: dict[str, int] = {}
    for key in wanted:
        header, rows = _collect(db, caller, org, key, body.date_from, body.date_to)
        files[f"{key}.csv"] = _csv_bytes(header, rows)
        counts[key] = len(rows)
    manifest = {
        "pack": body.title.strip(), "organisation": org.name, "made_at_utc": iso(made_at), "made_by": caller.user.name, "portal_version": get_settings().app_version,
        "period": {"from": body.date_from.isoformat() if body.date_from else None, "to": body.date_to.isoformat() if body.date_to else None, "timezone": org.timezone},
        "files": [{"name": name, "register": available[name[:-4]]["label"], "records": counts[name[:-4]], "dated_by": available[name[:-4]]["dated_by"], "bytes": len(data),
                   "sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()],
        "not_included": "Attached files, photos, signatures and PDFs. Deleted records. Other people's unfinished drafts of forms.",
        "how_to_check": "The SHA-256 of each file in this pack should equal the value listed here. The SHA-256 of the whole ZIP is shown in the portal next to the pack.",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        stamp = made_at.timetuple()[:6]
        for name, data in [("manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8")), *files.items()]:
            archive.writestr(zipfile.ZipInfo(name, date_time=stamp), data)
    data = buffer.getvalue()
    stored = save_bytes(db, org_id=org.id, data=data, name=f"audit-pack-{made_at:%Y%m%d-%H%M%S}.zip", mime_type="application/zip", purpose="audit_export", created_by=caller.user.id)
    row = AuditExport(org_id=org.id, title=body.title.strip(), registers=wanted, date_from=body.date_from, date_to=body.date_to, status="ready", counts=counts, file_id=stored.id,
                      sha256=stored.sha256, size_bytes=stored.size_bytes, created_by=caller.user.id, created_at=made_at)
    db.add(row)
    db.flush()
    audit(db, request, org_id=org.id, actor_id=caller.user.id, action="audit_export.created", target_type="audit_export", target_id=row.id,
          detail={"registers": wanted, "records": counts, "sha256": stored.sha256})
    db.commit()
    return _pack_out(db, row, {caller.user.id: caller.user.name}, {k: v["label"] for k, v in available.items()})


def _pack(db: Session, caller: Caller, pack_id: str) -> AuditExport:
    row = db.get(AuditExport, pack_id)
    if row is None or row.org_id != caller.org_id or row.deleted_at is not None:
        raise HTTPException(404, "Audit pack not found")
    return row


@router.get("/api/audit-exports/{pack_id}")
def get_pack(pack_id: str, caller: Caller = Depends(require_permission("audit_exports", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _pack(db, caller, pack_id)
    return _pack_out(db, row, _names(db, caller.org_id, {row.created_by}), {k: v["label"] for k, v in _registers(caller).items()})


@router.get("/api/audit-exports/{pack_id}/download")
def download_pack(pack_id: str, request: Request, caller: Caller = Depends(require_permission("audit_exports", "view")), db: Session = Depends(get_db)):
    """A pack holds whole-organisation records, so downloading needs the same reach as making it."""
    row = _pack(db, caller, pack_id)
    available = _registers(caller)
    refused = [available[k]["label"] for k in row.registers or [] if k in available and not available[k]["allowed"]]
    if refused:
        raise HTTPException(403, f"This pack holds registers you cannot see for the whole organisation: {', '.join(refused)}.")
    stored = db.get(StoredFile, row.file_id) if row.file_id else None
    if stored is None or stored.org_id != caller.org_id:
        raise HTTPException(410, "The stored file is missing. Make the pack again.")
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="audit_export.downloaded", target_type="audit_export", target_id=row.id, detail={"sha256": row.sha256})
    db.commit()
    return file_response(stored, download_name=stored.original_name)


@router.delete("/api/audit-exports/{pack_id}")
def remove_pack(pack_id: str, request: Request, caller: Caller = Depends(require_permission("audit_exports", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Takes the pack off the list. The row and the stored file are kept, so what was handed to an auditor can still be traced."""
    row = _pack(db, caller, pack_id)
    row.deleted_at, row.deleted_by = utcnow(), caller.user.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="audit_export.removed", target_type="audit_export", target_id=row.id, detail={"title": row.title})
    db.commit()
    return {"removed": True}
