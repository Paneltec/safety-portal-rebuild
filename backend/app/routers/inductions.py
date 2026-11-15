"""Inductions matrix, completed training and HR documents (stage 4d).

An induction is a certificate whose type is in the "induction" category, so the matrix and the
certification register are two views of the same records, never two stores. A matrix cell is:

  valid / expiring / expired / unknown   the worker's current certificate of that type
  not_applicable                         deliberately marked as not needed for this worker
  blank                                  nothing recorded
"""
import csv
import io
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import people
from ..db import get_db
from ..deps import Caller, get_caller, require_permission
from ..files import file_out, file_response, save_upload
from ..models import (
    Certification,
    CertificationKind,
    InductionExemption,
    Organisation,
    StoredFile,
    TrainingRecord,
    User,
    Worker,
    WorkerHrDocument,
    utcnow,
)
from ..services import audit, iso

types_router = APIRouter(prefix="/api/inductions/types", tags=["inductions"])
matrix_router = APIRouter(prefix="/api/workers/inductions", tags=["inductions"])
training_router = APIRouter(prefix="/api/completed-training", tags=["training"])
hr_router = APIRouter(prefix="/api/workers/{worker_id}/hr-documents", tags=["hr documents"])

EVIDENCE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
HR_TYPES = EVIDENCE_TYPES | {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


# ---------- induction types (matrix columns) ----------

class TypeIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)


def _induction_kinds(db: Session, org_id: str, *, include_inactive: bool = False) -> list[CertificationKind]:
    return [k for k in people.ensure_kinds(db, org_id) if k.category == "induction" and (include_inactive or k.active)]


def _type_out(kind: CertificationKind) -> dict[str, Any]:
    return {"id": kind.id, "column_key": kind.slug, "name": kind.label, "active": kind.active, "is_system": kind.is_system}


