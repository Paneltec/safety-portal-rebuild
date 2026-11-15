"""Form engine API: templates with immutable published versions, submissions and their files.

Permissions (decision D-023):
- managing templates needs `forms.use` (admin and HSEQ lead by default); deleting one needs `forms.delete`
- filling in a form needs `forms.edit`
- without `forms.team_view` a person sees only their own submissions
"""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import forms as engine
from ..db import get_db
from ..deps import Caller, require_permission
from ..files import file_exists, file_response, save_upload
from ..models import FormSubmission, FormSubmissionFile, FormTemplate, FormTemplateVersion, StoredFile, User, utcnow
from ..services import audit, iso

router = APIRouter(prefix="/api/forms", tags=["forms"])

IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
ATTACHMENT_TYPES = IMAGE_TYPES | {"application/pdf"}
MAX_FILES_PER_SUBMISSION = 100


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = "general"
    description: str | None = ""
    fields: list[dict[str, Any]] = Field(default_factory=list)
    required_certifications: list[str] = Field(default_factory=list)
    certification_gate_enabled: bool = False


class TemplatePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = None
    description: str | None = None
    fields: list[dict[str, Any]] | None = None
    required_certifications: list[str] | None = None
    certification_gate_enabled: bool | None = None


class ImportPayload(BaseModel):
    app: str | None = None
    exported_at: str | None = None
    version: int | None = None
    count: int | None = None
    templates: list[dict[str, Any]] = Field(max_length=200)


class FieldValue(BaseModel):
    id: str
    value: Any = None


class SubmissionIn(BaseModel):
    fields: list[FieldValue] = Field(default_factory=list)
    status: str = "submitted"  # "draft" keeps it editable; files can then be added before submitting
    client_submission_id: str | None = Field(default=None, min_length=8, max_length=64)
    launched_via: str | None = Field(default=None, max_length=40)
    source_scan_token: str | None = Field(default=None, max_length=200)
    source_asset_id: str | None = Field(default=None, max_length=100)


class SubmissionPatch(BaseModel):
    fields: list[FieldValue]
    expected_revision: int | None = None


# ---------- helpers ----------

def _schema(fields: Any) -> list[dict[str, Any]]:
    try:
        return engine.normalise_schema(fields)
    except engine.SchemaError as exc:
        raise HTTPException(422, str(exc)) from exc


def _category(value: str) -> str:
    if value not in engine.CATEGORIES:
        raise HTTPException(422, f"Unknown category. Use one of: {', '.join(engine.CATEGORIES)}")
    return value


def _template(db: Session, caller: Caller, template_id: str, *, include_deleted: bool = False) -> FormTemplate:
    template = db.get(FormTemplate, template_id)
    if template is None or template.org_id != caller.org_id or (template.deleted_at is not None and not include_deleted):
        raise HTTPException(404, "Form not found")
    return template


def _current_version(db: Session, template: FormTemplate) -> FormTemplateVersion | None:
    if template.published_version < 1:
        return None
    return db.scalars(
        select(FormTemplateVersion).where(
            FormTemplateVersion.template_id == template.id, FormTemplateVersion.version == template.published_version
        )
    ).first()


def _template_out(db: Session, template: FormTemplate, *, detail: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": template.id,
        "name": template.name,
        "category": template.category,
        "category_label": engine.CATEGORIES.get(template.category, template.category),
        "description": template.description,
        "status": template.status,
        "published_version": template.published_version,
        "has_unpublished_changes": template.has_unpublished_changes,
        "certification_gate_enabled": template.certification_gate_enabled,
        "required_certifications": list(template.required_certifications or []),
        "field_count": len([f for f in template.schema or [] if f.get("type") != "section_header"]),
        "updated_at": iso(template.updated_at),
        "created_at": iso(template.created_at),
    }
    if detail:
        version = _current_version(db, template)
        out["fields"] = template.schema or []
        out["published_fields"] = version.schema if version else None
    return out


def _can_see_all(caller: Caller) -> bool:
    return caller.can("forms", "team_view")


