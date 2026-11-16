"""Running the Simpro import and answering its review queue (stage 5b)."""
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import integrations, simpro
from ..db import get_db
from ..deps import Caller, require_permission
from ..models import ImportReviewItem, ImportRun, User, utcnow
from ..services import audit, iso

router = APIRouter(prefix="/api/integrations/simpro", tags=["integrations"])

RESOURCE_PERMISSION = {"workers": "workers", "sites": "sites", "suppliers": "suppliers"}
LABELS = {"workers": "Workers", "sites": "Sites", "suppliers": "Suppliers"}


class SyncIn(BaseModel):
    resource: Literal["workers", "sites", "suppliers"]
    dry_run: bool = True


class ResolveIn(BaseModel):
    action: Literal["link", "create", "ignore"]


def _run_out(run: ImportRun, names: dict[str, str] | None = None) -> dict[str, Any]:
    return {"id": run.id, "resource": run.resource, "resource_label": LABELS[run.resource], "mode": run.mode, "simulated": run.mode == "simulator",
            "dry_run": run.dry_run, "status": run.status, "counts": run.counts or {}, "companies": run.companies or [], "error": run.error,
            "started_by": (names or {}).get(run.started_by or ""), "started_at": iso(run.started_at), "finished_at": iso(run.finished_at)}


def _config(db: Session, caller: Caller):
    config = integrations.get_config(db, caller.org_id, "simpro")
    if config is None or config.mode == "off":
        raise HTTPException(409, "Simpro is not set up. Set it up under Connections, as Simulator or Live.")
    if config.mode == "live":
        kind = integrations.CATALOGUE["simpro"]
        missing = integrations.missing_required(kind, config)
        if missing:
            raise HTTPException(409, "Simpro details are missing: " + ", ".join(missing) + ".")
        if config.last_test_ok is not True or config.last_test_mode != "live":
            raise HTTPException(409, "Test the Simpro connection successfully before importing.")
    return config


