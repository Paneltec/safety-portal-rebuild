"""Suppliers and contractors (stage 4c).

One directory. A supplier flagged `is_contractor` does work for the organisation and is expected to
keep its compliance documents current. Notes, tasks and members are local records in their own
tables, so an import can never erase them; fields edited by hand are remembered in
`locally_edited` so a later sync leaves them alone.

Who sees what:
  suppliers.view                         the whole directory
  contractors.view + team_view           contractors only
  contractors.view, login linked to one  that supplier only (an external representative)
"""
import re
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session

from .. import people
from ..db import get_db
from ..deps import Caller, get_caller, require_permission
from ..files import file_out, file_response, save_upload
from ..models import (
    ComplianceDocType,
    Organisation,
    StoredFile,
    Supplier,
    SupplierDocument,
    SupplierMember,
    SupplierNote,
    SupplierTask,
    User,
    utcnow,
)
from ..permissions import slugify
from ..services import audit, iso

router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])
contractors_router = APIRouter(prefix="/api/contractors", tags=["suppliers"])
doc_types_router = APIRouter(prefix="/api/renewals/doc-types", tags=["renewals"])

EVIDENCE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
SOURCE_FIELDS = ("name", "abn", "trade", "contact_name", "contact_email", "contact_phone", "address", "suburb", "state", "postcode")
DEFAULT_DOC_TYPES = [
    ("public_liability", "Public liability insurance", "Certificate of currency", True),
    ("workers_compensation", "Workers compensation insurance", "Certificate of currency", True),
    ("professional_indemnity", "Professional indemnity insurance", "Certificate of currency", False),
    ("motor_vehicle", "Motor vehicle insurance", None, False),
    ("licence", "Trade licence or registration", None, False),
    ("swms", "Safe work method statement", None, False),
    ("other", "Other document", None, False),
]


# ---------- bodies ----------

class SupplierIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    abn: str | None = Field(None, max_length=20)
    trade: str | None = Field(None, max_length=120)
    contact_name: str | None = Field(None, max_length=160)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(None, max_length=40)
    address: str | None = Field(None, max_length=300)
    suburb: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=40)
    postcode: str | None = Field(None, max_length=12)
    is_contractor: bool = False
    active: bool = True


class SupplierPatch(SupplierIn):
    name: str | None = Field(None, min_length=2, max_length=200)  # type: ignore[assignment]
    is_contractor: bool | None = None  # type: ignore[assignment]
    active: bool | None = None  # type: ignore[assignment]


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


class TaskIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str | None = Field(None, max_length=4000)
    due_date: date | None = None
    status: Literal["open", "in_progress", "done"] = "open"
    priority: Literal["low", "normal", "high"] = "normal"
    assigned_to: str | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(None, min_length=2, max_length=200)
    description: str | None = Field(None, max_length=4000)
    due_date: date | None = None
    status: Literal["open", "in_progress", "done"] | None = None
    priority: Literal["low", "normal", "high"] | None = None
    assigned_to: str | None = None


class MemberIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    role: str | None = Field(None, max_length=120)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=40)
    is_primary_contact: bool = False


class MemberPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    role: str | None = Field(None, max_length=120)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=40)
    is_primary_contact: bool | None = None


class LinkUserIn(BaseModel):
    user_id: str


class DocTypeIn(BaseModel):
    label: str = Field(min_length=2, max_length=160)
    description: str | None = Field(None, max_length=400)
    required_for_contractors: bool = False


class DocTypePatch(BaseModel):
    label: str | None = Field(None, min_length=2, max_length=160)
    description: str | None = Field(None, max_length=400)
    required_for_contractors: bool | None = None
    active: bool | None = None
    sort_order: int | None = Field(None, ge=0, le=1000)


# ---------- shared helpers (also used by renewals) ----------

