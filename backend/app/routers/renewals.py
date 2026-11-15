"""Renewal links (stage 4c).

The office asks a supplier (for compliance documents) or a worker (for certificates) to upload
evidence through a link. The link:

  * opens one page for one request, shows only that request, and accepts only the files asked for;
  * expires, and can be revoked or reissued; only the hash of its token is stored;
  * never lists other suppliers, workers, documents or anything else in the portal.

Nothing uploaded counts until the office accepts it. Accepting a file creates the supplier
document or the certificate renewal, keeping what was there before as history.
"""
from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import comms, people
from ..db import get_db
from ..deps import Caller, require_permission
from ..files import file_out, file_response, save_upload
from ..models import (
    Certification,
    Organisation,
    OutboxMessage,
    RenewalLink,
    RenewalUpload,
    StoredFile,
    Supplier,
    Worker,
    utcnow,
)
from ..security import hash_token, new_token
from ..services import audit, hold_message, iso, portal_base_url
from .suppliers import EVIDENCE_TYPES, add_document, can_see, check_dates, doc_type_labels, ensure_doc_types

router = APIRouter(prefix="/api/renewals", tags=["renewals"])
public_router = APIRouter(prefix="/api/public/renewals", tags=["renewals (public)"])

OPEN_STATES = ("pending", "submitted")
STATUS_LABELS = {
    "pending": "Waiting for the recipient", "submitted": "Ready to review", "accepted": "Accepted",
    "partly_accepted": "Partly accepted", "rejected": "Rejected", "revoked": "Cancelled", "expired": "Link expired",
}
PUBLIC_UPLOAD_MAX_BYTES = 15 * 1024 * 1024


# ---------- bodies ----------

class RenewalCreate(BaseModel):
    entity_type: Literal["supplier", "worker"] = "supplier"
    entity_id: str
    doc_types_requested: list[str] = Field(min_length=1, max_length=20)
    expires_in_days: int = Field(14, ge=1, le=90)
    recipient_email: EmailStr | None = None
    subject: str | None = Field(None, max_length=200)
    message: str | None = Field(None, max_length=2000)


class RenewalBulkCreate(BaseModel):
    contractor_ids: list[str] = Field(min_length=1, max_length=200)
    doc_types_requested: list[str] = Field(min_length=1, max_length=20)
    expires_in_days: int = Field(14, ge=1, le=90)
    subject: str | None = Field(None, max_length=200)
    message: str | None = Field(None, max_length=2000)


class RenewalPatch(BaseModel):
    doc_types_requested: list[str] | None = Field(None, min_length=1, max_length=20)
    recipient_email: EmailStr | None = None
    subject: str | None = Field(None, max_length=200)
    message: str | None = Field(None, max_length=2000)
    expires_at: datetime | None = None


class Decision(BaseModel):
    upload_id: str
    decision: Literal["accept", "reject"]
    note: str | None = Field(None, max_length=400)
    # The office may correct what the recipient typed before accepting.
    reference: str | None = Field(None, max_length=80)
    issue_date: date | None = None
    expiry_date: date | None = None
    no_expiry: bool = False


class ReviewIn(BaseModel):
    decisions: list[Decision] = Field(min_length=1, max_length=40)


class ReissueIn(BaseModel):
    expires_in_days: int = Field(14, ge=1, le=90)


class BulkIds(BaseModel):
    ids: list[str] = Field(max_length=500)


# ---------- helpers ----------

def _effective_status(link: RenewalLink) -> str:
    if link.status == "pending" and link.expires_at <= utcnow():
        return "expired"
    return link.status


def _entity(db: Session, link: RenewalLink) -> Supplier | Worker | None:
    return db.get(Supplier if link.entity_type == "supplier" else Worker, link.entity_id)


def _entity_name(entity: Supplier | Worker | None) -> str:
    if entity is None:
        return "Unknown"
    return entity.name if isinstance(entity, Supplier) else people.worker_name(entity)


def _labels(db: Session, link: RenewalLink) -> dict[str, str]:
    return doc_type_labels(db, link.org_id) if link.entity_type == "supplier" else people.kind_labels(db, link.org_id)