@types_router.get("")
def list_types(include_inactive: bool = False, caller: Caller = Depends(require_permission("inductions", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = _induction_kinds(db, caller.org_id, include_inactive=include_inactive)
    db.commit()
    return {"items": [_type_out(k) for k in rows]}


@types_router.post("", status_code=201)
def create_type(body: TypeIn, request: Request, caller: Caller = Depends(require_permission("inductions", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Add a column to the matrix. The name must not clash with any certificate type."""
    if not caller.can("inductions", "team_view"):
        raise HTTPException(403, "You do not have permission to do this")
    if people.resolve_kind(db, caller.org_id, body.name) is not None:
        raise HTTPException(409, "A certificate or induction type with this name already exists")
    kind = CertificationKind(org_id=caller.org_id, slug=people.new_kind_slug(db, caller.org_id, body.name), label=body.name.strip(), category="induction")
    db.add(kind)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="induction_type.created", target_type="certification_kind", target_id=kind.id, detail={"slug": kind.slug})
    db.commit()
    return _type_out(kind)


def _type(db: Session, caller: Caller, type_id: str) -> CertificationKind:
    kind = db.get(CertificationKind, type_id)
    if kind is None or kind.org_id != caller.org_id or kind.category != "induction":
        raise HTTPException(404, "Induction type not found")
    return kind


@types_router.patch("/{type_id}")
def rename_type(type_id: str, body: TypeIn, request: Request, caller: Caller = Depends(require_permission("inductions", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    if not caller.can("inductions", "team_view"):
        raise HTTPException(403, "You do not have permission to do this")
    kind = _type(db, caller, type_id)
    other = people.resolve_kind(db, caller.org_id, body.name)
    if other is not None and other.id != kind.id:
        raise HTTPException(409, "Another type already uses this name")
    kind.label = body.name.strip()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="induction_type.renamed", target_type="certification_kind", target_id=kind.id)
    db.commit()
    return _type_out(kind)


@types_router.delete("/{type_id}")
def retire_type(type_id: str, request: Request, caller: Caller = Depends(require_permission("inductions", "delete")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Retire a column. Completions already recorded keep it and stay in each worker's history."""
    kind = _type(db, caller, type_id)
    kind.active = False
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="induction_type.retired", target_type="certification_kind", target_id=kind.id)
    db.commit()
    return _type_out(kind)


@types_router.post("/{type_id}/restore")
def restore_type(type_id: str, request: Request, caller: Caller = Depends(require_permission("inductions", "delete")), db: Session = Depends(get_db)) -> dict[str, Any]:
    kind = _type(db, caller, type_id)
    kind.active = True
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="induction_type.restored", target_type="certification_kind", target_id=kind.id)
    db.commit()
    return _type_out(kind)


# ---------- matrix ----------

class CellIn(BaseModel):
    worker_id: str
    column_key: str
    issue_date: date | None = None
    expiry_date: date | None = None
    held_no_expiry: bool = False
    not_held: bool = False
    clear: bool = False


def _matrix(db: Session, caller: Caller, *, q: str | None = None, include_inactive: bool = False) -> dict[str, Any]:
    org = db.get(Organisation, caller.org_id)
    window = people.cert_settings(org)["expiry_window_days"]
    today = people.today_for(org)
    columns = _induction_kinds(db, caller.org_id)
    stmt = select(Worker).where(Worker.org_id == caller.org_id, Worker.deleted_at.is_(None), Worker.archived_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(Worker.active.is_(True))
    if not caller.can("inductions", "team_view"):
        stmt = stmt.where(Worker.id == (caller.user.worker_id or ""))
    if q and q.strip():
        stmt = stmt.where(func.lower(Worker.first_name + " " + Worker.last_name).like(f"%{q.strip().lower()}%"))
    workers = db.scalars(stmt.order_by(func.lower(Worker.last_name), func.lower(Worker.first_name), Worker.id).limit(500)).all()
    ids = [w.id for w in workers]
    slugs = [c.slug for c in columns]
    certs: dict[tuple[str, str], Certification] = {}
    exempt: set[tuple[str, str]] = set()
    if ids and slugs:
        for cert in db.scalars(select(Certification).where(
                Certification.worker_id.in_(ids), Certification.kind_slug.in_(slugs), Certification.superseded_by_id.is_(None),
                Certification.archived_at.is_(None), Certification.deleted_at.is_(None))).all():
            certs[(cert.worker_id, cert.kind_slug)] = cert
        exempt = {(e.worker_id, e.kind_slug) for e in db.scalars(select(InductionExemption).where(
            InductionExemption.worker_id.in_(ids), InductionExemption.kind_slug.in_(slugs))).all()}
    summary = {"valid": 0, "expiring": 0, "expired": 0, "unknown": 0, "not_applicable": 0, "blank": 0}
    rows = []
    for worker in workers:
        cells: dict[str, Any] = {}
        for slug in slugs:
            cert = certs.get((worker.id, slug))
            if cert is not None:
                state, days = people.cert_status(cert, today, window)
                cells[slug] = {"status": state, "expiry_date": cert.expiry_date.isoformat() if cert.expiry_date else None,
                               "issue_date": cert.issue_date.isoformat() if cert.issue_date else None,
                               "no_expiry": cert.no_expiry, "days_left": days, "certification_id": cert.id,
                               "has_evidence": cert.evidence_file_id is not None}
            elif (worker.id, slug) in exempt:
                state = "not_applicable"
                cells[slug] = {"status": state}
            else:
                state = "blank"
                cells[slug] = {"status": state}
            summary[state] += 1
        rows.append({"worker": {"id": worker.id, "name": people.worker_name(worker), "position": worker.position, "active": worker.active}, "cells": cells})
    return {"columns": [{"column_key": c.slug, "name": c.label} for c in columns], "rows": rows, "summary": summary,
            "window_days": window, "can_edit": caller.can("inductions", "edit"), "truncated": len(workers) == 500}


@matrix_router.get("/matrix")
def matrix(
    q: str | None = Query(None, max_length=100), include_inactive: bool = False,
    caller: Caller = Depends(require_permission("inductions", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Workers down the side, inductions across the top. Without team view a person sees only their own row."""
    out = _matrix(db, caller, q=q, include_inactive=include_inactive)
    db.commit()
    return out


@matrix_router.get("/export.csv")
def export_matrix(request: Request, caller: Caller = Depends(require_permission("inductions", "view")), db: Session = Depends(get_db)) -> Response:
    data = _matrix(db, caller)
    labels = {"valid": "Valid", "expiring": "Expiring", "expired": "Expired", "unknown": "Held, expiry not recorded", "not_applicable": "Not applicable", "blank": ""}

    def safe(value: str) -> str:
        return "'" + value if value[:1] in ("=", "+", "-", "@") else value

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Worker", "Position", *[safe(c["name"]) for c in data["columns"]]])
    for row in data["rows"]:
        line = [safe(row["worker"]["name"]), safe(row["worker"]["position"] or "")]
        for column in data["columns"]:
            cell = row["cells"][column["column_key"]]
            if cell.get("no_expiry"):
                line.append("Does not expire")
            elif cell.get("expiry_date"):
                year, month, day = cell["expiry_date"].split("-")
                line.append(f"{day}/{month}/{year} ({labels[cell['status']]})")
            else:
                line.append(labels[cell["status"]])
        writer.writerow(line)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="inductions.exported", target_type="organisation", target_id=caller.org_id, detail={"rows": len(data["rows"])})
    db.commit()
    return Response("﻿" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="inductions-matrix.csv"'})


@matrix_router.put("/cell")
def set_cell(body: CellIn, request: Request, caller: Caller = Depends(require_permission("inductions", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Set one cell: a date, "does not expire", "not applicable", or clear it."""
    worker = db.get(Worker, body.worker_id)
    if worker is None or worker.org_id != caller.org_id or worker.deleted_at is not None:
        raise HTTPException(404, "Worker not found")
    if not caller.can("inductions", "team_view") and caller.user.worker_id != worker.id:
        raise HTTPException(404, "Worker not found")
    kind = next((k for k in _induction_kinds(db, caller.org_id) if k.slug == body.column_key), None)
    if kind is None:
        raise HTTPException(404, "Induction type not found")
    chosen = sum([body.clear, body.not_held, body.held_no_expiry, body.expiry_date is not None])
    if chosen != 1:
        raise HTTPException(422, "Give exactly one of: an expiry date, does not expire, not applicable, or clear")
    if body.issue_date and body.expiry_date and body.expiry_date < body.issue_date:
        raise HTTPException(422, "The expiry date is before the issue date")

    current = next((c for c in people.current_certs(db, worker.id) if c.kind_slug == kind.slug), None)
    exemption = db.scalars(select(InductionExemption).where(InductionExemption.worker_id == worker.id, InductionExemption.kind_slug == kind.slug)).first()
    detail: dict[str, Any] = {"kind": kind.slug}
    if body.clear:
        if current is not None:
            current.deleted_at = utcnow()
            if current.supersedes_id:
                previous = db.get(Certification, current.supersedes_id)
                if previous is not None:
                    previous.superseded_by_id = None
            detail["certification_id"] = current.id
        if exemption is not None:
            db.delete(exemption)
        action = "induction.cleared"
    elif body.not_held:
        if current is not None:
            raise HTTPException(409, "A completion is recorded for this induction. Clear it first if it was entered by mistake.")
        if exemption is None:
            db.add(InductionExemption(org_id=caller.org_id, worker_id=worker.id, kind_slug=kind.slug, created_by=caller.user.id))
        action = "induction.marked_not_applicable"
    else:
        if exemption is not None:
            db.delete(exemption)
        if current is None:
            current = Certification(org_id=caller.org_id, worker_id=worker.id, kind_slug=kind.slug, created_by=caller.user.id)
            db.add(current)
        current.expiry_date = None if body.held_no_expiry else body.expiry_date
        current.no_expiry = body.held_no_expiry
        if body.issue_date is not None:
            current.issue_date = body.issue_date
        db.flush()
        detail["certification_id"] = current.id
        action = "induction.recorded"
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=action, target_type="worker", target_id=worker.id, detail=detail)
    db.commit()
    row = next(r for r in _matrix(db, caller, include_inactive=True)["rows"] if r["worker"]["id"] == worker.id)
    return {"worker_id": worker.id, "column_key": kind.slug, "cell": row["cells"][kind.slug]}


# ---------- completed training ----------

class TrainingIn(BaseModel):
    worker_id: str
    course: str = Field(min_length=2, max_length=200)
    provider: str | None = Field(None, max_length=200)
    completed_date: date | None = None
    notes: str | None = Field(None, max_length=2000)


class TrainingPatch(BaseModel):
    course: str | None = Field(None, min_length=2, max_length=200)
    provider: str | None = Field(None, max_length=200)
    completed_date: date | None = None
    notes: str | None = Field(None, max_length=2000)


def _training_out(db: Session, record: TrainingRecord, worker: Worker) -> dict[str, Any]:
    stored = db.get(StoredFile, record.evidence_file_id) if record.evidence_file_id else None
    return {"id": record.id, "worker": {"id": worker.id, "name": people.worker_name(worker)}, "course": record.course,
            "provider": record.provider, "completed_date": record.completed_date.isoformat() if record.completed_date else None,
            "notes": record.notes, "evidence": file_out(stored) if stored else None,
            "evidence_url": f"/api/completed-training/{record.id}/file" if stored else None, "created_at": iso(record.created_at)}


def _training_scope(caller: Caller) -> Caller:
    if not (caller.can("reference_library", "view") or caller.can("inductions", "view")):
        raise HTTPException(403, "You do not have permission to do this")
    return caller


def _sees_all_training(caller: Caller) -> bool:
    return caller.can("reference_library", "view") or caller.can("inductions", "team_view")


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


@training_router.get("")
def list_training(
    worker_id: str | None = None, q: str | None = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    caller: Caller = Depends(get_caller), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Completed courses, newest first. Without team access a person sees only their own."""
    _training_scope(caller)
    stmt = select(TrainingRecord, Worker).join(Worker, Worker.id == TrainingRecord.worker_id).where(
        TrainingRecord.org_id == caller.org_id, TrainingRecord.deleted_at.is_(None), Worker.deleted_at.is_(None))
    if not _sees_all_training(caller):
        stmt = stmt.where(Worker.id == (caller.user.worker_id or ""))
    if worker_id:
        stmt = stmt.where(TrainingRecord.worker_id == worker_id)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(TrainingRecord.course).like(like) | func.lower(Worker.first_name + " " + Worker.last_name).like(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pairs = db.execute(stmt.order_by(TrainingRecord.completed_date.desc().nulls_last(), TrainingRecord.created_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [_training_out(db, r, w) for r, w in pairs], "total": int(total), "limit": limit, "offset": offset,
            "can_edit": caller.can("reference_library", "edit")}


@training_router.post("", status_code=201)
def add_training(body: TrainingIn, request: Request, caller: Caller = Depends(require_permission("reference_library", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    worker = db.get(Worker, body.worker_id)
    if worker is None or worker.org_id != caller.org_id or worker.deleted_at is not None:
        raise HTTPException(404, "Worker not found")
    if body.completed_date and body.completed_date > people.today_for(db.get(Organisation, caller.org_id)):
        raise HTTPException(422, "The completion date is in the future")
    record = TrainingRecord(org_id=caller.org_id, worker_id=worker.id, course=body.course.strip(), provider=_clean(body.provider),
                            completed_date=body.completed_date, notes=_clean(body.notes), created_by=caller.user.id)
    db.add(record)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="training.recorded", target_type="worker", target_id=worker.id, detail={"training_id": record.id, "course": record.course})
    db.commit()
    return _training_out(db, record, worker)


def _training(db: Session, caller: Caller, record_id: str) -> tuple[TrainingRecord, Worker]:
    record = db.get(TrainingRecord, record_id)
    if record is None or record.org_id != caller.org_id or record.deleted_at is not None:
        raise HTTPException(404, "Training record not found")
    worker = db.get(Worker, record.worker_id)
    if not _sees_all_training(caller) and caller.user.worker_id != worker.id:
        raise HTTPException(404, "Training record not found")
    return record, worker


@training_router.get("/{record_id}")
def get_training(record_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    _training_scope(caller)
    record, worker = _training(db, caller, record_id)
    return _training_out(db, record, worker)


@training_router.patch("/{record_id}")
def edit_training(record_id: str, body: TrainingPatch, request: Request, caller: Caller = Depends(require_permission("reference_library", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    record, worker = _training(db, caller, record_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("completed_date") and data["completed_date"] > people.today_for(db.get(Organisation, caller.org_id)):
        raise HTTPException(422, "The completion date is in the future")
    for key, value in data.items():
        if key == "course":
            if value is None:
                continue
            value = value.strip()
        elif isinstance(value, str):
            value = _clean(value)
        setattr(record, key, value)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="training.updated", target_type="worker", target_id=worker.id, detail={"training_id": record.id, "fields": sorted(data)})
    db.commit()
    return _training_out(db, record, worker)


@training_router.delete("/{record_id}")
def delete_training(record_id: str, request: Request, caller: Caller = Depends(require_permission("reference_library", "delete")), db: Session = Depends(get_db)) -> dict[str, Any]:
    record, worker = _training(db, caller, record_id)
    record.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="training.deleted", target_type="worker", target_id=worker.id, detail={"training_id": record.id})
    db.commit()
    return {"ok": True}


@training_router.post("/{record_id}/file")
def training_evidence(record_id: str, request: Request, file: UploadFile = File(...), caller: Caller = Depends(require_permission("reference_library", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    record, worker = _training(db, caller, record_id)
    stored = save_upload(db, org_id=caller.org_id, upload=file, purpose="training_evidence", created_by=caller.user.id, allowed_mimes=EVIDENCE_TYPES)
    record.evidence_file_id = stored.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="training.evidence_uploaded", target_type="worker", target_id=worker.id, detail={"training_id": record.id})
    db.commit()
    return _training_out(db, record, worker)


@training_router.get("/{record_id}/file")
def training_file(record_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    _training_scope(caller)
    record, _worker = _training(db, caller, record_id)
    stored = db.get(StoredFile, record.evidence_file_id) if record.evidence_file_id else None
    if stored is None or stored.org_id != caller.org_id:
        raise HTTPException(404, "No file is attached")
    return file_response(stored, inline=True)


# ---------- HR documents (private and confidential) ----------

class HrPatch(BaseModel):
    title: str | None = Field(None, min_length=2, max_length=200)
    notes: str | None = Field(None, max_length=2000)


def _hr_worker(db: Session, caller: Caller, worker_id: str) -> Worker:
    worker = db.get(Worker, worker_id)
    if worker is None or worker.org_id != caller.org_id or worker.deleted_at is not None:
        raise HTTPException(404, "Worker not found")
    return worker


def _hr_out(db: Session, doc: WorkerHrDocument, names: dict[str, str]) -> dict[str, Any]:
    stored = db.get(StoredFile, doc.file_id)
    return {"id": doc.id, "title": doc.title, "notes": doc.notes, "file": file_out(stored) if stored else None,
            "file_url": f"/api/workers/{doc.worker_id}/hr-documents/{doc.id}/file", "uploaded_by": names.get(doc.created_by or "", "Unknown"),
            "created_at": iso(doc.created_at), "archived_at": iso(doc.archived_at)}


def _names(db: Session, ids: set[str | None]) -> dict[str, str]:
    wanted = {i for i in ids if i}
    return {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(wanted))).all()} if wanted else {}


@hr_router.get("")
def list_hr(worker_id: str, include_archived: bool = False, caller: Caller = Depends(require_permission("hr_employees", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    worker = _hr_worker(db, caller, worker_id)
    stmt = select(WorkerHrDocument).where(WorkerHrDocument.worker_id == worker.id, WorkerHrDocument.deleted_at.is_(None))
    if not include_archived:
        stmt = stmt.where(WorkerHrDocument.archived_at.is_(None))
    rows = db.scalars(stmt.order_by(WorkerHrDocument.created_at.desc())).all()
    names = _names(db, {d.created_by for d in rows})
    return {"items": [_hr_out(db, d, names) for d in rows], "can_open": caller.can("hr_employees", "reveal_pii"), "can_edit": caller.can("hr_employees", "edit")}


@hr_router.post("", status_code=201)
def upload_hr(worker_id: str, request: Request, file: UploadFile = File(...), title: str | None = Form(None), notes: str | None = Form(None),
              caller: Caller = Depends(require_permission("hr_employees", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    worker = _hr_worker(db, caller, worker_id)
    stored = save_upload(db, org_id=caller.org_id, upload=file, purpose="hr_document", created_by=caller.user.id, allowed_mimes=HR_TYPES)
    doc = WorkerHrDocument(org_id=caller.org_id, worker_id=worker.id, file_id=stored.id, title=_clean(title) or stored.original_name, notes=_clean(notes), created_by=caller.user.id)
    db.add(doc)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="hr_document.uploaded", target_type="worker", target_id=worker.id, detail={"document_id": doc.id})
    db.commit()
    return _hr_out(db, doc, {caller.user.id: caller.user.name})


def _hr_doc(db: Session, caller: Caller, worker_id: str, doc_id: str) -> tuple[Worker, WorkerHrDocument]:
    worker = _hr_worker(db, caller, worker_id)
    doc = db.get(WorkerHrDocument, doc_id)
    if doc is None or doc.worker_id != worker.id or doc.deleted_at is not None:
        raise HTTPException(404, "Document not found")
    return worker, doc


@hr_router.get("/{doc_id}/file")
def hr_file(worker_id: str, doc_id: str, request: Request, caller: Caller = Depends(require_permission("hr_employees", "reveal_pii")), db: Session = Depends(get_db)):
    """Open a confidential file. Every open is written to the worker's history."""
    worker, doc = _hr_doc(db, caller, worker_id, doc_id)
    stored = db.get(StoredFile, doc.file_id)
    if stored is None or stored.org_id != caller.org_id:
        raise HTTPException(404, "No file")
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="hr_document.opened", target_type="worker", target_id=worker.id, detail={"document_id": doc.id})
    db.commit()
    return file_response(stored, inline=True)


@hr_router.patch("/{doc_id}")
def edit_hr(worker_id: str, doc_id: str, body: HrPatch, request: Request, caller: Caller = Depends(require_permission("hr_employees", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Title and notes only. The file itself cannot be changed: upload a new document instead."""
    worker, doc = _hr_doc(db, caller, worker_id, doc_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("title"):
        doc.title = data["title"].strip()
    if "notes" in data:
        doc.notes = _clean(data["notes"])
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="hr_document.updated", target_type="worker", target_id=worker.id, detail={"document_id": doc.id, "fields": sorted(data)})
    db.commit()
    return _hr_out(db, doc, _names(db, {doc.created_by}))


def _hr_flag(action: str, permission: str):
    def handler(worker_id: str, doc_id: str, request: Request, caller: Caller = Depends(require_permission("hr_employees", permission)), db: Session = Depends(get_db)) -> dict[str, Any]:
        worker, doc = _hr_doc(db, caller, worker_id, doc_id)
        if action == "archive":
            doc.archived_at = doc.archived_at or utcnow()
        elif action == "restore":
            doc.archived_at = None
        else:
            doc.deleted_at = utcnow()
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"hr_document.{action}d", target_type="worker", target_id=worker.id, detail={"document_id": doc.id})
        db.commit()
        return {"ok": True} if action == "delete" else _hr_out(db, doc, _names(db, {doc.created_by}))

    return handler


hr_router.add_api_route("/{doc_id}/archive", _hr_flag("archive", "archive"), methods=["POST"])
hr_router.add_api_route("/{doc_id}/restore", _hr_flag("restore", "archive"), methods=["POST"])
hr_router.add_api_route("/{doc_id}", _hr_flag("delete", "edit"), methods=["DELETE"])