def _text(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def clean_abn(value: str | None) -> str | None:
    """An ABN is 11 digits with a published checksum. Reject anything else rather than store a typo."""
    if not value or not value.strip():
        return None
    digits = re.sub(r"\s", "", value)
    if not digits.isdigit() or len(digits) != 11:
        raise HTTPException(422, "An ABN has 11 digits")
    weights = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
    numbers = [int(d) for d in digits]
    numbers[0] -= 1
    if sum(n * w for n, w in zip(numbers, weights)) % 89 != 0:
        raise HTTPException(422, "That ABN is not valid. Check the digits.")
    return digits


def ensure_doc_types(db: Session, org_id: str) -> list[ComplianceDocType]:
    rows = db.scalars(select(ComplianceDocType).where(ComplianceDocType.org_id == org_id)).all()
    if not rows:
        for index, (slug, label, description, required) in enumerate(DEFAULT_DOC_TYPES):
            db.add(ComplianceDocType(org_id=org_id, slug=slug, label=label, description=description,
                                     required_for_contractors=required, sort_order=index))
        db.flush()
        rows = db.scalars(select(ComplianceDocType).where(ComplianceDocType.org_id == org_id)).all()
    return sorted(rows, key=lambda t: (t.sort_order, t.label.lower()))


def doc_type_labels(db: Session, org_id: str) -> dict[str, str]:
    return {t.slug: t.label for t in ensure_doc_types(db, org_id)}


def can_see(caller: Caller, supplier: Supplier) -> bool:
    if caller.can("suppliers", "view"):
        return True
    if not caller.can("contractors", "view"):
        return False
    if caller.user.supplier_id:
        return caller.user.supplier_id == supplier.id
    return caller.can("contractors", "team_view") and supplier.is_contractor


def can_edit(caller: Caller, supplier: Supplier) -> bool:
    if caller.can("suppliers", "edit"):
        return True
    return caller.can("contractors", "edit") and supplier.is_contractor and can_see(caller, supplier)


def get_supplier(db: Session, caller: Caller, supplier_id: str, *, include_deleted: bool = False) -> Supplier:
    supplier = db.get(Supplier, supplier_id)
    if supplier is None or supplier.org_id != caller.org_id or (supplier.deleted_at is not None and not include_deleted):
        raise HTTPException(404, "Supplier not found")
    if not can_see(caller, supplier):
        raise HTTPException(404, "Supplier not found")
    return supplier


def _editable(db: Session, caller: Caller, supplier_id: str) -> Supplier:
    supplier = get_supplier(db, caller, supplier_id)
    if not can_edit(caller, supplier):
        raise HTTPException(403, "You do not have permission to do this")
    return supplier


def _status(supplier: Supplier) -> str:
    if supplier.deleted_at is not None:
        return "deleted"
    if supplier.archived_at is not None:
        return "archived"
    return "active" if supplier.active else "inactive"


def current_documents(db: Session, supplier_id: str) -> list[SupplierDocument]:
    return list(db.scalars(select(SupplierDocument).where(
        SupplierDocument.supplier_id == supplier_id, SupplierDocument.superseded_by_id.is_(None), SupplierDocument.deleted_at.is_(None))).all())


def compliance_summary(db: Session, org: Organisation, supplier: Supplier) -> dict[str, Any]:
    window = people.cert_settings(org)["expiry_window_days"]
    today = people.today_for(org)
    counts = {"valid": 0, "expiring": 0, "expired": 0, "unknown": 0}
    held: set[str] = set()
    for doc in current_documents(db, supplier.id):
        counts[people.cert_status(doc, today, window)[0]] += 1  # type: ignore[arg-type]
        held.add(doc.doc_type)
    missing = []
    if supplier.is_contractor:
        missing = [{"slug": t.slug, "label": t.label} for t in ensure_doc_types(db, org.id)
                   if t.active and t.required_for_contractors and t.slug not in held]
    return {**counts, "missing": missing}


def supplier_out(db: Session, caller: Caller, supplier: Supplier, *, detail: bool = False) -> dict[str, Any]:
    org = db.get(Organisation, supplier.org_id)
    out: dict[str, Any] = {
        "id": supplier.id, "name": supplier.name, "abn": supplier.abn, "trade": supplier.trade,
        "contact_name": supplier.contact_name, "contact_email": supplier.contact_email, "contact_phone": supplier.contact_phone,
        "address": supplier.address, "suburb": supplier.suburb, "state": supplier.state, "postcode": supplier.postcode,
        "address_full": ", ".join(x for x in (supplier.address, supplier.suburb, supplier.state, supplier.postcode) if x),
        "is_contractor": supplier.is_contractor, "active": supplier.active, "status": _status(supplier),
        "source": ({"system": supplier.source_system, "company": supplier.source_company, "id": supplier.source_id}
                   if supplier.source_system else None),
        "compliance": compliance_summary(db, org, supplier),
        "can_edit": can_edit(caller, supplier),
        "updated_at": iso(supplier.updated_at), "archived_at": iso(supplier.archived_at), "deleted_at": iso(supplier.deleted_at),
    }
    if detail:
        out["locally_edited"] = list(supplier.locally_edited or [])
        if caller.can("users", "view"):
            logins = db.scalars(select(User).where(User.supplier_id == supplier.id, User.deleted_at.is_(None)).order_by(func.lower(User.name))).all()
            out["logins"] = [{"id": u.id, "name": u.name, "email": u.email, "status": u.status, "role_id": u.role_id} for u in logins]
    return out


def document_out(db: Session, doc: SupplierDocument, labels: dict[str, str], today: date, window: int) -> dict[str, Any]:
    state, days = people.cert_status(doc, today, window)  # type: ignore[arg-type]
    stored = db.get(StoredFile, doc.file_id)
    return {
        "id": doc.id, "supplier_id": doc.supplier_id,
        "doc_type": {"slug": doc.doc_type, "label": labels.get(doc.doc_type, doc.doc_type)},
        "reference": doc.reference,
        "issue_date": doc.issue_date.isoformat() if doc.issue_date else None,
        "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None,
        "no_expiry": doc.no_expiry, "status": state, "status_label": people.STATUS_LABELS[state], "days_left": days,
        "notes": doc.notes, "source": doc.source, "current": doc.superseded_by_id is None,
        "file": file_out(stored) if stored else None,
        "file_url": f"/api/suppliers/{doc.supplier_id}/documents/{doc.id}/file",
        "created_at": iso(doc.created_at),
    }


def add_document(db: Session, *, supplier: Supplier, doc_type: str, file_id: str, reference: str | None, issue_date: date | None,
                 expiry_date: date | None, no_expiry: bool, notes: str | None, source: str, created_by: str | None,
                 renewal_id: str | None = None) -> SupplierDocument:
    """Store a compliance document. The previous current one of the same type is kept as history."""
    doc = SupplierDocument(org_id=supplier.org_id, supplier_id=supplier.id, doc_type=doc_type, file_id=file_id, reference=reference,
                           issue_date=issue_date, expiry_date=expiry_date, no_expiry=no_expiry, notes=notes, source=source,
                           renewal_id=renewal_id, created_by=created_by)
    db.add(doc)
    db.flush()
    for old in current_documents(db, supplier.id):
        if old.id != doc.id and old.doc_type == doc_type:
            old.superseded_by_id = doc.id
    return doc


# ---------- directory ----------

def _list(db: Session, caller: Caller, *, q: str | None, status: str, kind: str, limit: int, offset: int) -> dict[str, Any]:
    stmt = select(Supplier).where(Supplier.org_id == caller.org_id, Supplier.deleted_at.is_(None))
    if not caller.can("suppliers", "view"):
        if caller.user.supplier_id:
            stmt = stmt.where(Supplier.id == caller.user.supplier_id)
        elif caller.can("contractors", "team_view"):
            stmt = stmt.where(Supplier.is_contractor.is_(True))
        else:
            stmt = stmt.where(Supplier.id == "")
    if kind == "contractor":
        stmt = stmt.where(Supplier.is_contractor.is_(True))
    base = stmt
    if status == "active":
        stmt = stmt.where(Supplier.archived_at.is_(None), Supplier.active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(Supplier.archived_at.is_(None), Supplier.active.is_(False))
    elif status == "archived":
        stmt = stmt.where(Supplier.archived_at.is_not(None))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Supplier.name).like(like), func.lower(func.coalesce(Supplier.trade, "")).like(like),
                              func.lower(func.coalesce(Supplier.contact_name, "")).like(like), func.coalesce(Supplier.abn, "").like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(func.lower(Supplier.name), Supplier.id).limit(limit).offset(offset)).all()

    def count(*conds: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(base.where(*conds).subquery())) or 0)

    return {
        "items": [supplier_out(db, caller, s) for s in rows], "total": int(total), "limit": limit, "offset": offset,
        "counts": {"active": count(Supplier.archived_at.is_(None), Supplier.active.is_(True)),
                   "inactive": count(Supplier.archived_at.is_(None), Supplier.active.is_(False)),
                   "archived": count(Supplier.archived_at.is_not(None))},
    }