def _check_entity(db: Session, caller: Caller, entity_type: str, entity_id: str) -> Supplier | Worker:
    """The supplier or worker the link is for. Must be one the caller can already see."""
    if entity_type == "supplier":
        supplier = db.get(Supplier, entity_id)
        if supplier is None or supplier.org_id != caller.org_id or supplier.deleted_at is not None or not can_see(caller, supplier):
            raise HTTPException(404, "Supplier not found")
        return supplier
    worker = db.get(Worker, entity_id)
    if worker is None or worker.org_id != caller.org_id or worker.deleted_at is not None:
        raise HTTPException(404, "Worker not found")
    if not caller.can("certifications", "edit") or not caller.can("workers", "team_view"):
        raise HTTPException(403, "Asking a worker for certificates needs certification edit access")
    return worker


def _check_types(db: Session, org_id: str, entity_type: str, values: list[str]) -> list[str]:
    out: list[str] = []
    if entity_type == "supplier":
        known = {t.slug for t in ensure_doc_types(db, org_id) if t.active}
        for value in values:
            if value not in known:
                raise HTTPException(422, f"Unknown document type: {value}")
            if value not in out:
                out.append(value)
    else:
        for value in values:
            kind = people.resolve_kind(db, org_id, value)
            if kind is None or not kind.active:
                raise HTTPException(422, f"Unknown certification type: {value}")
            if kind.slug not in out:
                out.append(kind.slug)
    return out


def _current_uploads(db: Session, link: RenewalLink) -> list[RenewalUpload]:
    return list(db.scalars(select(RenewalUpload).where(
        RenewalUpload.renewal_id == link.id, RenewalUpload.round_no == link.round_no, RenewalUpload.replaced_at.is_(None))
        .order_by(RenewalUpload.uploaded_at)).all())


def _upload_out(db: Session, link: RenewalLink, upload: RenewalUpload, labels: dict[str, str]) -> dict[str, Any]:
    stored = db.get(StoredFile, upload.file_id)
    return {
        "id": upload.id, "doc_type": {"slug": upload.doc_type, "label": labels.get(upload.doc_type, upload.doc_type)},
        "reference": upload.reference,
        "issue_date": upload.issue_date.isoformat() if upload.issue_date else None,
        "expiry_date": upload.expiry_date.isoformat() if upload.expiry_date else None,
        "decision": upload.decision, "decision_note": upload.decision_note, "round": upload.round_no,
        "file": file_out(stored) if stored else None,
        "file_url": f"/api/renewals/{link.id}/uploads/{upload.id}/file",
        "uploaded_at": iso(upload.uploaded_at),
    }


def renewal_out(db: Session, link: RenewalLink, *, detail: bool = False) -> dict[str, Any]:
    labels = _labels(db, link)
    status = _effective_status(link)
    message = db.get(OutboxMessage, link.outbox_message_id) if link.outbox_message_id else None
    uploads = _current_uploads(db, link)
    out: dict[str, Any] = {
        "id": link.id, "entity_type": link.entity_type, "entity_id": link.entity_id, "entity_name": _entity_name(_entity(db, link)),
        "doc_types": [{"slug": s, "label": labels.get(s, s)} for s in link.doc_types],
        "status": status, "status_label": STATUS_LABELS[status],
        "recipient_email": link.recipient_email, "subject": link.subject, "message": link.message,
        "expires_at": iso(link.expires_at), "created_at": iso(link.created_at), "submitted_at": iso(link.submitted_at),
        "reviewed_at": iso(link.reviewed_at), "round": link.round_no,
        "uploaded": len({u.doc_type for u in uploads}), "requested": len(link.doc_types),
        # Whether anything actually went to the recipient. Until email is connected this is always "held".
        "delivery": message.status if message else "none",
    }
    if detail:
        out["uploads"] = [_upload_out(db, link, u, labels) for u in uploads]
        earlier = db.scalars(select(RenewalUpload).where(RenewalUpload.renewal_id == link.id, RenewalUpload.round_no < link.round_no,
                                                         RenewalUpload.replaced_at.is_(None)).order_by(RenewalUpload.uploaded_at)).all()
        out["earlier_uploads"] = [_upload_out(db, link, u, labels) for u in earlier]
    return out