@router.get("/companies")
def companies(caller: Caller = Depends(require_permission("integrations", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The companies seen by the last connection test, and the ones chosen for import."""
    config = integrations.get_config(db, caller.org_id, "simpro")
    settings = integrations.effective_settings(integrations.CATALOGUE["simpro"], config)
    caps = (config.capabilities if config else None) or {}
    return {"selected": [str(c) for c in (settings.get("company_ids") or [])], "found": caps.get("companies_found") or [], "missing": caps.get("companies_missing") or []}


@router.get("/last-synced")
def last_synced(caller: Caller = Depends(require_permission("integrations", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Per kind of record: the last import that was actually committed. A preview never counts."""
    out: dict[str, Any] = {}
    for resource in simpro.RESOURCES:
        run = db.scalars(select(ImportRun).where(ImportRun.org_id == caller.org_id, ImportRun.provider == "simpro", ImportRun.resource == resource,
                                                 ImportRun.status.in_(("committed", "partial"))).order_by(ImportRun.started_at.desc())).first()
        out[resource] = _run_out(run) if run else None
    open_items = db.scalars(select(ImportReviewItem).where(ImportReviewItem.org_id == caller.org_id, ImportReviewItem.status == "open")).all()
    return {"resources": out, "open_review": len(open_items)}


@router.get("/runs")
def runs(caller: Caller = Depends(require_permission("integrations", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(ImportRun).where(ImportRun.org_id == caller.org_id, ImportRun.provider == "simpro").order_by(ImportRun.started_at.desc()).limit(30)).all()
    ids = {r.started_by for r in rows if r.started_by}
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}
    return {"items": [_run_out(r, names) for r in rows]}


@router.post("/sync")
def sync(body: SyncIn, request: Request, caller: Caller = Depends(require_permission("integrations", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Fetch one kind of record from Simpro. With `dry_run` (the default) nothing is saved: the
    answer says what an import would create, update and question. Needs edit access to that kind
    of record as well as to connections."""
    if not caller.can(RESOURCE_PERMISSION[body.resource], "edit"):
        raise HTTPException(403, f"Importing {body.resource} also needs edit access to {body.resource}")
    config = _config(db, caller)
    try:
        run, detail = simpro.run(db, caller.org_id, config, body.resource, dry_run=body.dry_run, user_id=caller.user.id)
        if body.dry_run:
            # Keep the record of the preview, discard everything else it touched.
            snapshot = _run_out(run)
            db.rollback()
            db.add(ImportRun(org_id=caller.org_id, provider="simpro", resource=run.resource, mode=snapshot["mode"], dry_run=True, status=snapshot["status"],
                             counts=snapshot["counts"], companies=snapshot["companies"], error=snapshot["error"], started_by=caller.user.id, finished_at=utcnow()))
            db.commit()
            return {**snapshot, **detail}
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="import.committed", target_type="import_run", target_id=run.id,
              detail={"resource": run.resource, "mode": run.mode, "status": run.status, "counts": run.counts})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Another import changed the same records at the same time. Nothing was saved; run it again.") from None
    return {**_run_out(run), **detail}


def _review_out(db: Session, item: ImportReviewItem) -> dict[str, Any]:
    model = simpro.MODELS[item.resource]
    candidate = db.get(model, item.candidate_id) if item.candidate_id else None
    incoming = dict(item.incoming or {})
    shown = [f for f in simpro.FIELDS[item.resource] if f != "active"]
    return {
        "id": item.id, "resource": item.resource, "resource_label": LABELS[item.resource], "simulated": item.source_system == "simulator",
        "source_company": item.source_company, "match_on": item.match_on, "status": item.status,
        "incoming": {"name": simpro._label(item.resource, incoming), **{f: incoming.get(f) for f in shown}},
        "candidate": ({"id": candidate.id, "name": simpro._label(item.resource, {f: getattr(candidate, f, None) for f in ("first_name", "last_name", "name")}),
                       **{f: getattr(candidate, f) for f in shown}, "available": candidate.deleted_at is None and candidate.source_system is None}
                      if candidate is not None and candidate.org_id == item.org_id else None),
        "created_at": iso(item.created_at),
    }


@router.get("/review")
def review_queue(status: Literal["open", "linked", "created", "ignored"] = "open",
                 caller: Caller = Depends(require_permission("integrations", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Incoming records that look like someone already on file. Only kinds the caller may view are listed."""
    rows = db.scalars(select(ImportReviewItem).where(ImportReviewItem.org_id == caller.org_id, ImportReviewItem.status == status)
                      .order_by(ImportReviewItem.created_at)).all()
    return {"items": [_review_out(db, r) for r in rows if caller.can(RESOURCE_PERMISSION[r.resource], "view")]}


@router.post("/review/{item_id}/resolve")
def resolve(item_id: str, body: ResolveIn, request: Request, caller: Caller = Depends(require_permission("integrations", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Answer one question: it is the same record (link), it is a different one (create), or leave it out (ignore)."""
    item = db.get(ImportReviewItem, item_id)
    if item is None or item.org_id != caller.org_id:
        raise HTTPException(404, "Review item not found")
    if not caller.can(RESOURCE_PERMISSION[item.resource], "edit"):
        raise HTTPException(403, f"This needs edit access to {item.resource}")
    if item.status != "open":
        raise HTTPException(409, "This has already been answered")
    model = simpro.MODELS[item.resource]
    taken = db.scalars(select(model).where(model.org_id == item.org_id, model.source_system == item.source_system,
                                           model.source_company == item.source_company, model.source_id == item.source_id)).first()
    if taken is not None:
        raise HTTPException(409, "This Simpro record is already linked to a record in the portal")
    result_id: str | None = None
    if body.action == "link":
        candidate = db.get(model, item.candidate_id) if item.candidate_id else None
        if candidate is None or candidate.org_id != item.org_id or candidate.deleted_at is not None:
            raise HTTPException(409, "The record it looked like no longer exists. Choose create or ignore.")
        if candidate.source_system is not None:
            raise HTTPException(409, "That record is already linked to a different Simpro record. Choose create or ignore.")
        simpro.link(db, item, candidate)
        item.status, result_id = "linked", candidate.id
    elif body.action == "create":
        row = simpro.create_from_review(db, item, caller.user.id)
        db.flush()
        item.status, result_id = "created", row.id
    else:
        item.status = "ignored"
    item.resolved_by, item.resolved_at = caller.user.id, utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"import.review_{item.status}", target_type=item.resource.rstrip("s"),
          target_id=result_id or item.id, detail={"review_item": item.id, "source_company": item.source_company, "source_id": item.source_id})
    db.commit()
    return {**_review_out(db, item), "result_id": result_id}
