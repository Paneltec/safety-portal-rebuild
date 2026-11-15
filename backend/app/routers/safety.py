"""Safety registers API. One set of routes per register kind, built from a shared factory,
plus the SWMS-only extras (versions, assignments, acknowledgements)."""
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import forms as engine
from .. import safety
from ..db import get_db
from ..deps import Caller, get_caller
from ..files import file_exists, file_response, read_bytes, save_upload
from ..models import (
    Company, FormSubmission, FormSubmissionFile, FormTemplateVersion, Organisation, Role, SafetyAcknowledgement,
    SafetyAssignment, SafetyRecord, SafetyRecordEvent, SafetyRecordFile, StoredFile, User, new_id, utcnow,
)
from ..pdf import _when, submission_pdf
from ..permissions import TEAM_SCOPED_RESOURCES
from ..services import audit, iso

IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
ATTACHMENT_TYPES = IMAGE_TYPES | {"application/pdf"}


class FieldValue(BaseModel):
    id: str
    value: Any = None


class RecordIn(BaseModel):
    fields: list[FieldValue] = Field(default_factory=list)
    status: str | None = None  # review kinds: "draft" (default) or "submitted"
    workspace_id: str | None = Field(default=None, max_length=36)


class RecordPatch(BaseModel):
    fields: list[FieldValue]
    expected_revision: int | None = None


class ReviewIn(BaseModel):
    action: str  # approve | request_changes | reject
    note: str | None = Field(default=None, max_length=2000)


class StatusIn(BaseModel):
    status: str
    note: str | None = Field(default=None, max_length=2000)


class BulkArchiveIn(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=1000)
    before_date: date | None = None
    status: str | None = None
    dry_run: bool = True


class RuleIn(BaseModel):
    kind: str
    target_id: str = Field(min_length=1, max_length=80)


class AssignmentsIn(BaseModel):
    rules: list[RuleIn] = Field(max_length=500)


class AcknowledgeIn(BaseModel):
    signed_name: str = Field(min_length=1, max_length=200)


# ---------- shared helpers ----------

def _need(caller: Caller, resource: str, action: str) -> None:
    if not caller.can(resource, action):
        raise HTTPException(403, "You do not have permission to do this")


def _own_only(caller: Caller, kind: safety.Kind) -> bool:
    return kind.resource in TEAM_SCOPED_RESOURCES and not caller.can(kind.resource, "team_view")


def _assignment_reasons(db: Session, record: SafetyRecord, user: User) -> list[str] | None:
    rules = db.scalars(select(SafetyAssignment).where(SafetyAssignment.record_id == record.id)).all()
    if not rules:
        return ["Open to everyone who can view this register"]
    reasons = []
    for rule in rules:
        if rule.target_kind == "role" and rule.target_id == user.role_id:
            reasons.append("Assigned to their role")
        elif rule.target_kind == "user" and rule.target_id == user.id:
            reasons.append("Assigned to this person by name")
        elif rule.target_kind == "company" and user.company_id and rule.target_id == user.company_id:
            reasons.append("Assigned to their company")
    return reasons or None


def _reader_can_see(db: Session, caller: Caller, kind: safety.Kind, record: SafetyRecord) -> bool:
    """Record-level scope, checked on every read, download and export."""
    if _own_only(caller, kind) and record.created_by != caller.user.id:
        return False
    if kind.key == "swms" and not caller.can("swms", "edit"):
        # People who only read SWMS see approved ones that are assigned to them, whoever wrote them.
        if record.status != "approved" or record.archived_at is not None:
            return False
        return _assignment_reasons(db, record, caller.user) is not None
    return True


def _load(db: Session, caller: Caller, kind: safety.Kind, record_id: str) -> SafetyRecord:
    record = db.get(SafetyRecord, record_id)
    if (record is None or record.org_id != caller.org_id or record.kind != kind.key or record.deleted_at is not None
            or not _reader_can_see(db, caller, kind, record)):
        raise HTTPException(404, f"{kind.label} not found")
    return record


def _can_modify(caller: Caller, kind: safety.Kind, record: SafetyRecord) -> bool:
    if not caller.can(kind.resource, "edit") or record.archived_at is not None:
        return False
    if _own_only(caller, kind) and record.created_by != caller.user.id:
        return False
    if kind.workflow == "review":
        return record.status in safety.EDITABLE_REVIEW
    return record.status != "closed"