def _get(db: Session, caller: Caller, renewal_id: str) -> RenewalLink:
    link = db.get(RenewalLink, renewal_id)
    if link is None or link.org_id != caller.org_id or link.deleted_at is not None:
        raise HTTPException(404, "Renewal request not found")
    return link


def _mint(db: Session, request: Request, caller: Caller, *, entity_type: str, entity: Supplier | Worker, doc_types: list[str],
          days: int, recipient: str | None, subject: str | None, message: str | None) -> tuple[RenewalLink, str]:
    token = new_token()
    link = RenewalLink(
        org_id=caller.org_id, entity_type=entity_type, entity_id=entity.id, doc_types=doc_types, token_hash=hash_token(token),
        recipient_email=recipient, subject=(subject or "").strip() or None, message=(message or "").strip() or None,
        expires_at=utcnow() + timedelta(days=days), created_by=caller.user.id,
    )
    db.add(link)
    db.flush()
    if recipient:
        _notify(db, request, caller, link, token, _entity_name(entity))
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="renewal.created",
          target_type="renewal_link", target_id=link.id,
          detail={"entity_type": entity_type, "entity_id": entity.id, "doc_types": doc_types, "expires_at": iso(link.expires_at)})
    return link, token


def _notify(db: Session, request: Request, caller: Caller, link: RenewalLink, token: str, name: str) -> None:
    """Prepare the email for a request. The text holds the link, so it is never stored."""
    org_name = db.get(Organisation, caller.org_id).name
    labels = _labels(db, link)
    wanted = "\n".join(f"- {labels.get(s, s)}" for s in link.doc_types)
    text = (f"Hello,\n\n{org_name} needs current copies of the following for {name}:\n{wanted}\n\n"
            + (f"{link.message}\n\n" if link.message else "")
            + f"Upload them here (no login needed):\n{portal_base_url(request)}/renew/{token}\n\nThis link expires on {link.expires_at:%d/%m/%Y}.")
    held = hold_message(db, org_id=caller.org_id, channel="email", purpose="renewal_request", recipient=link.recipient_email,
                        subject=link.subject or f"Documents requested by {org_name}", related_type="renewal_link", related_id=link.id,
                        created_by=caller.user.id, request=request, body=text, store_body=False)
    db.flush()
    link.outbox_message_id = held.id


def link_note_for(link: RenewalLink) -> str:
    from sqlalchemy.orm import object_session

    db = object_session(link)
    message = db.get(OutboxMessage, link.outbox_message_id) if db is not None and link.outbox_message_id else None
    return comms.link_note(message)


def _link_payload(request: Request, token: str, link: RenewalLink) -> dict[str, Any]:
    # Shown once. Only the hash is stored, so it cannot be shown again; reissue to get a new one.
    # `delivery` itself comes from the outbox record, so it is never reported as held when no
    # message exists.
    return {"link": f"{portal_base_url(request)}/renew/{token}", "delivery_note": link_note_for(link)}


# ---------- office: list and create ----------

