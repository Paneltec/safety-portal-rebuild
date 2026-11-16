"""Reminders about certificates and supplier documents that are running out (stage 5b).

A reminder is only ever prepared when a person asks for it, and it goes through the same gate as
every other message (comms.deliver), so safe mode and the connection state decide whether it is
sent or held. Each certificate or document gets at most one "expiring" and one "expired" reminder
for a given expiry date: asking again finds the one already prepared instead of making another.
"""
from datetime import timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import comms, people
from .models import Certification, Organisation, OutboxMessage, Supplier, SupplierDocument, Worker

EXPIRED_LOOKBACK_DAYS = 60  # do not chase things that lapsed long ago


def _existing(db: Session, org_id: str, key: str) -> OutboxMessage | None:
    return db.scalars(select(OutboxMessage).where(OutboxMessage.org_id == org_id, OutboxMessage.dedupe_key == key, OutboxMessage.deleted_at.is_(None),
                                                  OutboxMessage.status.in_(comms.OPEN_STATES))).first()


def _prepare(db: Session, request: Request | None, *, org: Organisation, key: str, recipient: str | None, subject: str, body: str,
             related_type: str, related_id: str, user_id: str | None, dry_run: bool, label: str) -> dict[str, Any]:
    if not recipient:
        return {"label": label, "outcome": "no_email"}
    if _existing(db, org.id, key) is not None:
        return {"label": label, "outcome": "already_prepared"}
    if dry_run:
        return {"label": label, "outcome": "would_prepare", "recipient": recipient}
    message = comms.deliver(db, request, org_id=org.id, channel="email", purpose="reminder", recipient=recipient, subject=subject, body=body,
                            store_body=True, related_type=related_type, related_id=related_id, created_by=user_id, dedupe_key=key)
    return {"label": label, "outcome": message.status, "recipient": recipient, "message_id": message.id}


def _bucket(expiry, today) -> str:
    return "expired" if expiry < today else "expiring"


def certificate_reminder(db: Session, request: Request | None, org: Organisation, cert: Certification, worker: Worker, labels: dict[str, str],
                         user_id: str | None, *, dry_run: bool = False) -> dict[str, Any]:
    today = people.today_for(org)
    label = labels.get(cert.kind_slug, cert.kind_slug)
    state = _bucket(cert.expiry_date, today)
    when = cert.expiry_date.strftime("%d/%m/%Y")
    body = (f"Hello {worker.preferred_name or worker.first_name},\n\nYour {label} certificate {'expired on' if state == 'expired' else 'expires on'} {when}. "
            f"Please arrange a renewal and give the new certificate to the office.\n\n{org.name}")
    return _prepare(db, request, org=org, key=f"cert-reminder:{cert.id}:{cert.expiry_date.isoformat()}:{state}", recipient=worker.email,
                    subject=f"{label} {'has expired' if state == 'expired' else 'expires soon'}", body=body, related_type="certification", related_id=cert.id,
                    user_id=user_id, dry_run=dry_run, label=f"{people.worker_name(worker)}: {label} ({state} {when})")


def scan_certificates(db: Session, request: Request | None, org: Organisation, user_id: str | None, *, dry_run: bool) -> list[dict[str, Any]]:
    today = people.today_for(org)
    window = people.cert_settings(org)["expiry_window_days"]
    labels = people.kind_labels(db, org.id)
    pairs = db.execute(select(Certification, Worker).join(Worker, Worker.id == Certification.worker_id).where(
        Certification.org_id == org.id, Certification.deleted_at.is_(None), Certification.archived_at.is_(None), Certification.superseded_by_id.is_(None),
        Certification.no_expiry.is_(False), Certification.expiry_date.is_not(None),
        Certification.expiry_date <= today + timedelta(days=window), Certification.expiry_date >= today - timedelta(days=EXPIRED_LOOKBACK_DAYS),
        Worker.deleted_at.is_(None), Worker.archived_at.is_(None), Worker.active.is_(True)).order_by(Certification.expiry_date)).all()
    return [certificate_reminder(db, request, org, cert, worker, labels, user_id, dry_run=dry_run) for cert, worker in pairs]


def scan_supplier_documents(db: Session, request: Request | None, org: Organisation, user_id: str | None, labels: dict[str, str], *, dry_run: bool) -> list[dict[str, Any]]:
    today = people.today_for(org)
    window = people.cert_settings(org)["expiry_window_days"]
    pairs = db.execute(select(SupplierDocument, Supplier).join(Supplier, Supplier.id == SupplierDocument.supplier_id).where(
        SupplierDocument.org_id == org.id, SupplierDocument.deleted_at.is_(None), SupplierDocument.superseded_by_id.is_(None),
        SupplierDocument.no_expiry.is_(False), SupplierDocument.expiry_date.is_not(None),
        SupplierDocument.expiry_date <= today + timedelta(days=window), SupplierDocument.expiry_date >= today - timedelta(days=EXPIRED_LOOKBACK_DAYS),
        Supplier.deleted_at.is_(None), Supplier.archived_at.is_(None), Supplier.active.is_(True)).order_by(SupplierDocument.expiry_date)).all()
    out = []
    for doc, supplier in pairs:
        label = labels.get(doc.doc_type, doc.doc_type)
        state = _bucket(doc.expiry_date, today)
        when = doc.expiry_date.strftime("%d/%m/%Y")
        body = (f"Hello,\n\nOur records show that the {label} we hold for {supplier.name} {'expired on' if state == 'expired' else 'expires on'} {when}. "
                f"Please send us a current copy.\n\n{org.name}")
        out.append(_prepare(db, request, org=org, key=f"doc-reminder:{doc.id}:{doc.expiry_date.isoformat()}:{state}", recipient=supplier.contact_email,
                            subject=f"{label} {'has expired' if state == 'expired' else 'expires soon'}", body=body, related_type="supplier_document",
                            related_id=doc.id, user_id=user_id, dry_run=dry_run, label=f"{supplier.name}: {label} ({state} {when})"))
    return out


def summarise(results: list[dict[str, Any]], *, dry_run: bool) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result["outcome"]] = counts.get(result["outcome"], 0) + 1
    return {"dry_run": dry_run, "due": len(results), "counts": counts, "items": results[:200]}