def _submission(db: Session, caller: Caller, submission_id: str) -> FormSubmission:
    """Load a submission in the caller's organisation and record scope. Anything else looks missing."""
    sub = db.get(FormSubmission, submission_id)
    if sub is None or sub.org_id != caller.org_id or sub.deleted_at is not None:
        raise HTTPException(404, "Submission not found")
    if sub.submitted_by != caller.user.id and not _can_see_all(caller):
        raise HTTPException(404, "Submission not found")
    return sub


def _files(db: Session, submission_id: str) -> list[tuple[FormSubmissionFile, StoredFile]]:
    return list(
        db.execute(
            select(FormSubmissionFile, StoredFile)
            .join(StoredFile, StoredFile.id == FormSubmissionFile.stored_file_id)
            .where(FormSubmissionFile.submission_id == submission_id)
            .order_by(FormSubmissionFile.created_at, FormSubmissionFile.id)
        ).all()
    )


def _files_by_field(db: Session, submission_id: str) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    for link, _stored in _files(db, submission_id):
        grouped.setdefault(link.field_id, set()).add(link.id)
    return grouped


def _submission_out(db: Session, sub: FormSubmission, *, detail: bool = False) -> dict[str, Any]:
    version = db.get(FormTemplateVersion, sub.template_version_id)
    author = db.get(User, sub.submitted_by)
    out: dict[str, Any] = {
        "id": sub.id,
        "template_id": sub.template_id,
        "template_name": version.name if version else "",
        "template_version": version.version if version else None,
        "category": version.category if version else None,
        "status": sub.status,
        "revision": sub.revision,
        "submitted_by": sub.submitted_by,
        "submitted_by_name": author.name if author else None,
        "created_at": iso(sub.created_at),
        "updated_at": iso(sub.updated_at),
        "submitted_at": iso(sub.submitted_at),
        "launched_via": sub.launched_via,
        "source_asset_id": sub.source_asset_id,
    }
    if detail:
        # The schema snapshot travels with the record, so it always renders as it was filled in.
        out["schema"] = version.schema if version else []
        out["values"] = sub.values or {}
        out["source_scan_token"] = sub.source_scan_token
        out["files"] = [
            {"id": link.id, "field_id": link.field_id, "kind": link.kind, "name": stored.original_name,
             "mime_type": stored.mime_type, "size_bytes": stored.size_bytes, "sha256": stored.sha256,
             "missing": not file_exists(stored)}
            for link, stored in _files(db, sub.id)
        ]
    return out