@router.get("")
def list_renewals(
    status: Literal["pending", "submitted", "accepted", "partly_accepted", "rejected", "revoked", "expired"] | None = None,
    entity_type: Literal["supplier", "worker"] | None = None,
    entity_id: str | None = None,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    caller: Caller = Depends(require_permission("renewals", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(RenewalLink).where(RenewalLink.org_id == caller.org_id, RenewalLink.deleted_at.is_(None))
    if entity_type:
        stmt = stmt.where(RenewalLink.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(RenewalLink.entity_id == entity_id)
    rows = [renewal_out(db, r) for r in db.scalars(stmt.order_by(RenewalLink.created_at.desc())).all()]
    counts: dict[str, int] = {key: 0 for key in STATUS_LABELS}
    for row in rows:
        counts[row["status"]] += 1
    if status:
        rows = [r for r in rows if r["status"] == status]
    return {"items": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset, "counts": counts}


@router.post("", status_code=201)
def create_renewal(
    body: RenewalCreate, request: Request,
    caller: Caller = Depends(require_permission("renewals", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a request and its link. The link is returned once; nothing is emailed."""
    entity = _check_entity(db, caller, body.entity_type, body.entity_id)
    doc_types = _check_types(db, caller.org_id, body.entity_type, body.doc_types_requested)
    recipient = str(body.recipient_email).lower() if body.recipient_email else (
        entity.contact_email if isinstance(entity, Supplier) else entity.email)
    link, token = _mint(db, request, caller, entity_type=body.entity_type, entity=entity, doc_types=doc_types,
                        days=body.expires_in_days, recipient=recipient, subject=body.subject, message=body.message)
    db.commit()
    return {**renewal_out(db, link, detail=True), **_link_payload(request, token, link)}


@router.post("/bulk", status_code=201)
def bulk_create(
    body: RenewalBulkCreate, request: Request,
    caller: Caller = Depends(require_permission("renewals", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """One request per supplier. A supplier that already has an open request covering every
    document asked for is skipped rather than given a second link."""
    doc_types = _check_types(db, caller.org_id, "supplier", body.doc_types_requested)
    created, skipped = [], []
    for supplier_id in dict.fromkeys(body.contractor_ids):
        supplier = db.get(Supplier, supplier_id)
        if supplier is None or supplier.org_id != caller.org_id or supplier.deleted_at is not None or not can_see(caller, supplier):
            skipped.append({"id": supplier_id, "reason": "not_found"})
            continue
        open_links = db.scalars(select(RenewalLink).where(
            RenewalLink.org_id == caller.org_id, RenewalLink.entity_type == "supplier", RenewalLink.entity_id == supplier.id,
            RenewalLink.deleted_at.is_(None), RenewalLink.status.in_(OPEN_STATES))).all()
        if any(_effective_status(l) in OPEN_STATES and set(doc_types) <= set(l.doc_types) for l in open_links):
            skipped.append({"id": supplier_id, "reason": "already_requested"})
            continue
        link, token = _mint(db, request, caller, entity_type="supplier", entity=supplier, doc_types=doc_types, days=body.expires_in_days,
                            recipient=supplier.contact_email, subject=body.subject, message=body.message)
        created.append({"id": link.id, "entity_id": supplier.id, "entity_name": supplier.name, **_link_payload(request, token, link)})
    db.commit()
    return {"created": created, "skipped": skipped}


@router.post("/bulk-delete")
def bulk_delete(
    body: BulkIds, request: Request,
    caller: Caller = Depends(require_permission("renewals", "delete")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    deleted, skipped = 0, []
    for rid in dict.fromkeys(body.ids):
        link = db.get(RenewalLink, rid)
        if link is None or link.org_id != caller.org_id or link.deleted_at is not None:
            skipped.append({"id": rid, "reason": "not_found"})
            continue
        _revoke(link)
        link.deleted_at = utcnow()
        deleted += 1
    if deleted:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="renewal.bulk_deleted",
              target_type="organisation", target_id=caller.org_id, detail={"deleted": deleted})
        db.commit()
    return {"requested": len(body.ids), "deleted": deleted, "skipped": skipped}


# ---------- office: one request ----------

@router.get("/{renewal_id}")
def get_renewal(
    renewal_id: str, caller: Caller = Depends(require_permission("renewals", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return renewal_out(db, _get(db, caller, renewal_id), detail=True)


@router.patch("/{renewal_id}")
def update_renewal(
    renewal_id: str, body: RenewalPatch, request: Request,
    caller: Caller = Depends(require_permission("renewals", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Change the wording, the documents asked for or the expiry while the request is still open.
    The recipient's link keeps working."""
    link = _get(db, caller, renewal_id)
    if link.status != "pending":
        raise HTTPException(409, "Only a request that is still waiting for the recipient can be changed")
    data = body.model_dump(exclude_unset=True)
    if data.get("doc_types_requested"):
        link.doc_types = _check_types(db, caller.org_id, link.entity_type, data["doc_types_requested"])
    if "recipient_email" in data:
        link.recipient_email = str(data["recipient_email"]).lower() if data["recipient_email"] else None
    for key in ("subject", "message"):
        if key in data:
            setattr(link, key, (data[key] or "").strip() or None)
    if data.get("expires_at") is not None:
        when = data["expires_at"]
        if when.tzinfo is None:
            raise HTTPException(422, "The expiry time must include its timezone")
        if when <= utcnow() or when > utcnow() + timedelta(days=90):
            raise HTTPException(422, "The expiry must be in the future and within 90 days")
        link.expires_at = when
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="renewal.updated",
          target_type="renewal_link", target_id=link.id, detail={"fields": sorted(data)})
    db.commit()
    return renewal_out(db, link, detail=True)


def _revoke(link: RenewalLink) -> bool:
    if link.status in OPEN_STATES:
        link.status = "revoked"
        return True
    return False


@router.post("/{renewal_id}/revoke")
def revoke(
    renewal_id: str, request: Request,
    caller: Caller = Depends(require_permission("renewals", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Cancel the request. The link stops working immediately."""
    link = _get(db, caller, renewal_id)
    if _revoke(link):
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="renewal.revoked", target_type="renewal_link", target_id=link.id)
        db.commit()
    return renewal_out(db, link, detail=True)


@router.delete("/{renewal_id}")
def delete_renewal(
    renewal_id: str, request: Request,
    caller: Caller = Depends(require_permission("renewals", "delete")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    link = _get(db, caller, renewal_id)
    _revoke(link)
    link.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="renewal.deleted", target_type="renewal_link", target_id=link.id)
    db.commit()
    return {"ok": True}


@router.post("/{renewal_id}/reissue")
def reissue(
    renewal_id: str, body: ReissueIn, request: Request,
    caller: Caller = Depends(require_permission("renewals", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Give the recipient a fresh link: after an expiry, a rejection, or a lost link. The previous
    link stops working. Files already accepted stay accepted; earlier uploads are kept as history."""
    link = _get(db, caller, renewal_id)
    if link.status == "accepted":
        raise HTTPException(409, "Everything in this request has already been accepted")
    if link.status == "submitted":
        raise HTTPException(409, "This request is waiting for review. Review it before sending a new link.")
    token = new_token()
    link.token_hash = hash_token(token)
    link.status = "pending"
    link.expires_at = utcnow() + timedelta(days=body.expires_in_days)
    link.submitted_at = None
    link.round_no += 1
    if link.recipient_email:
        _notify(db, request, caller, link, token, _entity_name(_entity(db, link)))
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="renewal.reissued",
          target_type="renewal_link", target_id=link.id, detail={"round": link.round_no, "expires_at": iso(link.expires_at)})
    db.commit()
    return {**renewal_out(db, link, detail=True), **_link_payload(request, token, link)}


@router.get("/{renewal_id}/uploads/{upload_id}/file")
def upload_file(
    renewal_id: str, upload_id: str, download: bool = False,
    caller: Caller = Depends(require_permission("renewals", "view")), db: Session = Depends(get_db),
):
    link = _get(db, caller, renewal_id)
    upload = db.get(RenewalUpload, upload_id)
    if upload is None or upload.renewal_id != link.id:
        raise HTTPException(404, "File not found")
    stored = db.get(StoredFile, upload.file_id)
    if stored is None or stored.org_id != caller.org_id:
        raise HTTPException(404, "File not found")
    return file_response(stored, inline=not download)


@router.post("/{renewal_id}/review")
def review(
    renewal_id: str, body: ReviewIn, request: Request,
    caller: Caller = Depends(require_permission("renewals", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Accept or reject each uploaded file. Every file must be decided in one go. An accepted file
    becomes the supplier's current document (or the worker's renewed certificate)."""
    link = _get(db, caller, renewal_id)
    if link.status != "submitted":
        raise HTTPException(409, "There is nothing waiting for review on this request")
    entity = _entity(db, link)
    if entity is None or entity.deleted_at is not None:
        raise HTTPException(409, "The supplier or worker this request was for no longer exists")
    if link.entity_type == "worker" and not caller.can("certifications", "edit"):
        raise HTTPException(403, "Accepting certificates needs certification edit access")
    uploads = {u.id: u for u in _current_uploads(db, link) if u.decision == "pending"}
    decisions = {d.upload_id: d for d in body.decisions}
    if set(decisions) != set(uploads):
        raise HTTPException(422, "Decide every uploaded file: accept or reject each one")
    for decision in decisions.values():
        if decision.decision == "reject" and not (decision.note or "").strip():
            raise HTTPException(422, "Say why a file is rejected, so the recipient knows what to send instead")
        if decision.decision == "accept":
            check_dates(decision.issue_date, decision.expiry_date, decision.no_expiry)

    accepted = rejected = 0
    for upload_id, decision in decisions.items():
        upload = uploads[upload_id]
        upload.decision_note = (decision.note or "").strip() or None
        if decision.decision == "reject":
            upload.decision = "rejected"
            rejected += 1
            continue
        upload.decision = "accepted"
        accepted += 1
        reference = (decision.reference or "").strip() or upload.reference
        issue = decision.issue_date or upload.issue_date
        expiry = None if decision.no_expiry else (decision.expiry_date or upload.expiry_date)
        check_dates(issue, expiry, decision.no_expiry)
        if isinstance(entity, Supplier):
            doc = add_document(db, supplier=entity, doc_type=upload.doc_type, file_id=upload.file_id, reference=reference,
                               issue_date=issue, expiry_date=expiry, no_expiry=decision.no_expiry, notes=None, source="renewal",
                               created_by=caller.user.id, renewal_id=link.id)
            upload.result_id = doc.id
        else:
            current = next((c for c in people.current_certs(db, entity.id) if c.kind_slug == upload.doc_type), None)
            cert = Certification(org_id=link.org_id, worker_id=entity.id, kind_slug=upload.doc_type,
                                 number=reference or (current.number if current else None), issue_date=issue, expiry_date=expiry,
                                 no_expiry=decision.no_expiry, evidence_file_id=upload.file_id,
                                 supersedes_id=current.id if current else None, created_by=caller.user.id)
            db.add(cert)
            db.flush()
            if current is not None:
                current.superseded_by_id = cert.id  # the earlier certificate and its evidence are kept
            upload.result_id = cert.id

    link.status = "accepted" if rejected == 0 else "rejected" if accepted == 0 else "partly_accepted"
    link.reviewed_by, link.reviewed_at = caller.user.id, utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="renewal.reviewed",
          target_type="renewal_link", target_id=link.id, detail={"accepted": accepted, "rejected": rejected})
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="renewal.accepted_documents",
          target_type="supplier" if link.entity_type == "supplier" else "worker", target_id=link.entity_id,
          detail={"renewal_id": link.id, "accepted": accepted, "rejected": rejected})
    db.commit()
    return renewal_out(db, link, detail=True)


# ---------- public: the recipient's page ----------

def _by_token(db: Session, token: str) -> RenewalLink:
    """Every way a token can be unusable looks the same from outside."""
    link = db.scalars(select(RenewalLink).where(RenewalLink.token_hash == hash_token(token))).first() if token else None
    if link is None or link.deleted_at is not None or link.status == "revoked":
        raise HTTPException(404, "This link is not valid. Ask the person who sent it for a new one.")
    return link


def _public(db: Session, link: RenewalLink) -> dict[str, Any]:
    org = db.get(Organisation, link.org_id)
    labels = _labels(db, link)
    descriptions = {t.slug: t.description for t in ensure_doc_types(db, link.org_id)} if link.entity_type == "supplier" else {}
    uploads = {u.doc_type: u for u in _current_uploads(db, link)}
    rejected = {}
    if link.round_no > 1:
        for upload in db.scalars(select(RenewalUpload).where(RenewalUpload.renewal_id == link.id, RenewalUpload.round_no == link.round_no - 1,
                                                             RenewalUpload.decision == "rejected")).all():
            rejected[upload.doc_type] = upload.decision_note
    accepted = {u.doc_type for u in db.scalars(select(RenewalUpload).where(RenewalUpload.renewal_id == link.id, RenewalUpload.decision == "accepted")).all()}
    status = _effective_status(link)
    items = []
    for slug in link.doc_types:
        upload = uploads.get(slug)
        stored = db.get(StoredFile, upload.file_id) if upload else None
        items.append({
            "slug": slug, "label": labels.get(slug, slug), "description": descriptions.get(slug),
            "already_accepted": slug in accepted,
            "rejected_note": rejected.get(slug),
            "uploaded": ({"name": stored.original_name if stored else "file", "reference": upload.reference,
                          "expiry_date": upload.expiry_date.isoformat() if upload.expiry_date else None} if upload else None),
        })
    return {
        "organisation": (org.details or {}).get("trading_name") or org.name, "timezone": org.timezone,
        "for_name": _entity_name(_entity(db, link)), "kind": link.entity_type,
        "message": link.message, "status": status, "expires_at": iso(link.expires_at), "submitted_at": iso(link.submitted_at),
        "documents": items, "can_upload": status == "pending",
    }


@public_router.get("/{token}")
def public_view(token: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """What the recipient sees: who is asking, who it is for, and the documents requested."""
    return _public(db, _by_token(db, token))


def _open_link(db: Session, token: str) -> RenewalLink:
    link = _by_token(db, token)
    status = _effective_status(link)
    if status == "expired":
        raise HTTPException(410, "This link has expired. Ask the person who sent it for a new one.")
    if status != "pending":
        raise HTTPException(409, "These documents have already been sent. Nothing more can be added with this link.")
    return link


@public_router.post("/{token}/upload")
def public_upload(
    token: str,
    doc_type: str = Form(...), file: UploadFile = File(...), reference: str | None = Form(None, max_length=80),
    issue_date: date | None = Form(None), expiry_date: date | None = Form(None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Attach one file for one of the requested documents. Uploading again replaces the earlier file."""
    link = _open_link(db, token)
    if doc_type not in link.doc_types:
        raise HTTPException(422, "That document was not asked for")
    already = db.scalars(select(RenewalUpload).where(RenewalUpload.renewal_id == link.id, RenewalUpload.doc_type == doc_type,
                                                     RenewalUpload.decision == "accepted")).first()
    if already is not None:
        raise HTTPException(409, "That document has already been accepted")
    check_dates(issue_date, expiry_date, False)
    stored = save_upload(db, org_id=link.org_id, upload=file, purpose="renewal_upload", created_by=None,
                         max_bytes=PUBLIC_UPLOAD_MAX_BYTES, allowed_mimes=EVIDENCE_TYPES)
    for old in _current_uploads(db, link):
        if old.doc_type == doc_type:
            old.replaced_at = utcnow()
    db.add(RenewalUpload(org_id=link.org_id, renewal_id=link.id, doc_type=doc_type, file_id=stored.id,
                         reference=(reference or "").strip() or None, issue_date=issue_date, expiry_date=expiry_date, round_no=link.round_no))
    db.commit()
    return _public(db, link)


@public_router.post("/{token}/submit")
def public_submit(token: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Send the uploaded files to the office. After this the link accepts nothing more."""
    link = _by_token(db, token)
    if link.status == "submitted":
        return _public(db, link)  # a repeated tap is harmless
    link = _open_link(db, token)
    accepted = {u.doc_type for u in db.scalars(select(RenewalUpload).where(RenewalUpload.renewal_id == link.id, RenewalUpload.decision == "accepted")).all()}
    supplied = {u.doc_type for u in _current_uploads(db, link)}
    if not supplied:
        raise HTTPException(422, "Add at least one document before sending")
    missing = [s for s in link.doc_types if s not in supplied and s not in accepted]
    if missing:
        labels = _labels(db, link)
        raise HTTPException(422, "Still needed: " + ", ".join(labels.get(s, s) for s in missing))
    link.status = "submitted"
    link.submitted_at = utcnow()
    db.commit()
    return _public(db, link)