def _files(db: Session, record: SafetyRecord) -> list[dict[str, Any]]:
    """Files attached to the record, including those that came with the form it was projected from."""
    rows: list[tuple[str, str, str, StoredFile]] = [
        (link.id, link.field_id, link.kind, stored)
        for link, stored in db.execute(
            select(SafetyRecordFile, StoredFile).join(StoredFile, StoredFile.id == SafetyRecordFile.stored_file_id)
            .where(SafetyRecordFile.record_id == record.id).order_by(SafetyRecordFile.created_at, SafetyRecordFile.id)
        ).all()
    ]
    if record.source_submission_id:
        rows += [
            (link.id, link.field_id, link.kind, stored)
            for link, stored in db.execute(
                select(FormSubmissionFile, StoredFile).join(StoredFile, StoredFile.id == FormSubmissionFile.stored_file_id)
                .where(FormSubmissionFile.submission_id == record.source_submission_id)
                .order_by(FormSubmissionFile.created_at, FormSubmissionFile.id)
            ).all()
        ]
    return [{"id": i, "field_id": f, "kind": k, "name": s.original_name, "mime_type": s.mime_type, "size_bytes": s.size_bytes,
             "sha256": s.sha256, "missing": not file_exists(s), "_stored": s} for i, f, k, s in rows]


def _public_files(db: Session, record: SafetyRecord) -> list[dict[str, Any]]:
    return [{k: v for k, v in f.items() if k != "_stored"} for f in _files(db, record)]


def _event(db: Session, record: SafetyRecord, actor: str | None, action: str, note: str | None = None, **detail: Any) -> None:
    db.add(SafetyRecordEvent(org_id=record.org_id, record_id=record.id, action=action, actor_user_id=actor, note=note, detail=detail))


def _out(db: Session, caller: Caller, kind: safety.Kind, record: SafetyRecord, *, detail: bool = False) -> dict[str, Any]:
    author = db.get(User, record.created_by)
    out: dict[str, Any] = {
        "id": record.id,
        "kind": record.kind,
        "title": record.title,
        "status": record.status,
        "status_label": safety.STATUS_LABELS.get(record.status, record.status),
        "record_date": record.record_date.isoformat() if record.record_date else None,
        "revision": record.revision,
        "source": record.source,
        "source_submission_id": record.source_submission_id,
        "version_no": record.version_no,
        "supersedes_id": record.supersedes_id,
        "superseded_by_id": record.superseded_by_id,
        "created_by": record.created_by,
        "created_by_name": author.name if author else None,
        "created_at": iso(record.created_at),
        "updated_at": iso(record.updated_at),
        "submitted_at": iso(record.submitted_at),
        "archived_at": iso(record.archived_at),
        "category": (record.values or {}).get("category") or (record.values or {}).get("hazard_type"),
        "severity": (record.values or {}).get("severity"),
        "failed_check_count": len(safety.failed_checks(record.schema, record.values or {})),
    }
    if detail:
        out.update({
            "schema": record.schema,
            "values": record.values or {},
            "files": _public_files(db, record),
            "failed_checks": safety.failed_checks(record.schema, record.values or {}),
            "risk": safety.risk_summary(record.schema, record.values or {}),
            "can_edit": _can_modify(caller, kind, record),
            "can_review": kind.workflow == "review" and record.status == "submitted" and caller.can(kind.resource, "approve"),
            "can_archive": caller.can(kind.resource, "archive"),
            "can_delete": caller.can(kind.resource, "delete"),
        })
        if kind.key == "swms":
            mine = db.scalars(select(SafetyAcknowledgement).where(
                SafetyAcknowledgement.record_id == record.id, SafetyAcknowledgement.user_id == caller.user.id)).first()
            out["acknowledged_by_me_at"] = iso(mine.created_at) if mine else None
            out["acknowledgement_count"] = int(db.scalar(select(func.count()).select_from(SafetyAcknowledgement).where(
                SafetyAcknowledgement.record_id == record.id)) or 0)
    return out