def _viewer(caller: Caller = Depends(get_caller)) -> Caller:
    if not (caller.can("suppliers", "view") or caller.can("contractors", "view")):
        raise HTTPException(403, "You do not have permission to do this")
    return caller


@router.get("")
def list_suppliers(
    q: str | None = Query(None, max_length=100),
    status: Literal["active", "inactive", "archived", "all"] = "active",
    kind: Literal["all", "contractor"] = "all",
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    caller: Caller = Depends(_viewer), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _list(db, caller, q=q, status=status, kind=kind, limit=limit, offset=offset)


@contractors_router.get("")
def list_contractors(
    q: str | None = Query(None, max_length=100),
    status: Literal["active", "inactive", "archived", "all"] = "active",
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    caller: Caller = Depends(require_permission("contractors", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """The same directory, contractors only."""
    return _list(db, caller, q=q, status=status, kind="contractor", limit=limit, offset=offset)


@contractors_router.get("/{supplier_id}")
def get_contractor(
    supplier_id: str, caller: Caller = Depends(require_permission("contractors", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    supplier = get_supplier(db, caller, supplier_id)
    if not supplier.is_contractor:
        raise HTTPException(404, "Supplier not found")
    return supplier_out(db, caller, supplier, detail=True)


def _apply(supplier: Supplier, data: dict[str, Any]) -> list[str]:
    changed = []
    for key, value in data.items():
        if key == "abn":
            value = clean_abn(value)
        elif key == "contact_email":
            value = str(value).strip().lower() if value else None
        elif key in ("is_contractor", "active"):
            if value is None:
                continue
        elif key == "name":
            if value is None:
                continue
            value = value.strip()
        else:
            value = _text(value)
        if getattr(supplier, key) != value:
            setattr(supplier, key, value)
            changed.append(key)
    return changed


@router.post("", status_code=201)
def create_supplier(
    body: SupplierIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)
) -> dict[str, Any]:
    if not (caller.can("suppliers", "edit") or (body.is_contractor and caller.can("contractors", "edit") and caller.can("contractors", "team_view"))):
        raise HTTPException(403, "You do not have permission to do this")
    supplier = Supplier(org_id=caller.org_id, name=body.name.strip(), created_by=caller.user.id)
    _apply(supplier, body.model_dump(exclude_unset=True))
    db.add(supplier)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.created",
          target_type="supplier", target_id=supplier.id, detail={"name": supplier.name, "is_contractor": supplier.is_contractor})
    db.commit()
    same = db.scalars(select(Supplier).where(
        Supplier.org_id == caller.org_id, Supplier.id != supplier.id, Supplier.deleted_at.is_(None),
        or_(func.lower(Supplier.name) == supplier.name.lower(), (Supplier.abn == supplier.abn) if supplier.abn else false())).limit(5)).all()
    return {**supplier_out(db, caller, supplier, detail=True),
            "possible_duplicates": [{"id": s.id, "name": s.name, "abn": s.abn} for s in same]}


@router.get("/panel-counts")
def panel_counts(caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Open tasks, notes and members per supplier, for the badges on the list."""
    out: dict[str, dict[str, int]] = {}

    def add(model: Any, key: str, *conds: Any) -> None:
        rows = db.execute(select(model.supplier_id, func.count()).where(model.org_id == caller.org_id, model.deleted_at.is_(None), *conds)
                          .group_by(model.supplier_id)).all()
        for sid, n in rows:
            out.setdefault(sid, {"open_tasks": 0, "notes": 0, "members": 0})[key] = int(n)

    add(SupplierTask, "open_tasks", SupplierTask.status != "done")
    add(SupplierNote, "notes")
    add(SupplierMember, "members")
    visible = {s.id for s in db.scalars(select(Supplier).where(Supplier.org_id == caller.org_id, Supplier.deleted_at.is_(None))).all() if can_see(caller, s)}
    return {"counts": {sid: counts for sid, counts in out.items() if sid in visible}}


@router.get("/{supplier_id}")
def get_one(supplier_id: str, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    return supplier_out(db, caller, get_supplier(db, caller, supplier_id), detail=True)


@router.patch("/{supplier_id}")
def update_supplier(
    supplier_id: str, body: SupplierPatch, request: Request, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)
) -> dict[str, Any]:
    supplier = _editable(db, caller, supplier_id)
    data = body.model_dump(exclude_unset=True)
    if "is_contractor" in data and not caller.can("suppliers", "edit"):
        raise HTTPException(403, "Only people who manage suppliers can change whether this is a contractor")
    changed = _apply(supplier, data)
    if changed:
        if supplier.source_system:
            # Remember what was changed by hand, so the next import leaves those fields alone.
            supplier.locally_edited = sorted(set(supplier.locally_edited or []) | (set(changed) & set(SOURCE_FIELDS)))
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.updated",
              target_type="supplier", target_id=supplier.id, detail={"fields": changed})
    db.commit()
    return supplier_out(db, caller, supplier, detail=True)


def _flag(action: str, permission: tuple[str, str]):
    def handler(supplier_id: str, request: Request, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
        if not caller.can(*permission):
            raise HTTPException(403, "You do not have permission to do this")
        supplier = get_supplier(db, caller, supplier_id, include_deleted=action == "restore")
        before = (supplier.archived_at, supplier.deleted_at)
        if action == "archive" and supplier.archived_at is None:
            supplier.archived_at = utcnow()
        elif action == "unarchive":
            supplier.archived_at = None
        elif action == "delete":
            supplier.deleted_at = utcnow()
            for user in db.scalars(select(User).where(User.supplier_id == supplier.id)).all():
                user.supplier_id = None  # the login is kept but no longer scoped to a deleted record
        elif action == "restore":
            supplier.deleted_at = None
        if before != (supplier.archived_at, supplier.deleted_at):
            audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"supplier.{action}d" if action.endswith("e") else f"supplier.{action}",
                  target_type="supplier", target_id=supplier.id)
            db.commit()
        return {"ok": True} if action == "delete" else supplier_out(db, caller, supplier, detail=True)

    return handler


router.add_api_route("/{supplier_id}/archive", _flag("archive", ("suppliers", "archive")), methods=["POST"])
router.add_api_route("/{supplier_id}/unarchive", _flag("unarchive", ("suppliers", "archive")), methods=["POST"])
router.add_api_route("/{supplier_id}", _flag("delete", ("suppliers", "delete")), methods=["DELETE"])
router.add_api_route("/{supplier_id}/restore", _flag("restore", ("suppliers", "delete")), methods=["POST"])


# ---------- external logins ----------

@router.post("/{supplier_id}/link-user")
def link_login(
    supplier_id: str, body: LinkUserIn, request: Request,
    caller: Caller = Depends(require_permission("users", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Tie a login to this supplier. With a contractor-representative role, that person then sees
    only this supplier."""
    supplier = get_supplier(db, caller, supplier_id)
    if not caller.can("suppliers", "edit"):
        raise HTTPException(403, "You do not have permission to do this")
    user = db.get(User, body.user_id)
    if user is None or user.org_id != caller.org_id or user.deleted_at is not None:
        raise HTTPException(404, "User not found")
    if user.supplier_id and user.supplier_id != supplier.id:
        raise HTTPException(409, "That login is already linked to another supplier. Unlink it there first.")
    user.supplier_id = supplier.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.user_linked",
          target_type="supplier", target_id=supplier.id, detail={"user_id": user.id})
    db.commit()
    return supplier_out(db, caller, supplier, detail=True)


@router.delete("/{supplier_id}/link-user/{user_id}")
def unlink_login(
    supplier_id: str, user_id: str, request: Request,
    caller: Caller = Depends(require_permission("users", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    supplier = get_supplier(db, caller, supplier_id)
    user = db.get(User, user_id)
    if user is not None and user.org_id == caller.org_id and user.supplier_id == supplier.id:
        user.supplier_id = None
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.user_unlinked",
              target_type="supplier", target_id=supplier.id, detail={"user_id": user.id})
        db.commit()
    return supplier_out(db, caller, supplier, detail=True)


# ---------- compliance documents ----------

def _doc_ctx(db: Session, caller: Caller) -> tuple[dict[str, str], date, int]:
    org = db.get(Organisation, caller.org_id)
    return doc_type_labels(db, org.id), people.today_for(org), people.cert_settings(org)["expiry_window_days"]


def check_dates(issue: date | None, expiry: date | None, never: bool) -> None:
    if never and expiry is not None:
        raise HTTPException(422, "Leave the expiry date empty for a document that does not expire")
    if issue is not None and expiry is not None and expiry < issue:
        raise HTTPException(422, "The expiry date is before the issue date")


@router.get("/{supplier_id}/documents")
def list_documents(
    supplier_id: str, history: bool = False, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)
) -> dict[str, Any]:
    supplier = get_supplier(db, caller, supplier_id)
    labels, today, window = _doc_ctx(db, caller)
    stmt = select(SupplierDocument).where(SupplierDocument.supplier_id == supplier.id, SupplierDocument.deleted_at.is_(None))
    if not history:
        stmt = stmt.where(SupplierDocument.superseded_by_id.is_(None))
    rows = db.scalars(stmt.order_by(SupplierDocument.doc_type, SupplierDocument.created_at.desc())).all()
    org = db.get(Organisation, caller.org_id)
    return {"items": [document_out(db, d, labels, today, window) for d in rows],
            "missing": compliance_summary(db, org, supplier)["missing"]}


@router.post("/{supplier_id}/documents", status_code=201)
def upload_document(
    supplier_id: str, request: Request,
    doc_type: str = Form(...), file: UploadFile = File(...), reference: str | None = Form(None),
    issue_date: date | None = Form(None), expiry_date: date | None = Form(None), no_expiry: bool = Form(False),
    notes: str | None = Form(None),
    caller: Caller = Depends(_viewer), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Add a compliance document from the office. A newer document of the same type replaces the
    current one; the earlier one stays in the history."""
    supplier = _editable(db, caller, supplier_id)
    kinds = {t.slug: t for t in ensure_doc_types(db, caller.org_id)}
    if doc_type not in kinds or not kinds[doc_type].active:
        raise HTTPException(422, "Unknown document type")
    check_dates(issue_date, expiry_date, no_expiry)
    stored = save_upload(db, org_id=caller.org_id, upload=file, purpose="supplier_document", created_by=caller.user.id, allowed_mimes=EVIDENCE_TYPES)
    doc = add_document(db, supplier=supplier, doc_type=doc_type, file_id=stored.id, reference=_text(reference), issue_date=issue_date,
                       expiry_date=expiry_date, no_expiry=no_expiry, notes=_text(notes), source="office", created_by=caller.user.id)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.document_added",
          target_type="supplier", target_id=supplier.id, detail={"document_id": doc.id, "doc_type": doc_type})
    db.commit()
    labels, today, window = _doc_ctx(db, caller)
    return document_out(db, doc, labels, today, window)


def _get_document(db: Session, caller: Caller, supplier_id: str, doc_id: str) -> tuple[Supplier, SupplierDocument]:
    supplier = get_supplier(db, caller, supplier_id)
    doc = db.get(SupplierDocument, doc_id)
    if doc is None or doc.supplier_id != supplier.id or doc.deleted_at is not None:
        raise HTTPException(404, "Document not found")
    return supplier, doc


@router.get("/{supplier_id}/documents/{doc_id}/file")
def document_file(
    supplier_id: str, doc_id: str, download: bool = False, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)
):
    _supplier, doc = _get_document(db, caller, supplier_id, doc_id)
    stored = db.get(StoredFile, doc.file_id)
    if stored is None or stored.org_id != caller.org_id:
        raise HTTPException(404, "No file")
    return file_response(stored, inline=not download)


@router.delete("/{supplier_id}/documents/{doc_id}")
def delete_document(
    supplier_id: str, doc_id: str, request: Request, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Remove a document added by mistake. The one it replaced becomes current again."""
    supplier, doc = _get_document(db, caller, supplier_id, doc_id)
    if not can_edit(caller, supplier):
        raise HTTPException(403, "You do not have permission to do this")
    if doc.superseded_by_id is not None:
        raise HTTPException(409, "A newer document has replaced this one. Delete the newer one first.")
    doc.deleted_at = utcnow()
    previous = db.scalars(select(SupplierDocument).where(SupplierDocument.superseded_by_id == doc.id)).all()
    for old in previous:
        old.superseded_by_id = None
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.document_deleted",
          target_type="supplier", target_id=supplier.id, detail={"document_id": doc.id, "doc_type": doc.doc_type})
    db.commit()
    return {"ok": True}


# ---------- notes ----------

def _names(db: Session, ids: set[str | None]) -> dict[str, str]:
    wanted = {i for i in ids if i}
    return {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(wanted))).all()} if wanted else {}


def _note_out(note: SupplierNote, names: dict[str, str]) -> dict[str, Any]:
    return {"id": note.id, "supplier_id": note.supplier_id, "body": note.body, "author": names.get(note.created_by or "", "Unknown"),
            "created_at": iso(note.created_at), "updated_at": iso(note.updated_at),
            "edited": note.updated_at is not None and note.created_at is not None and (note.updated_at - note.created_at).total_seconds() > 1}


@router.get("/{supplier_id}/notes")
def list_notes(supplier_id: str, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    supplier = get_supplier(db, caller, supplier_id)
    rows = db.scalars(select(SupplierNote).where(SupplierNote.supplier_id == supplier.id, SupplierNote.deleted_at.is_(None))
                      .order_by(SupplierNote.created_at.desc())).all()
    names = _names(db, {n.created_by for n in rows})
    return {"items": [_note_out(n, names) for n in rows]}


@router.post("/{supplier_id}/notes", status_code=201)
def add_note(supplier_id: str, body: NoteIn, request: Request, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    supplier = _editable(db, caller, supplier_id)
    note = SupplierNote(org_id=caller.org_id, supplier_id=supplier.id, body=body.body.strip(), created_by=caller.user.id)
    db.add(note)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.note_added", target_type="supplier", target_id=supplier.id, detail={"note_id": note.id})
    db.commit()
    return _note_out(note, {caller.user.id: caller.user.name})


def _child(db: Session, caller: Caller, model: Any, row_id: str, what: str) -> tuple[Any, Supplier]:
    row = db.get(model, row_id)
    if row is None or row.org_id != caller.org_id or row.deleted_at is not None:
        raise HTTPException(404, f"{what} not found")
    supplier = get_supplier(db, caller, row.supplier_id)
    if not can_edit(caller, supplier):
        raise HTTPException(403, "You do not have permission to do this")
    return row, supplier


@router.patch("/notes/{note_id}")
def edit_note(note_id: str, body: NoteIn, request: Request, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    note, supplier = _child(db, caller, SupplierNote, note_id, "Note")
    note.body = body.body.strip()
    note.updated_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.note_edited", target_type="supplier", target_id=supplier.id, detail={"note_id": note.id})
    db.commit()
    return _note_out(note, _names(db, {note.created_by}))


@router.delete("/notes/{note_id}")
def delete_note(note_id: str, request: Request, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    note, supplier = _child(db, caller, SupplierNote, note_id, "Note")
    note.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.note_deleted", target_type="supplier", target_id=supplier.id, detail={"note_id": note.id})
    db.commit()
    return {"ok": True}


# ---------- tasks ----------

def _task_out(task: SupplierTask, names: dict[str, str], today: date) -> dict[str, Any]:
    return {"id": task.id, "supplier_id": task.supplier_id, "title": task.title, "description": task.description,
            "due_date": task.due_date.isoformat() if task.due_date else None, "status": task.status, "priority": task.priority,
            "assigned_to": task.assigned_to, "assigned_name": names.get(task.assigned_to or ""),
            "overdue": task.status != "done" and task.due_date is not None and task.due_date < today,
            "created_at": iso(task.created_at), "completed_at": iso(task.completed_at)}


def _assignee(db: Session, caller: Caller, user_id: str | None) -> None:
    if user_id is not None:
        user = db.get(User, user_id)
        if user is None or user.org_id != caller.org_id or user.deleted_at is not None:
            raise HTTPException(404, "The person this task is assigned to was not found")


@router.get("/{supplier_id}/tasks")
def list_tasks(supplier_id: str, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    supplier = get_supplier(db, caller, supplier_id)
    rows = db.scalars(select(SupplierTask).where(SupplierTask.supplier_id == supplier.id, SupplierTask.deleted_at.is_(None))
                      .order_by(SupplierTask.status == "done", SupplierTask.due_date.is_(None), SupplierTask.due_date, SupplierTask.created_at)).all()
    today = people.today_for(db.get(Organisation, caller.org_id))
    names = _names(db, {t.assigned_to for t in rows})
    return {"items": [_task_out(t, names, today) for t in rows]}


@router.post("/{supplier_id}/tasks", status_code=201)
def add_task(supplier_id: str, body: TaskIn, request: Request, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    supplier = _editable(db, caller, supplier_id)
    _assignee(db, caller, body.assigned_to)
    task = SupplierTask(org_id=caller.org_id, supplier_id=supplier.id, title=body.title.strip(), description=_text(body.description),
                        due_date=body.due_date, status=body.status, priority=body.priority, assigned_to=body.assigned_to,
                        created_by=caller.user.id, completed_at=utcnow() if body.status == "done" else None)
    db.add(task)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.task_added", target_type="supplier", target_id=supplier.id, detail={"task_id": task.id})
    db.commit()
    return _task_out(task, _names(db, {task.assigned_to}), people.today_for(db.get(Organisation, caller.org_id)))


@router.patch("/tasks/{task_id}")
def edit_task(task_id: str, body: TaskPatch, request: Request, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    task, supplier = _child(db, caller, SupplierTask, task_id, "Task")
    data = body.model_dump(exclude_unset=True)
    if "assigned_to" in data:
        _assignee(db, caller, data["assigned_to"])
    for key, value in data.items():
        if key in ("title", "status", "priority") and value is None:
            continue
        setattr(task, key, value.strip() if key == "title" else _text(value) if key == "description" else value)
    task.completed_at = (task.completed_at or utcnow()) if task.status == "done" else None
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.task_updated", target_type="supplier", target_id=supplier.id,
          detail={"task_id": task.id, "fields": sorted(data)})
    db.commit()
    return _task_out(task, _names(db, {task.assigned_to}), people.today_for(db.get(Organisation, caller.org_id)))


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, request: Request, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    task, supplier = _child(db, caller, SupplierTask, task_id, "Task")
    task.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.task_deleted", target_type="supplier", target_id=supplier.id, detail={"task_id": task.id})
    db.commit()
    return {"ok": True}


# ---------- members ----------

def _member_out(member: SupplierMember) -> dict[str, Any]:
    return {"id": member.id, "supplier_id": member.supplier_id, "name": member.name, "role": member.role, "email": member.email,
            "phone": member.phone, "is_primary_contact": member.is_primary_contact}


def _one_primary(db: Session, member: SupplierMember) -> None:
    if member.is_primary_contact:
        for other in db.scalars(select(SupplierMember).where(SupplierMember.supplier_id == member.supplier_id, SupplierMember.id != member.id,
                                                             SupplierMember.is_primary_contact.is_(True))).all():
            other.is_primary_contact = False


@router.get("/{supplier_id}/members")
def list_members(supplier_id: str, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    supplier = get_supplier(db, caller, supplier_id)
    rows = db.scalars(select(SupplierMember).where(SupplierMember.supplier_id == supplier.id, SupplierMember.deleted_at.is_(None))
                      .order_by(SupplierMember.is_primary_contact.desc(), func.lower(SupplierMember.name))).all()
    return {"items": [_member_out(m) for m in rows]}


@router.post("/{supplier_id}/members", status_code=201)
def add_member(supplier_id: str, body: MemberIn, request: Request, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    supplier = _editable(db, caller, supplier_id)
    member = SupplierMember(org_id=caller.org_id, supplier_id=supplier.id, name=body.name.strip(), role=_text(body.role),
                            email=str(body.email).lower() if body.email else None, phone=_text(body.phone), is_primary_contact=body.is_primary_contact)
    db.add(member)
    db.flush()
    _one_primary(db, member)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.member_added", target_type="supplier", target_id=supplier.id, detail={"member_id": member.id})
    db.commit()
    return _member_out(member)


@router.patch("/members/{member_id}")
def edit_member(member_id: str, body: MemberPatch, request: Request, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    member, supplier = _child(db, caller, SupplierMember, member_id, "Person")
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key in ("name", "is_primary_contact") and value is None:
            continue
        if key == "email":
            value = str(value).lower() if value else None
        elif isinstance(value, str):
            value = _text(value)
        setattr(member, key, value)
    _one_primary(db, member)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.member_updated", target_type="supplier", target_id=supplier.id, detail={"member_id": member.id})
    db.commit()
    return _member_out(member)


@router.delete("/members/{member_id}")
def delete_member(member_id: str, request: Request, caller: Caller = Depends(_viewer), db: Session = Depends(get_db)) -> dict[str, Any]:
    member, supplier = _child(db, caller, SupplierMember, member_id, "Person")
    member.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="supplier.member_removed", target_type="supplier", target_id=supplier.id, detail={"member_id": member.id})
    db.commit()
    return {"ok": True}


# ---------- document types ----------

def _type_out(kind: ComplianceDocType) -> dict[str, Any]:
    return {"id": kind.id, "slug": kind.slug, "label": kind.label, "description": kind.description,
            "required_for_contractors": kind.required_for_contractors, "active": kind.active, "sort_order": kind.sort_order}


@doc_types_router.get("")
def list_doc_types(include_inactive: bool = False, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    if not any(caller.can(r, "view") for r in ("renewals", "suppliers", "contractors")):
        raise HTTPException(403, "You do not have permission to do this")
    rows = ensure_doc_types(db, caller.org_id)
    db.commit()
    return {"items": [_type_out(t) for t in rows if include_inactive or t.active]}


@doc_types_router.post("", status_code=201)
def create_doc_type(body: DocTypeIn, request: Request, caller: Caller = Depends(require_permission("renewals", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    existing = ensure_doc_types(db, caller.org_id)
    if any(t.label.lower() == body.label.strip().lower() for t in existing):
        raise HTTPException(409, "A document type with this name already exists")
    base = slugify(body.label).replace("-", "_") or "document"
    slug, n = base, 2
    while any(t.slug == slug for t in existing):
        slug, n = f"{base}_{n}", n + 1
    kind = ComplianceDocType(org_id=caller.org_id, slug=slug, label=body.label.strip(), description=_text(body.description),
                             required_for_contractors=body.required_for_contractors, sort_order=max((t.sort_order for t in existing), default=0) + 1)
    db.add(kind)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="doc_type.created", target_type="doc_type", target_id=kind.id, detail={"slug": slug})
    db.commit()
    return _type_out(kind)


def _doc_type(db: Session, caller: Caller, type_id: str) -> ComplianceDocType:
    kind = db.get(ComplianceDocType, type_id)
    if kind is None or kind.org_id != caller.org_id:
        raise HTTPException(404, "Document type not found")
    return kind


@doc_types_router.patch("/{type_id}")
def update_doc_type(type_id: str, body: DocTypePatch, request: Request, caller: Caller = Depends(require_permission("renewals", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    kind = _doc_type(db, caller, type_id)
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is None and key != "description":
            continue
        setattr(kind, key, _text(value) if isinstance(value, str) else value)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="doc_type.updated", target_type="doc_type", target_id=kind.id, detail={"fields": sorted(data)})
    db.commit()
    return _type_out(kind)


@doc_types_router.delete("/{type_id}")
def retire_doc_type(type_id: str, request: Request, caller: Caller = Depends(require_permission("renewals", "delete")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Retire a type. Documents already held keep it, so nothing loses its label."""
    kind = _doc_type(db, caller, type_id)
    kind.active = False
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="doc_type.retired", target_type="doc_type", target_id=kind.id)
    db.commit()
    return _type_out(kind)