def _values(items: list[FieldValue]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in items:
        if item.id in values:
            raise HTTPException(422, f"Field '{item.id}' is answered twice")
        values[item.id] = item.value
    return values


def _apply(db: Session, sub: FormSubmission, values: dict[str, Any], *, final: bool) -> None:
    version = db.get(FormTemplateVersion, sub.template_version_id)
    cleaned, errors = engine.validate_values(version.schema, values, final=final, files_by_field=_files_by_field(db, sub.id))
    if errors:
        raise HTTPException(422, {"message": "Some answers need attention", "errors": errors})
    sub.values = cleaned


# ---------- catalogue ----------

@router.get("/field-types")
def field_types(caller: Caller = Depends(require_permission("forms", "view"))) -> dict[str, Any]:
    """The single definition of field families, shared by the office and phone apps."""
    return {"field_types": engine.field_type_catalogue(),
            "categories": [{"key": k, "label": v} for k, v in engine.CATEGORIES.items()]}


# ---------- pickers ----------

@router.get("/pickers/workers")
def pick_workers(
    q: str | None = Query(None, max_length=100),
    caller: Caller = Depends(require_permission("forms", "view")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """People who can be chosen in a worker field. Until the worker register is built (stage 4)
    this lists active user accounts; the value shape {id, label} stays the same afterwards."""
    stmt = select(User).where(User.org_id == caller.org_id, User.status == "active", User.deleted_at.is_(None))
    if q and q.strip():
        stmt = stmt.where(func.lower(User.name).like(f"%{q.strip().lower()}%"))
    rows = db.scalars(stmt.order_by(func.lower(User.name), User.id).limit(100)).all()
    return {"available": True, "source": "users", "items": [{"id": u.id, "label": u.name} for u in rows]}


def _not_yet(what: str, stage: str) -> dict[str, Any]:
    # An explicit unavailable state: never an invented list.
    return {"available": False, "source": None, "items": [], "message": f"{what} can be chosen once {stage} is built."}


@router.get("/pickers/sites")
def pick_sites(caller: Caller = Depends(require_permission("forms", "view"))) -> dict[str, Any]:
    return _not_yet("Sites", "the sites register (stage 4)")


@router.get("/pickers/jobs")
def pick_jobs(caller: Caller = Depends(require_permission("forms", "view"))) -> dict[str, Any]:
    return _not_yet("Jobs", "daily jobs (stage 4)")


@router.get("/fleet/vehicles")
def pick_vehicles(caller: Caller = Depends(require_permission("forms", "view"))) -> dict[str, Any]:
    return {**_not_yet("Navixy vehicles", "the Navixy connection (stages 5 and 6)"), "manual_entry_allowed": True}


# ---------- templates ----------

@router.get("/templates")
def list_templates(
    category: str | None = None,
    status: str | None = None,
    include_archived: bool = False,
    caller: Caller = Depends(require_permission("forms", "view")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """People who cannot manage templates only see published forms."""
    stmt = select(FormTemplate).where(FormTemplate.org_id == caller.org_id, FormTemplate.deleted_at.is_(None))
    manager = caller.can("forms", "use")
    if not manager:
        stmt = stmt.where(FormTemplate.status == "published")
    elif status:
        stmt = stmt.where(FormTemplate.status == status)
    elif not include_archived:
        stmt = stmt.where(FormTemplate.status != "archived")
    if category:
        stmt = stmt.where(FormTemplate.category == category)
    rows = db.scalars(stmt.order_by(FormTemplate.category, func.lower(FormTemplate.name), FormTemplate.id)).all()
    return {"count": len(rows), "templates": [_template_out(db, t) for t in rows]}


@router.post("/templates", status_code=201)
def create_template(
    body: TemplateIn,
    request: Request,
    caller: Caller = Depends(require_permission("forms", "use")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    template = FormTemplate(
        org_id=caller.org_id, name=body.name.strip(), category=_category(body.category), description=body.description or "",
        schema=_schema(body.fields), certification_gate_enabled=body.certification_gate_enabled,
        required_certifications=list(dict.fromkeys(body.required_certifications)), created_by=caller.user.id,
    )
    db.add(template)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="form_template.created",
          target_type="form_template", target_id=template.id, detail={"name": template.name, "category": template.category})
    db.commit()
    return _template_out(db, template, detail=True)


@router.get("/templates/export")
def export_templates(
    caller: Caller = Depends(require_permission("forms", "use")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Definitions only. Answers are never part of a template export."""
    rows = db.scalars(
        select(FormTemplate).where(FormTemplate.org_id == caller.org_id, FormTemplate.deleted_at.is_(None))
        .order_by(func.lower(FormTemplate.name), FormTemplate.id)
    ).all()
    templates = [
        {"name": t.name, "category": t.category, "description": t.description, "fields": t.schema or [],
         "required_certifications": list(t.required_certifications or []),
         "certification_gate_enabled": t.certification_gate_enabled}
        for t in rows
    ]
    return {"app": "safety-portal", "exported_at": datetime.now(timezone.utc).isoformat(), "version": 1,
            "count": len(templates), "templates": templates}


@router.post("/templates/import", status_code=201)
def import_templates(
    body: ImportPayload,
    request: Request,
    caller: Caller = Depends(require_permission("forms", "use")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create draft templates from an export. Nothing is published, and a bad entry is reported, not half-imported."""
    existing = {
        n.lower() for n in db.scalars(
            select(FormTemplate.name).where(FormTemplate.org_id == caller.org_id, FormTemplate.deleted_at.is_(None))
        )
    }
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, raw in enumerate(body.templates, start=1):
        try:
            item = TemplateIn.model_validate(raw)
            schema = engine.normalise_schema(item.fields)
            category = item.category if item.category in engine.CATEGORIES else "general"
        except Exception as exc:  # noqa: BLE001 - reported per entry
            errors.append({"index": index, "name": str(raw.get("name", ""))[:200], "error": str(exc)[:300]})
            continue
        name = item.name.strip()
        if name.lower() in existing:
            name = f"{name} (imported)"[:200]
        existing.add(name.lower())
        template = FormTemplate(org_id=caller.org_id, name=name, category=category, description=item.description or "",
                                schema=schema, created_by=caller.user.id)
        db.add(template)
        db.flush()
        created.append({"id": template.id, "name": template.name})
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="form_template.imported",
          target_type="form_template", target_id=None, detail={"created": len(created), "errors": len(errors)})
    db.commit()
    return {"created": created, "errors": errors}


@router.get("/templates/{template_id}")
def get_template(
    template_id: str, caller: Caller = Depends(require_permission("forms", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    template = _template(db, caller, template_id)
    if template.status != "published" and not caller.can("forms", "use"):
        raise HTTPException(404, "Form not found")
    out = _template_out(db, template, detail=True)
    if not caller.can("forms", "use"):
        out["fields"] = out["published_fields"]  # never show unpublished edits to people filling in forms
    return out


@router.patch("/templates/{template_id}")
def patch_template(
    template_id: str,
    body: TemplatePatch,
    request: Request,
    caller: Caller = Depends(require_permission("forms", "use")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Edit the working draft. Published versions and existing submissions are not touched."""
    template = _template(db, caller, template_id)
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(400, "No fields to update")
    changed: list[str] = []
    if patch.get("name") is not None and patch["name"].strip() != template.name:
        template.name = patch["name"].strip()
        changed.append("name")
    if patch.get("category") is not None and patch["category"] != template.category:
        template.category = _category(patch["category"])
        changed.append("category")
    if patch.get("description") is not None and patch["description"] != template.description:
        template.description = patch["description"]
        changed.append("description")
    if patch.get("fields") is not None:
        schema = _schema(patch["fields"])
        if schema != template.schema:
            template.schema = schema
            changed.append("fields")
    if patch.get("certification_gate_enabled") is not None and patch["certification_gate_enabled"] != template.certification_gate_enabled:
        template.certification_gate_enabled = patch["certification_gate_enabled"]
        changed.append("certification_gate_enabled")
    if patch.get("required_certifications") is not None:
        certs = list(dict.fromkeys(patch["required_certifications"]))
        if certs != list(template.required_certifications or []):
            template.required_certifications = certs
            changed.append("required_certifications")
    if changed:
        if {"name", "category", "fields"} & set(changed):
            template.has_unpublished_changes = True
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="form_template.updated",
              target_type="form_template", target_id=template.id, detail={"changed": changed})
        db.commit()
    return _template_out(db, template, detail=True)


@router.post("/templates/{template_id}/publish")
def publish_template(
    template_id: str,
    request: Request,
    caller: Caller = Depends(require_permission("forms", "use")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Freeze the draft as the next version. New submissions use it; earlier ones keep their own version."""
    template = _template(db, caller, template_id)
    if not [f for f in template.schema or [] if f.get("type") != "section_header"]:
        raise HTTPException(400, "Add at least one field before publishing")
    if template.published_version >= 1 and not template.has_unpublished_changes and template.status == "published":
        return _template_out(db, template, detail=True)
    if template.has_unpublished_changes or template.published_version < 1:
        template.published_version += 1
        db.add(FormTemplateVersion(org_id=caller.org_id, template_id=template.id, version=template.published_version,
                                   name=template.name, category=template.category, schema=template.schema,
                                   published_by=caller.user.id))
    template.status = "published"
    template.has_unpublished_changes = False
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="form_template.published",
          target_type="form_template", target_id=template.id, detail={"version": template.published_version})
    db.commit()
    return _template_out(db, template, detail=True)


@router.post("/templates/{template_id}/archive")
def archive_template(
    template_id: str,
    request: Request,
    caller: Caller = Depends(require_permission("forms", "use")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Stop new submissions. Existing submissions stay readable."""
    template = _template(db, caller, template_id)
    if template.status != "archived":
        template.status = "archived"
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="form_template.archived",
              target_type="form_template", target_id=template.id)
        db.commit()
    return _template_out(db, template, detail=True)


@router.delete("/templates/{template_id}")
def delete_template(
    template_id: str,
    request: Request,
    caller: Caller = Depends(require_permission("forms", "delete")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Remove a template from use. Its submissions and their version snapshots are kept."""
    template = _template(db, caller, template_id)
    template.deleted_at = utcnow()
    template.status = "archived"
    kept = db.scalar(select(func.count()).select_from(FormSubmission).where(FormSubmission.template_id == template.id)) or 0
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="form_template.deleted",
          target_type="form_template", target_id=template.id, detail={"name": template.name, "submissions_kept": int(kept)})
    db.commit()
    return {"ok": True, "submissions_kept": int(kept)}


@router.get("/templates/{template_id}/access-check")
def access_check(
    template_id: str, caller: Caller = Depends(require_permission("forms", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Whether the caller may fill in this form right now, with a plain reason when not."""
    template = db.get(FormTemplate, template_id)
    if template is None or template.org_id != caller.org_id or template.deleted_at is not None:
        raise HTTPException(404, "Form not found")
    if template.status != "published":
        return {"allowed": False, "reason": "not_published", "message": "This form is not available at the moment."}
    if not caller.can("forms", "edit"):
        return {"allowed": False, "reason": "no_permission", "message": "Your role cannot fill in forms."}
    return {"allowed": True, "reason": None, "message": None, "template_version": template.published_version}


# ---------- submissions ----------

@router.post("/templates/{template_id}/submissions", status_code=201)
def create_submission(
    template_id: str,
    body: SubmissionIn,
    request: Request,
    caller: Caller = Depends(require_permission("forms", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Start or send a form. Sending the same client_submission_id again returns the first result
    instead of creating a duplicate, so an offline retry is safe."""
    if body.status not in ("draft", "submitted"):
        raise HTTPException(422, "status must be draft or submitted")
    template = _template(db, caller, template_id)
    version = _current_version(db, template)
    if template.status != "published" or version is None:
        raise HTTPException(409, "This form is not available at the moment")
    if body.client_submission_id:
        existing = db.scalars(
            select(FormSubmission).where(
                FormSubmission.org_id == caller.org_id, FormSubmission.submitted_by == caller.user.id,
                FormSubmission.client_submission_id == body.client_submission_id,
            )
        ).first()
        if existing is not None:
            return {**_submission_out(db, existing, detail=True), "duplicate": True}
    sub = FormSubmission(
        org_id=caller.org_id, template_id=template.id, template_version_id=version.id, status="draft",
        submitted_by=caller.user.id, client_submission_id=body.client_submission_id, launched_via=body.launched_via,
        source_scan_token=body.source_scan_token, source_asset_id=body.source_asset_id,
    )
    db.add(sub)
    try:
        db.flush()
    except IntegrityError:
        # Two identical retries raced; the other one won.
        db.rollback()
        existing = db.scalars(
            select(FormSubmission).where(
                FormSubmission.org_id == caller.org_id, FormSubmission.submitted_by == caller.user.id,
                FormSubmission.client_submission_id == body.client_submission_id,
            )
        ).one()
        return {**_submission_out(db, existing, detail=True), "duplicate": True}
    final = body.status == "submitted"
    _apply(db, sub, _values(body.fields), final=final)
    if final:
        sub.status = "submitted"
        sub.submitted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id,
          action="form_submission.submitted" if final else "form_submission.draft_created",
          target_type="form_submission", target_id=sub.id, detail={"template_id": template.id, "version": version.version})
    db.commit()
    return {**_submission_out(db, sub, detail=True), "duplicate": False}


@router.get("/templates/{template_id}/submissions")
def list_submissions(
    template_id: str,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    caller: Caller = Depends(require_permission("forms", "view")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    template = _template(db, caller, template_id, include_deleted=True)
    stmt = select(FormSubmission).where(
        FormSubmission.org_id == caller.org_id, FormSubmission.template_id == template.id, FormSubmission.deleted_at.is_(None)
    )
    if not _can_see_all(caller):
        stmt = stmt.where(FormSubmission.submitted_by == caller.user.id)
    else:
        # Other people's unfinished drafts are their own business.
        stmt = stmt.where((FormSubmission.status != "draft") | (FormSubmission.submitted_by == caller.user.id))
    if status:
        stmt = stmt.where(FormSubmission.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(FormSubmission.created_at.desc(), FormSubmission.id).limit(limit).offset(offset)).all()
    return {"items": [_submission_out(db, s) for s in rows], "total": int(total), "limit": limit, "offset": offset,
            "scope": "team" if _can_see_all(caller) else "own"}


@router.get("/submissions/{submission_id}")
def get_submission(
    submission_id: str, caller: Caller = Depends(require_permission("forms", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return _submission_out(db, _submission(db, caller, submission_id), detail=True)


def _own_draft(db: Session, caller: Caller, submission_id: str) -> FormSubmission:
    sub = _submission(db, caller, submission_id)
    if sub.submitted_by != caller.user.id:
        raise HTTPException(403, "Only the person filling in a form can change it")
    if sub.status != "draft":
        raise HTTPException(409, "This form has been submitted and can no longer be changed")
    return sub


@router.patch("/submissions/{submission_id}")
def update_draft(
    submission_id: str,
    body: SubmissionPatch,
    caller: Caller = Depends(require_permission("forms", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Save a draft. The answers sent replace the stored answers."""
    sub = _own_draft(db, caller, submission_id)
    if body.expected_revision is not None and body.expected_revision != sub.revision:
        raise HTTPException(409, "This draft was changed somewhere else. Reload it before saving.")
    _apply(db, sub, _values(body.fields), final=False)
    sub.revision += 1
    db.commit()
    return _submission_out(db, sub, detail=True)


@router.post("/submissions/{submission_id}/submit")
def submit_draft(
    submission_id: str,
    request: Request,
    body: SubmissionPatch | None = None,
    caller: Caller = Depends(require_permission("forms", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Check every required answer and file, then lock the record."""
    sub = _submission(db, caller, submission_id)
    if sub.status == "submitted" and sub.submitted_by == caller.user.id:
        return _submission_out(db, sub, detail=True)  # a repeated submit is harmless
    sub = _own_draft(db, caller, submission_id)
    _apply(db, sub, _values(body.fields) if body is not None else dict(sub.values or {}), final=True)
    sub.status = "submitted"
    sub.submitted_at = utcnow()
    sub.revision += 1
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="form_submission.submitted",
          target_type="form_submission", target_id=sub.id, detail={"template_id": sub.template_id})
    db.commit()
    return _submission_out(db, sub, detail=True)


@router.delete("/submissions/{submission_id}")
def delete_submission(
    submission_id: str, request: Request, caller: Caller = Depends(require_permission("forms", "view")), db: Session = Depends(get_db)
) -> dict[str, bool]:
    """A person may discard their own draft. Removing a submitted record needs `forms.delete` and keeps an audit entry."""
    sub = _submission(db, caller, submission_id)
    own_draft = sub.status == "draft" and sub.submitted_by == caller.user.id
    if not own_draft and not caller.can("forms", "delete"):
        raise HTTPException(403, "You do not have permission to do this")
    sub.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="form_submission.deleted",
          target_type="form_submission", target_id=sub.id, detail={"status": sub.status})
    db.commit()
    return {"ok": True}


# ---------- submission files ----------

def _attach(db: Session, request: Request, caller: Caller, submission_id: str, field_id: str, upload: UploadFile) -> dict[str, Any]:
    sub = _own_draft(db, caller, submission_id)
    version = db.get(FormTemplateVersion, sub.template_version_id)
    field = next((f for f in version.schema if f["id"] == field_id), None)
    if field is None or not engine.FIELD_TYPES[field["type"]].get("file"):
        raise HTTPException(400, "This field does not take files")
    count = db.scalar(select(func.count()).select_from(FormSubmissionFile).where(FormSubmissionFile.submission_id == sub.id)) or 0
    if count >= MAX_FILES_PER_SUBMISSION:
        raise HTTPException(400, "This form already has the maximum number of files")
    kind = field["type"]
    allowed = ATTACHMENT_TYPES if kind == "attachment" else IMAGE_TYPES
    stored = save_upload(db, org_id=caller.org_id, upload=upload, purpose=f"form_{kind}", created_by=caller.user.id,
                         allowed_mimes=allowed)
    link = FormSubmissionFile(org_id=caller.org_id, submission_id=sub.id, field_id=field_id, kind=kind,
                              stored_file_id=stored.id, created_by=caller.user.id)
    db.add(link)
    db.commit()
    return {"id": link.id, "field_id": field_id, "kind": kind, "name": stored.original_name, "mime_type": stored.mime_type,
            "size_bytes": stored.size_bytes, "sha256": stored.sha256}


@router.post("/submissions/{submission_id}/files", status_code=201)
def upload_submission_file(
    submission_id: str,
    request: Request,
    field_id: str = Form(...),
    file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("forms", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Add a photo, attachment or signature to a draft. The returned id goes into that field's answer."""
    return _attach(db, request, caller, submission_id, field_id, file)


@router.post("/submissions/{submission_id}/photos", status_code=201)
def upload_photo(
    submission_id: str, request: Request, field_id: str = Form(...), file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("forms", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _attach(db, request, caller, submission_id, field_id, file)


@router.post("/submissions/{submission_id}/attachments", status_code=201)
def upload_attachment(
    submission_id: str, request: Request, field_id: str = Form(...), file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("forms", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _attach(db, request, caller, submission_id, field_id, file)


def _link(db: Session, sub: FormSubmission, file_id: str) -> FormSubmissionFile:
    link = db.get(FormSubmissionFile, file_id)
    if link is None or link.submission_id != sub.id:
        raise HTTPException(404, "File not found")
    return link


@router.get("/submissions/{submission_id}/files/{file_id}")
def download_submission_file(
    submission_id: str,
    file_id: str,
    download: int = Query(0, ge=0, le=1),
    caller: Caller = Depends(require_permission("forms", "view")),
    db: Session = Depends(get_db),
):
    """Files are only reachable through a submission the caller is allowed to see."""
    sub = _submission(db, caller, submission_id)
    link = _link(db, sub, file_id)
    stored = db.get(StoredFile, link.stored_file_id)
    if stored is None:
        raise HTTPException(410, "The stored file is missing. It needs to be uploaded again.")
    return file_response(stored, inline=download == 0)


@router.delete("/submissions/{submission_id}/files/{file_id}")
def remove_submission_file(
    submission_id: str, file_id: str, caller: Caller = Depends(require_permission("forms", "edit")), db: Session = Depends(get_db)
) -> dict[str, bool]:
    sub = _own_draft(db, caller, submission_id)
    link = _link(db, sub, file_id)
    # Drop the id from any answer that still refers to it.
    values = dict(sub.values or {})
    current = values.get(link.field_id)
    if isinstance(current, list):
        values[link.field_id] = [v for v in current if v != link.id]
    elif isinstance(current, dict) and current.get("file_id") == link.id:
        values.pop(link.field_id)
    sub.values = values
    db.delete(link)
    db.commit()
    return {"ok": True}