def _values(items: list[FieldValue]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in items:
        if item.id in values:
            raise HTTPException(422, f"Field '{item.id}' is answered twice")
        values[item.id] = item.value
    return values


def _apply(db: Session, kind: safety.Kind, record: SafetyRecord, values: dict[str, Any], *, final: bool) -> None:
    by_field: dict[str, set[str]] = {}
    for f in _files(db, record):
        by_field.setdefault(f["field_id"], set()).add(f["id"])
    cleaned, errors = engine.validate_values(record.schema, values, final=final, files_by_field=by_field)
    if errors:
        raise HTTPException(422, {"message": "Some answers need attention", "errors": errors})
    record.values = cleaned
    record.title = safety.derive_title(kind.key, cleaned, record.title or kind.label)
    record.record_date = safety.derive_date(cleaned) or record.record_date


def project_submission(db: Session, sub: FormSubmission, version: FormTemplateVersion) -> SafetyRecord | None:
    """Put a submitted form into the register for its category, exactly once (unique per submission)."""
    kind_key = safety.KIND_FOR_CATEGORY.get(version.category)
    if kind_key is None:
        return None
    exists = db.scalars(select(SafetyRecord.id).where(
        SafetyRecord.org_id == sub.org_id, SafetyRecord.source_submission_id == sub.id)).first()
    if exists:
        return None
    kind = safety.KINDS[kind_key]
    values = dict(sub.values or {})
    record = SafetyRecord(
        org_id=sub.org_id, kind=kind.key, schema=version.schema, values=values,
        title=safety.derive_title(kind.key, values, version.name),
        record_date=safety.derive_date(values) or (sub.submitted_at or utcnow()).date(),
        status="submitted" if kind.workflow == "review" else "open",
        source="form", source_submission_id=sub.id, created_by=sub.submitted_by, submitted_at=sub.submitted_at,
    )
    db.add(record)
    db.flush()
    _event(db, record, sub.submitted_by, "created", form=version.name, form_version=version.version)
    return record


# ---------- route factory ----------

def make_router(kind: safety.Kind) -> APIRouter:
    router = APIRouter(prefix=f"/api/{kind.prefix}", tags=[kind.plural])
    res = kind.resource

    @router.get("/schema", name=f"{kind.key}_schema")
    def schema(caller: Caller = Depends(get_caller)) -> dict[str, Any]:
        _need(caller, res, "view")
        return {"kind": kind.key, "label": kind.label, "plural": kind.plural, "workflow": kind.workflow,
                "statuses": [{"key": s, "label": safety.STATUS_LABELS[s]} for s in kind.statuses],
                "schema": kind.schema, "risk_rule": safety.RISK_RULE if kind.key in ("risk_assessment", "ssra") else None,
                "can_create": caller.can(res, "edit"), "scope": "own" if _own_only(caller, kind) else "team"}

    @router.get("", name=f"{kind.key}_list")
    def list_records(
        status: str | None = None,
        q: str | None = Query(None, max_length=100),
        category: str | None = Query(None, max_length=60),
        date_from: date | None = None,
        date_to: date | None = None,
        include_archived: bool = False,
        include_superseded: bool = False,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        caller: Caller = Depends(get_caller),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _need(caller, res, "view")
        stmt = select(SafetyRecord).where(SafetyRecord.org_id == caller.org_id, SafetyRecord.kind == kind.key, SafetyRecord.deleted_at.is_(None))
        if _own_only(caller, kind):
            stmt = stmt.where(SafetyRecord.created_by == caller.user.id)
        if not include_archived:
            stmt = stmt.where(SafetyRecord.archived_at.is_(None))
        if not include_superseded:
            stmt = stmt.where(SafetyRecord.superseded_by_id.is_(None))
        if status:
            stmt = stmt.where(SafetyRecord.status == status)
        if q and q.strip():
            stmt = stmt.where(func.lower(SafetyRecord.title).like(f"%{q.strip().lower()}%"))
        if category:
            stmt = stmt.where((SafetyRecord.values["category"].astext == category) | (SafetyRecord.values["hazard_type"].astext == category))
        if date_from:
            stmt = stmt.where(SafetyRecord.record_date >= date_from)
        if date_to:
            stmt = stmt.where(SafetyRecord.record_date <= date_to)
        stmt = stmt.order_by(SafetyRecord.record_date.desc().nulls_last(), SafetyRecord.created_at.desc(), SafetyRecord.id)
        if kind.key == "swms" and not caller.can("swms", "edit"):
            # Assignment is checked per record, so filter first and page afterwards.
            visible = [r for r in db.scalars(stmt).all() if _reader_can_see(db, caller, kind, r)]
            total, rows = len(visible), visible[offset:offset + limit]
        else:
            total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
            rows = db.scalars(stmt.limit(limit).offset(offset)).all()
        return {"items": [_out(db, caller, kind, r) for r in rows], "total": int(total), "limit": limit, "offset": offset,
                "scope": "own" if _own_only(caller, kind) else "team"}

    @router.post("", status_code=201, name=f"{kind.key}_create")
    def create(body: RecordIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
        _need(caller, res, "edit")
        if kind.workflow == "review":
            status = body.status or "draft"
            if status not in ("draft", "submitted"):
                raise HTTPException(422, "status must be draft or submitted")
        else:
            status = body.status or "open"
            if status != "open":
                raise HTTPException(422, "A new record starts as open")
        record = SafetyRecord(org_id=caller.org_id, kind=kind.key, title=kind.label, status=status, schema=kind.schema,
                              values={}, created_by=caller.user.id, workspace_id=body.workspace_id)
        db.add(record)
        db.flush()
        # Tracking records and submitted review records must be complete; a draft may be partial.
        _apply(db, kind, record, _values(body.fields), final=status != "draft")
        if record.record_date is None:
            record.record_date = utcnow().date()
        if status == "submitted":
            record.submitted_at = utcnow()
        _event(db, record, caller.user.id, "created", status=status)
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"{kind.key}.created",
              target_type=kind.key, target_id=record.id, detail={"title": record.title, "status": status})
        db.commit()
        return _out(db, caller, kind, record, detail=True)

    @router.post("/archive", name=f"{kind.key}_bulk_archive")
    def bulk_archive(body: BulkArchiveIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
        """Archive many records by explicit ids or by criteria. Runs as a preview unless dry_run is false."""
        _need(caller, res, "archive")
        if not body.ids and body.before_date is None and body.status is None:
            raise HTTPException(400, "Choose records, a date or a status to archive")
        stmt = select(SafetyRecord).where(SafetyRecord.org_id == caller.org_id, SafetyRecord.kind == kind.key,
                                          SafetyRecord.deleted_at.is_(None), SafetyRecord.archived_at.is_(None))
        if body.ids:
            stmt = stmt.where(SafetyRecord.id.in_(body.ids))
        if body.before_date:
            stmt = stmt.where(SafetyRecord.record_date < body.before_date)
        if body.status:
            stmt = stmt.where(SafetyRecord.status == body.status)
        rows = db.scalars(stmt.order_by(SafetyRecord.record_date, SafetyRecord.id)).all()
        preview = {"matched": len(rows), "sample": [{"id": r.id, "title": r.title} for r in rows[:10]]}
        if body.dry_run:
            return {**preview, "dry_run": True, "archived": 0, "batch_id": None}
        batch = new_id()
        for record in rows:
            record.archived_at, record.archive_batch_id = utcnow(), batch
            _event(db, record, caller.user.id, "archived", batch_id=batch)
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"{kind.key}.bulk_archived",
              target_type=kind.key, target_id=None, detail={"batch_id": batch, "count": len(rows)})
        db.commit()
        return {**preview, "dry_run": False, "archived": len(rows), "batch_id": batch if rows else None}

    @router.post("/unarchive-batch/{batch_id}", name=f"{kind.key}_unarchive_batch")
    def unarchive_batch(batch_id: str, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
        _need(caller, res, "archive")
        rows = db.scalars(select(SafetyRecord).where(SafetyRecord.org_id == caller.org_id, SafetyRecord.kind == kind.key,
                                                     SafetyRecord.archive_batch_id == batch_id, SafetyRecord.archived_at.is_not(None))).all()
        if not rows:
            raise HTTPException(404, "Archive batch not found")
        for record in rows:
            record.archived_at, record.archive_batch_id = None, None
            _event(db, record, caller.user.id, "unarchived", batch_id=batch_id)
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"{kind.key}.batch_unarchived",
              target_type=kind.key, target_id=None, detail={"batch_id": batch_id, "count": len(rows)})
        db.commit()
        return {"restored": len(rows)}

    if kind.workflow == "tracking":
        @router.get("/category-counts", name=f"{kind.key}_category_counts")
        def category_counts(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
            """Counts by category and status, using the same scope as the list."""
            _need(caller, res, "view")
            stmt = select(SafetyRecord).where(SafetyRecord.org_id == caller.org_id, SafetyRecord.kind == kind.key,
                                              SafetyRecord.deleted_at.is_(None), SafetyRecord.archived_at.is_(None))
            if _own_only(caller, kind):
                stmt = stmt.where(SafetyRecord.created_by == caller.user.id)
            by_category: dict[str, int] = {}
            by_status: dict[str, int] = {}
            for record in db.scalars(stmt).all():
                category = (record.values or {}).get("category") or (record.values or {}).get("hazard_type") or "Uncategorised"
                by_category[category] = by_category.get(category, 0) + 1
                by_status[record.status] = by_status.get(record.status, 0) + 1
            return {"total": sum(by_status.values()), "by_category": by_category, "by_status": by_status}

    @router.get("/{record_id}", name=f"{kind.key}_get")
    def get_record(record_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
        _need(caller, res, "view")
        return _out(db, caller, kind, _load(db, caller, kind, record_id), detail=True)

    def _modifiable(db: Session, caller: Caller, record_id: str) -> SafetyRecord:
        _need(caller, res, "edit")
        record = _load(db, caller, kind, record_id)
        if not _can_modify(caller, kind, record):
            raise HTTPException(409, "This record can no longer be changed" if record.archived_at is None else "This record is archived")
        return record

    @router.patch("/{record_id}", name=f"{kind.key}_patch")
    def patch(record_id: str, body: RecordPatch, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
        record = _modifiable(db, caller, record_id)
        if body.expected_revision is not None and body.expected_revision != record.revision:
            raise HTTPException(409, "This record was changed by someone else. Reload it before saving.")
        before = dict(record.values or {})
        _apply(db, kind, record, _values(body.fields), final=kind.workflow == "tracking")
        changed = sorted(k for k in set(before) | set(record.values) if before.get(k) != record.values.get(k))
        if changed:
            record.revision += 1
            record.updated_by = caller.user.id
            # The earlier answers are kept in the history, so an edit never erases the original observation.
            _event(db, record, caller.user.id, "edited", changed=changed, before={k: before.get(k) for k in changed})
            audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"{kind.key}.updated",
                  target_type=kind.key, target_id=record.id, detail={"changed": changed})
            db.commit()
        return _out(db, caller, kind, record, detail=True)

    if kind.workflow == "review":
        @router.post("/{record_id}/submit", name=f"{kind.key}_submit")
        def submit(record_id: str, request: Request, body: RecordPatch | None = None, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
            record = _modifiable(db, caller, record_id)
            _apply(db, kind, record, _values(body.fields) if body is not None else dict(record.values or {}), final=True)
            record.status, record.submitted_at = "submitted", utcnow()
            record.revision += 1
            _event(db, record, caller.user.id, "submitted")
            audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"{kind.key}.submitted", target_type=kind.key, target_id=record.id)
            db.commit()
            return _out(db, caller, kind, record, detail=True)

        @router.post("/{record_id}/review", name=f"{kind.key}_review")
        def review(record_id: str, body: ReviewIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
            """Approve, return for changes, or reject. Only a person, never an automated step, can approve."""
            _need(caller, res, "approve")
            record = _load(db, caller, kind, record_id)
            if record.status != "submitted":
                raise HTTPException(409, "Only a record waiting for review can be reviewed")
            if body.action not in ("approve", "request_changes", "reject"):
                raise HTTPException(422, "action must be approve, request_changes or reject")
            note = (body.note or "").strip()
            if body.action != "approve" and not note:
                raise HTTPException(422, "Say what needs to change, or why it is rejected")
            record.status = {"approve": "approved", "request_changes": "changes_requested", "reject": "rejected"}[body.action]
            if body.action == "approve" and record.supersedes_id:
                previous = db.get(SafetyRecord, record.supersedes_id)
                if previous is not None and previous.org_id == record.org_id:
                    previous.superseded_by_id = record.id
                    _event(db, previous, caller.user.id, "superseded", by=record.id)
            _event(db, record, caller.user.id, f"review_{body.action}", note or None)
            audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"{kind.key}.reviewed",
                  target_type=kind.key, target_id=record.id, detail={"action": body.action})
            db.commit()
            return _out(db, caller, kind, record, detail=True)
    else:
        @router.post("/{record_id}/status", name=f"{kind.key}_status")
        def set_status(record_id: str, body: StatusIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
            """Move between open, in progress and closed. The original report and its history stay as they were."""
            _need(caller, res, "edit")
            record = _load(db, caller, kind, record_id)
            if _own_only(caller, kind) and record.created_by != caller.user.id:
                raise HTTPException(404, f"{kind.label} not found")
            if body.status not in safety.TRACKING_STATUSES:
                raise HTTPException(422, "status must be open, in_progress or closed")
            note = (body.note or "").strip()
            if body.status == "closed" and not note:
                raise HTTPException(422, "Say how this was resolved before closing it")
            if body.status != record.status:
                _event(db, record, caller.user.id, "status_changed", note or None, **{"from": record.status, "to": body.status})
                record.status = body.status
                record.revision += 1
                audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"{kind.key}.status_changed",
                      target_type=kind.key, target_id=record.id, detail={"to": body.status})
                db.commit()
            return _out(db, caller, kind, record, detail=True)

    def _set_archived(db: Session, request: Request, caller: Caller, record_id: str, archived: bool) -> dict[str, Any]:
        _need(caller, res, "archive")
        record = _load(db, caller, kind, record_id)
        if (record.archived_at is not None) != archived:
            record.archived_at = utcnow() if archived else None
            record.archive_batch_id = None
            _event(db, record, caller.user.id, "archived" if archived else "unarchived")
            audit(db, request, org_id=caller.org_id, actor_id=caller.user.id,
                  action=f"{kind.key}.{'archived' if archived else 'unarchived'}", target_type=kind.key, target_id=record.id)
            db.commit()
        return _out(db, caller, kind, record, detail=True)

    @router.post("/{record_id}/archive", name=f"{kind.key}_archive")
    def archive(record_id: str, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
        return _set_archived(db, request, caller, record_id, True)

    @router.post("/{record_id}/unarchive", name=f"{kind.key}_unarchive")
    def unarchive(record_id: str, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
        return _set_archived(db, request, caller, record_id, False)

    @router.delete("/{record_id}", name=f"{kind.key}_delete")
    def delete(record_id: str, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, bool]:
        """Remove from the register. An author may discard their own draft; anything else needs the delete right."""
        _need(caller, res, "view")
        record = _load(db, caller, kind, record_id)
        own_draft = record.status == "draft" and record.created_by == caller.user.id and caller.can(res, "edit")
        if not own_draft:
            _need(caller, res, "delete")
        record.deleted_at = utcnow()
        _event(db, record, caller.user.id, "deleted")
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"{kind.key}.deleted",
              target_type=kind.key, target_id=record.id, detail={"title": record.title, "status": record.status})
        db.commit()
        return {"ok": True}

    @router.get("/{record_id}/history", name=f"{kind.key}_history")
    def history(record_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
        _need(caller, res, "view")
        record = _load(db, caller, kind, record_id)
        rows = db.execute(
            select(SafetyRecordEvent, User.name).outerjoin(User, User.id == SafetyRecordEvent.actor_user_id)
            .where(SafetyRecordEvent.record_id == record.id).order_by(SafetyRecordEvent.created_at.desc(), SafetyRecordEvent.id)
        ).all()
        return {"count": len(rows), "events": [
            {"id": e.id, "action": e.action, "actor_name": name, "note": e.note, "detail": e.detail, "at": iso(e.created_at)} for e, name in rows]}

    @router.get("/{record_id}/pdf", name=f"{kind.key}_pdf")
    def pdf(record_id: str, download: int = Query(0, ge=0, le=1), caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> Response:
        from .org import org_out

        _need(caller, res, "view")
        record = _load(db, caller, kind, record_id)
        org = db.get(Organisation, caller.org_id)
        author = db.get(User, record.created_by)
        files = {f["id"]: {"name": f["name"], "mime": f["mime_type"], "data": read_bytes(f["_stored"])} for f in _files(db, record)}
        meta = [
            ("Register", kind.plural),
            ("Status", safety.STATUS_LABELS.get(record.status, record.status) + (" (archived)" if record.archived_at else "")),
            ("Recorded by", author.name if author else "Unknown"),
            ("Date", "/".join(reversed(record.record_date.isoformat().split("-"))) if record.record_date else "—"),
            ("Created", _when(record.created_at, org.timezone)),
        ]
        if kind.key == "swms":
            meta.append(("Version", str(record.version_no) + (" (superseded by a newer version)" if record.superseded_by_id else "")))
        failed = safety.failed_checks(record.schema, record.values or {})
        if failed:
            meta.append(("Failed checks", "; ".join(f["label"] for f in failed)))
        risk = safety.risk_summary(record.schema, record.values or {})
        if risk:
            meta.append(("Risk scoring rule", risk["rule"]))
            for row in risk["rows"]:
                if row["initial"]:
                    residual = f"; residual {row['residual']['score']} {row['residual']['rating']}" if row["residual"] else ""
                    meta.append((f"Risk: {row['hazard'][:40] or 'hazard'}", f"initial {row['initial']['score']} {row['initial']['rating']}{residual}"))
        meta.append(("Record", record.id))
        logo = read_bytes(db.get(StoredFile, org.logo_file_id)) if org.logo_file_id else None
        data = submission_pdf(org=org_out(db, org), logo=logo, title=record.title, meta=meta, schema=record.schema,
                              values=record.values or {}, files=files, timezone=org.timezone)
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in record.title).strip()[:80] or kind.key
        return Response(data, media_type="application/pdf", headers={
            "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{name}.pdf"',
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.post("/{record_id}/files", status_code=201, name=f"{kind.key}_upload_file")
    def upload_file(record_id: str, field_id: str = Form(...), file: UploadFile = File(...), caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
        record = _modifiable(db, caller, record_id)
        field = next((f for f in record.schema if f["id"] == field_id), None)
        if field is None or not engine.FIELD_TYPES[field["type"]].get("file"):
            raise HTTPException(400, "This field does not take files")
        if len(_files(db, record)) >= 100:
            raise HTTPException(400, "This record already has the maximum number of files")
        stored = save_upload(db, org_id=caller.org_id, upload=file, purpose=f"{kind.key}_{field['type']}", created_by=caller.user.id,
                             allowed_mimes=ATTACHMENT_TYPES if field["type"] == "attachment" else IMAGE_TYPES)
        link = SafetyRecordFile(org_id=caller.org_id, record_id=record.id, field_id=field_id, kind=field["type"],
                                stored_file_id=stored.id, created_by=caller.user.id)
        db.add(link)
        db.commit()
        return {"id": link.id, "field_id": field_id, "kind": link.kind, "name": stored.original_name, "mime_type": stored.mime_type,
                "size_bytes": stored.size_bytes, "sha256": stored.sha256}

    @router.get("/{record_id}/files/{file_id}", name=f"{kind.key}_download_file")
    def download_file(record_id: str, file_id: str, download: int = Query(0, ge=0, le=1), caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
        """A file is only reachable through a record the caller is allowed to see."""
        _need(caller, res, "view")
        record = _load(db, caller, kind, record_id)
        match = next((f for f in _files(db, record) if f["id"] == file_id), None)
        if match is None:
            raise HTTPException(404, "File not found")
        return file_response(match["_stored"], inline=download == 0)

    @router.delete("/{record_id}/files/{file_id}", name=f"{kind.key}_remove_file")
    def remove_file(record_id: str, file_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, bool]:
        record = _modifiable(db, caller, record_id)
        link = db.get(SafetyRecordFile, file_id)
        if link is None or link.record_id != record.id:
            raise HTTPException(404, "File not found")
        values = dict(record.values or {})
        current = values.get(link.field_id)
        if isinstance(current, list):
            values[link.field_id] = [v for v in current if v != link.id]
        elif isinstance(current, dict) and current.get("file_id") == link.id:
            values.pop(link.field_id)
        record.values = values
        db.delete(link)
        db.commit()
        return {"ok": True}

    return router


routers = [make_router(kind) for kind in safety.KINDS.values()]

# ---------- SWMS extras ----------

swms_router = APIRouter(prefix="/api/swms", tags=["SWMS"])
SWMS = safety.KINDS["swms"]


@swms_router.post("/{record_id}/new-version", status_code=201)
def new_version(record_id: str, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Start a new draft from an approved SWMS. The approved one stays in force, with its
    acknowledgements, until the new version is approved."""
    _need(caller, "swms", "edit")
    current = _load(db, caller, SWMS, record_id)
    if current.status != "approved" or current.superseded_by_id is not None:
        raise HTTPException(409, "A new version can only be started from the current approved SWMS")
    pending = db.scalars(select(SafetyRecord.id).where(
        SafetyRecord.org_id == caller.org_id, SafetyRecord.supersedes_id == current.id, SafetyRecord.deleted_at.is_(None),
        SafetyRecord.status.in_(["draft", "submitted", "changes_requested"]))).first()
    if pending:
        raise HTTPException(409, "A new version of this SWMS is already being prepared")
    file_fields = {f["id"] for f in current.schema if engine.FIELD_TYPES[f["type"]].get("file")}
    draft = SafetyRecord(org_id=caller.org_id, kind="swms", title=current.title, status="draft", schema=SWMS.schema,
                         values={k: v for k, v in (current.values or {}).items() if k not in file_fields},
                         record_date=utcnow().date(), created_by=caller.user.id, supersedes_id=current.id,
                         version_no=current.version_no + 1, workspace_id=current.workspace_id)
    db.add(draft)
    db.flush()
    for rule in db.scalars(select(SafetyAssignment).where(SafetyAssignment.record_id == current.id)).all():
        db.add(SafetyAssignment(org_id=caller.org_id, record_id=draft.id, target_kind=rule.target_kind, target_id=rule.target_id))
    _event(db, draft, caller.user.id, "created", new_version_of=current.id)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="swms.new_version_started",
          target_type="swms", target_id=draft.id, detail={"supersedes": current.id, "version_no": draft.version_no})
    db.commit()
    return _out(db, caller, SWMS, draft, detail=True)


@swms_router.get("/{record_id}/versions")
def versions(record_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Every version in this SWMS's chain, newest first."""
    _need(caller, "swms", "edit")
    record = _load(db, caller, SWMS, record_id)
    chain = {record.id: record}
    cursor = record
    while cursor.supersedes_id and cursor.supersedes_id not in chain:
        cursor = db.get(SafetyRecord, cursor.supersedes_id)
        if cursor is None or cursor.org_id != caller.org_id:
            break
        chain[cursor.id] = cursor
    frontier = [record.id]
    while frontier:
        later = db.scalars(select(SafetyRecord).where(SafetyRecord.org_id == caller.org_id, SafetyRecord.supersedes_id.in_(frontier),
                                                      SafetyRecord.deleted_at.is_(None))).all()
        frontier = [r.id for r in later if r.id not in chain]
        chain.update({r.id: r for r in later})
    ordered = sorted(chain.values(), key=lambda r: (-r.version_no, r.created_at))
    return {"count": len(ordered), "versions": [_out(db, caller, SWMS, r) for r in ordered]}


def _rules_out(db: Session, record: SafetyRecord) -> dict[str, Any]:
    out = []
    for rule in db.scalars(select(SafetyAssignment).where(SafetyAssignment.record_id == record.id)
                           .order_by(SafetyAssignment.target_kind, SafetyAssignment.target_id)).all():
        if rule.target_kind == "role":
            role = db.scalars(select(Role).where(Role.org_id == record.org_id, Role.role_id == rule.target_id)).first()
            label = role.name if role else rule.target_id
        elif rule.target_kind == "user":
            user = db.get(User, rule.target_id)
            label = user.name if user else "Unknown person"
        else:
            company = db.get(Company, rule.target_id)
            label = company.name if company else "Unknown company"
        out.append({"kind": rule.target_kind, "target_id": rule.target_id, "label": label})
    return {"record_id": record.id, "open_to_everyone": not out, "rules": out}


@swms_router.get("/{record_id}/assignments")
def get_assignments(record_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    _need(caller, "swms", "edit")
    return _rules_out(db, _load(db, caller, SWMS, record_id))


@swms_router.put("/{record_id}/assignments")
def put_assignments(record_id: str, body: AssignmentsIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Replace who this SWMS is assigned to (roles, named people, companies). Empty means everyone who can view SWMS."""
    _need(caller, "swms", "edit")
    record = _load(db, caller, SWMS, record_id)
    wanted: set[tuple[str, str]] = set()
    for rule in body.rules:
        if rule.kind == "role":
            ok = db.scalars(select(Role.id).where(Role.org_id == caller.org_id, Role.role_id == rule.target_id, Role.deleted_at.is_(None))).first()
        elif rule.kind == "user":
            user = db.get(User, rule.target_id)
            ok = user is not None and user.org_id == caller.org_id and user.deleted_at is None
        elif rule.kind == "company":
            company = db.get(Company, rule.target_id)
            ok = company is not None and company.org_id == caller.org_id
        else:
            raise HTTPException(422, "A rule's kind must be role, user or company")
        if not ok:
            raise HTTPException(404, f"The {rule.kind} in a rule was not found")
        wanted.add((rule.kind, rule.target_id))
    existing = {(r.target_kind, r.target_id): r for r in db.scalars(select(SafetyAssignment).where(SafetyAssignment.record_id == record.id))}
    for key, row in existing.items():
        if key not in wanted:
            db.delete(row)
    for kind_key, target_id in wanted - set(existing):
        db.add(SafetyAssignment(org_id=caller.org_id, record_id=record.id, target_kind=kind_key, target_id=target_id))
    _event(db, record, caller.user.id, "assignments_changed", after=sorted(f"{k}:{t}" for k, t in wanted))
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="swms.assignments_changed", target_type="swms",
          target_id=record.id, detail={"after": sorted(f"{k}:{t}" for k, t in wanted)})
    db.commit()
    return _rules_out(db, record)


@swms_router.post("/{record_id}/acknowledge")
def acknowledge(record_id: str, body: AcknowledgeIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Sign off this exact version. The sign-off stays on this version when a newer one is approved."""
    _need(caller, "swms", "view")
    record = _load(db, caller, SWMS, record_id)
    if record.status != "approved":
        raise HTTPException(409, "Only an approved SWMS can be acknowledged")
    if record.superseded_by_id is not None:
        raise HTTPException(409, "A newer version of this SWMS is in force. Acknowledge that one.")
    existing = db.scalars(select(SafetyAcknowledgement).where(
        SafetyAcknowledgement.record_id == record.id, SafetyAcknowledgement.user_id == caller.user.id)).first()
    if existing is None:
        db.add(SafetyAcknowledgement(org_id=caller.org_id, record_id=record.id, user_id=caller.user.id, signed_name=body.signed_name.strip()))
        _event(db, record, caller.user.id, "acknowledged", version_no=record.version_no)
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="swms.acknowledged", target_type="swms",
              target_id=record.id, detail={"version_no": record.version_no})
        db.commit()
    return _out(db, caller, SWMS, record, detail=True)


@swms_router.get("/{record_id}/acknowledgements")
def acknowledgements(record_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    _need(caller, "swms", "edit")
    record = _load(db, caller, SWMS, record_id)
    rows = db.execute(
        select(SafetyAcknowledgement, User.name).outerjoin(User, User.id == SafetyAcknowledgement.user_id)
        .where(SafetyAcknowledgement.record_id == record.id).order_by(SafetyAcknowledgement.created_at)
    ).all()
    return {"record_id": record.id, "version_no": record.version_no, "count": len(rows), "acknowledgements": [
        {"user_id": a.user_id, "user_name": name, "signed_name": a.signed_name, "at": iso(a.created_at)} for a, name in rows]}
