"""Connections, safe mode, the outbox and the notification bell (stage 5a)."""
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import comms, integrations, people
from ..crypto import key_status
from ..db import get_db
from ..deps import Caller, get_caller, require_permission
from ..models import (
    Certification,
    DailyJob,
    NotificationRead,
    Organisation,
    OutboxAttempt,
    OutboxMessage,
    RenewalLink,
    SiteAttendance,
    Supplier,
    User,
    Worker,
    utcnow,
)
from ..services import audit, effective_for, iso
from .. import permissions as perms

integrations_router = APIRouter(prefix="/api/integrations", tags=["integrations"])
safe_mode_router = APIRouter(prefix="/api/admin/comms-safe-mode", tags=["communications"])
outbox_router = APIRouter(prefix="/api/email/outbox", tags=["communications"])
notifications_router = APIRouter(prefix="/api/notifications", tags=["notifications"])


# ---------- connections ----------

class ConfigIn(BaseModel):
    settings: dict[str, Any] | None = None
    secrets: dict[str, Any] | None = None
    clear_secrets: list[str] | None = Field(None, max_length=10)
    mode: Literal["off", "simulator", "live"] | None = None


def _kind(key: str) -> integrations.Kind:
    kind = integrations.CATALOGUE.get(key)
    if kind is None:
        raise HTTPException(404, "Unknown connection")
    return kind


def _config_out(kind: integrations.Kind, config: Any, *, can_edit: bool) -> dict[str, Any]:
    held = set((config.secrets_set if config else None) or [])
    settings = integrations.effective_settings(kind, config)
    return {
        "kind": kind.key, "label": kind.label, "purpose": kind.purpose, "notes": kind.notes, "channel": kind.channel,
        "mode": config.mode if config else "off",
        "status": integrations.status_of(kind, config),
        # A secret is only ever reported as present or absent. Its value is never returned.
        "fields": [{"name": f.name, "label": f.label, "kind": f.kind, "secret": f.secret, "required": f.required, "help": f.help,
                    "minimum": f.minimum, "maximum": f.maximum,
                    **({"is_set": f.name in held} if f.secret else {"value": settings.get(f.name)})} for f in kind.fields],
        "last_test": ({"at": iso(config.last_test_at), "ok": config.last_test_ok, "simulated": config.last_test_mode == "simulator",
                       "message": config.last_test_message} if config and config.last_test_at else None),
        "capabilities": (config.capabilities if config else None) or {},
        # Kept separate on purpose: a good test does not mean data has been imported.
        "last_sync_at": iso(config.last_sync_at) if config else None,
        "sync": "No import has run yet." if not (config and config.last_sync_at) else None,
        "can_edit": can_edit,
        "updated_at": iso(config.updated_at) if config else None,
    }


@integrations_router.get("")
def list_integrations(caller: Caller = Depends(require_permission("integrations", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    can_edit = caller.can("integrations", "edit")
    items = [_config_out(kind, integrations.get_config(db, caller.org_id, kind.key), can_edit=can_edit) for kind in integrations.CATALOGUE.values()]
    return {"items": items, "encryption": key_status(), "safe_mode": comms.safe_mode(db.get(Organisation, caller.org_id))["effective"]}


@integrations_router.get("/{kind}")
def get_integration(kind: str, caller: Caller = Depends(require_permission("integrations", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    k = _kind(kind)
    return _config_out(k, integrations.get_config(db, caller.org_id, k.key), can_edit=caller.can("integrations", "edit"))


@integrations_router.put("/{kind}")
def save_integration(kind: str, body: ConfigIn, request: Request, caller: Caller = Depends(require_permission("integrations", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Save details. A secret left out, empty, or sent back as dots is kept; list it in
    `clear_secrets` to remove it on purpose."""
    k = _kind(kind)
    config, changed = integrations.save_config(db, caller.org_id, k, settings=body.settings, secrets=body.secrets,
                                               clear_secrets=body.clear_secrets, mode=body.mode, user_id=caller.user.id)
    if changed:
        # Names only. The values of secrets never reach the audit log.
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="integration.updated", target_type="integration", target_id=k.key, detail={"fields": sorted(set(changed))})
    db.commit()
    return _config_out(k, config, can_edit=True)


@integrations_router.delete("/{kind}")
def disconnect(kind: str, request: Request, caller: Caller = Depends(require_permission("integrations", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Remove the saved details and switch the connection off."""
    k = _kind(kind)
    config = integrations.get_config(db, caller.org_id, k.key)
    if config is not None:
        db.delete(config)
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="integration.disconnected", target_type="integration", target_id=k.key)
        db.commit()
    return _config_out(k, None, can_edit=True)


@integrations_router.post("/{kind}/test-connection")
def test_connection(kind: str, request: Request, caller: Caller = Depends(require_permission("integrations", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Check the saved details against the provider. Reads only: nothing is sent or changed there."""
    k = _kind(kind)
    config = integrations.get_config(db, caller.org_id, k.key)
    if config is None:
        raise HTTPException(409, "Save the connection details first")
    result = integrations.run_test(db, config, k)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="integration.tested", target_type="integration", target_id=k.key,
          detail={"ok": result.ok, "simulated": result.simulated})
    db.commit()
    return {"ok": result.ok, "simulated": result.simulated, "message": result.message, "connection": _config_out(k, config, can_edit=True)}


# ---------- safe mode ----------

class SafeModeIn(BaseModel):
    mode: Literal["on", "off"]


def _safe_status(db: Session, org: Organisation) -> dict[str, Any]:
    blocked = db.scalar(select(func.count()).select_from(OutboxMessage).where(
        OutboxMessage.org_id == org.id, OutboxMessage.status == "held", OutboxMessage.hold_code == "safe_mode", OutboxMessage.deleted_at.is_(None))) or 0
    return {**comms.safe_mode(org), "blocked_count": int(blocked)}


@safe_mode_router.get("/status")
def safe_mode_status(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Anyone signed in can see whether messages are being sent, so the badge always matches the server."""
    return {**_safe_status(db, db.get(Organisation, caller.org_id)), "can_change": caller.can("comms_safe_mode", "edit")}


@safe_mode_router.get("/who-can-toggle")
def who_can_toggle(caller: Caller = Depends(require_permission("users", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    users = db.scalars(select(User).where(User.org_id == caller.org_id, User.deleted_at.is_(None), User.status == "active").order_by(func.lower(User.name))).all()
    return {"items": [{"id": u.id, "name": u.name} for u in users if perms.can(effective_for(db, u), "comms_safe_mode", "edit")]}


@safe_mode_router.patch("")
def set_safe_mode(body: SafeModeIn, request: Request, caller: Caller = Depends(require_permission("comms_safe_mode", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Switch the organisation's safe mode. The server setting can lock it on regardless."""
    org = db.get(Organisation, caller.org_id)
    before = comms.safe_mode(org)
    if body.mode == "off" and before["env_locked"]:
        raise HTTPException(409, "Safe mode is locked on by the server (COMMS_SAFE_MODE). Change that setting on the server first.")
    if before["org_value"] != body.mode:
        org.details = {**(org.details or {}), "comms_safe_mode": body.mode}
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="comms.safe_mode_changed", target_type="organisation", target_id=org.id,
              detail={"from": before["org_value"], "to": body.mode})
        db.commit()
    return {**_safe_status(db, org), "can_change": True}


# ---------- outbox ----------

PURPOSES = {"invitation": "Invitation", "password_reset": "Password reset", "renewal_request": "Document request", "daily_job": "Daily job",
            "test": "Test message"}
STATUS_LABELS = {"held": "Not sent", "queued": "Waiting to send", "sent": "Sent", "simulated": "Simulated", "failed": "Failed", "cancelled": "Cancelled"}


class BulkDeleteIn(BaseModel):
    ids: list[str] | None = Field(None, max_length=500)
    status: Literal["held", "sent", "simulated", "failed", "cancelled"] | None = None  # never "queued"


def _message_out(db: Session, message: OutboxMessage, *, detail: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": message.id, "channel": message.channel, "purpose": message.purpose, "purpose_label": PURPOSES.get(message.purpose, message.purpose),
        "recipient": message.recipient, "subject": message.subject, "status": message.status, "status_label": STATUS_LABELS.get(message.status, message.status),
        "hold_code": message.hold_code, "reason": message.last_error if message.status == "failed" else message.hold_reason if message.status == "held" else None,
        "related_type": message.related_type, "related_id": message.related_id, "attempts": message.attempts,
        "can_retry": message.body_text is not None and message.status in ("held", "failed"),
        "created_at": iso(message.created_at), "sent_at": iso(message.sent_at),
    }
    if detail:
        out["body_text"] = message.body_text
        out["body_stored"] = message.body_text is not None
        rows = db.scalars(select(OutboxAttempt).where(OutboxAttempt.message_id == message.id).order_by(OutboxAttempt.at)).all()
        out["history"] = [{"at": iso(a.at), "outcome": a.outcome, "detail": a.detail} for a in rows]
    return out


def _outbox_stmt(caller: Caller):
    stmt = select(OutboxMessage).where(OutboxMessage.org_id == caller.org_id, OutboxMessage.deleted_at.is_(None))
    if not caller.can("notifications", "view"):
        stmt = stmt.where(OutboxMessage.created_by == caller.user.id)  # otherwise only what you created
    return stmt


def _message(db: Session, caller: Caller, message_id: str) -> OutboxMessage:
    message = db.get(OutboxMessage, message_id)
    if message is None or message.org_id != caller.org_id or message.deleted_at is not None:
        raise HTTPException(404, "Message not found")
    if not caller.can("notifications", "view") and message.created_by != caller.user.id:
        raise HTTPException(404, "Message not found")
    return message


@outbox_router.get("")
def list_outbox(
    status: Literal["held", "queued", "sent", "simulated", "failed", "cancelled"] | None = None,
    channel: Literal["email", "sms"] | None = None,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    caller: Caller = Depends(get_caller), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Every message the portal has prepared, newest first, with what happened to each."""
    base = _outbox_stmt(caller)
    if channel:
        base = base.where(OutboxMessage.channel == channel)
    counts = {key: 0 for key in STATUS_LABELS}
    for key, n in db.execute(select(OutboxMessage.status, func.count()).where(OutboxMessage.id.in_(select(base.subquery().c.id))).group_by(OutboxMessage.status)).all():
        counts[key] = int(n)
    stmt = base.where(OutboxMessage.status == status) if status else base
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(OutboxMessage.created_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [_message_out(db, m) for m in rows], "total": int(total), "limit": limit, "offset": offset, "counts": counts,
            "safe_mode": comms.safe_mode(db.get(Organisation, caller.org_id))["effective"], "sees_all": caller.can("notifications", "view")}


@outbox_router.post("/bulk-delete")
def bulk_delete(body: BulkDeleteIn, request: Request, caller: Caller = Depends(require_permission("notifications", "delete")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Hide messages from the list. Anything still waiting to send is only removed when named by id."""
    if not body.ids and not body.status:
        raise HTTPException(422, "Give the ids to delete, or a status")
    stmt = select(OutboxMessage).where(OutboxMessage.org_id == caller.org_id, OutboxMessage.deleted_at.is_(None))
    stmt = stmt.where(OutboxMessage.id.in_(body.ids)) if body.ids else stmt.where(OutboxMessage.status == body.status)
    rows = db.scalars(stmt).all()
    now = utcnow()
    for message in rows:
        if message.status == "queued":
            message.status = "cancelled"
        message.deleted_at = now
    if rows:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="outbox.bulk_deleted", target_type="organisation", target_id=caller.org_id, detail={"deleted": len(rows)})
        db.commit()
    return {"deleted": len(rows)}


@outbox_router.get("/{message_id}")
def get_message(message_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _message_out(db, _message(db, caller, message_id), detail=True)


@outbox_router.post("/{message_id}/retry")
def retry_message(message_id: str, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Send again. A message that was sent is never sent twice, and one that carried a one-time
    link cannot be retried because its text was never kept."""
    message = _message(db, caller, message_id)
    if not (caller.can("notifications", "edit") or message.created_by == caller.user.id):
        raise HTTPException(403, "You do not have permission to do this")
    if message.status in ("sent", "simulated"):
        raise HTTPException(409, "This message has already gone. It will not be sent twice.")
    if message.status == "cancelled":
        raise HTTPException(409, "This message was cancelled")
    if message.body_text is None:
        raise HTTPException(409, "This message carried a one-time link, which is never stored. Create a new link instead.")
    comms.retry(db, request, message, caller.user.id)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="outbox.retried", target_type="outbox_message", target_id=message.id, detail={"result": message.status})
    db.commit()
    return _message_out(db, message, detail=True)


@outbox_router.post("/{message_id}/cancel")
def cancel_message(message_id: str, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    message = _message(db, caller, message_id)
    if not (caller.can("notifications", "edit") or message.created_by == caller.user.id):
        raise HTTPException(403, "You do not have permission to do this")
    if message.status in ("held", "queued", "failed"):
        message.status = "cancelled"
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="outbox.cancelled", target_type="outbox_message", target_id=message.id)
        db.commit()
    elif message.status != "cancelled":
        raise HTTPException(409, "This message has already gone and cannot be cancelled")
    return _message_out(db, message, detail=True)


@outbox_router.delete("/{message_id}")
def delete_message(message_id: str, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Hide a message from the list. Admins can hide any; others only their own."""
    message = _message(db, caller, message_id)
    if not (caller.can("notifications", "delete") or message.created_by == caller.user.id):
        raise HTTPException(403, "You do not have permission to do this")
    if message.status == "queued":
        message.status = "cancelled"
    message.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="outbox.deleted", target_type="outbox_message", target_id=message.id)
    db.commit()
    return {"ok": True}


# ---------- notification bell ----------

def _signals(db: Session, caller: Caller) -> list[dict[str, Any]]:
    """What needs this person's attention right now, worked out from live records. Each kind of
    signal is only produced for someone who may already see the records behind it."""
    org = db.get(Organisation, caller.org_id)
    today = people.today_for(org)
    window = people.cert_settings(org)["expiry_window_days"]
    out: list[dict[str, Any]] = []

    def add(nid: str, category: str, title: str, subtitle: str | None, severity: str, link: str, at: Any) -> None:
        out.append({"id": nid, "category": category, "title": title, "subtitle": subtitle, "severity": severity, "link": link, "created_at": iso(at) if at else None})

    # Certificates: the roster for team viewers, otherwise the person's own.
    if caller.can("certifications", "view"):
        stmt = select(Certification, Worker).join(Worker, Worker.id == Certification.worker_id).where(
            Certification.org_id == caller.org_id, Certification.deleted_at.is_(None), Certification.superseded_by_id.is_(None),
            Certification.archived_at.is_(None), Worker.deleted_at.is_(None), Worker.archived_at.is_(None), Worker.active.is_(True),
            Certification.no_expiry.is_(False), Certification.expiry_date.is_not(None), Certification.expiry_date <= today + timedelta(days=window))
        team = caller.can("workers", "team_view")
        if not team:
            stmt = stmt.where(Worker.id == (caller.user.worker_id or ""))
        labels = people.kind_labels(db, caller.org_id)
        for cert, worker in db.execute(stmt.order_by(Certification.expiry_date).limit(60)).all():
            expired = cert.expiry_date < today
            who = people.worker_name(worker) if team else "Your"
            label = labels.get(cert.kind_slug, cert.kind_slug)
            # The id includes the expiry date, so a renewal that later expires is a new notification.
            add(f"cert-{'expired' if expired else 'expiring'}:{cert.id}:{cert.expiry_date.isoformat()}", "certifications",
                f"{who}{' ' if not team else ': '}{label} {'has expired' if expired else 'expires soon'}",
                f"{'Expired' if expired else 'Expires'} {cert.expiry_date.strftime('%d/%m/%Y')}", "bad" if expired else "warn",
                f"/app/workers/{worker.id}" if team else "/app/profile", cert.updated_at)

    if caller.can("renewals", "view"):
        for link in db.scalars(select(RenewalLink).where(RenewalLink.org_id == caller.org_id, RenewalLink.deleted_at.is_(None), RenewalLink.status == "submitted")
                               .order_by(RenewalLink.submitted_at.desc()).limit(30)).all():
            entity = db.get(Supplier if link.entity_type == "supplier" else Worker, link.entity_id)
            name = entity.name if isinstance(entity, Supplier) else people.worker_name(entity) if entity else "Unknown"
            add(f"renewal-submitted:{link.id}:{link.round_no}", "renewals", f"{name} has uploaded documents to check", "Open Renewals to accept or reject them", "warn", "/app/renewals", link.submitted_at)

    if caller.is_admin:
        for job in db.scalars(select(DailyJob).where(DailyJob.org_id == caller.org_id, DailyJob.deleted_at.is_(None), DailyJob.status == "declined", DailyJob.job_date >= today)
                              .order_by(DailyJob.responded_at.desc()).limit(30)).all():
            add(f"job-declined:{job.id}", "jobs", f"{job.worker_name} cannot do the job at {job.site_name}", job.decline_reason, "bad", "/app/daily-jobs", job.responded_at)

    if caller.can("sites_visitors", "view"):
        cutoff = utcnow() - timedelta(hours=16)
        stale = db.scalar(select(func.count()).select_from(SiteAttendance).where(
            SiteAttendance.org_id == caller.org_id, SiteAttendance.kind == "visitor", SiteAttendance.signed_out_at.is_(None),
            SiteAttendance.deleted_at.is_(None), SiteAttendance.signed_in_at < cutoff)) or 0
        if stale:
            add(f"visitors-stale:{today.isoformat()}", "sites", f"{stale} visitor{'s' if stale != 1 else ''} still signed in from an earlier day",
                "They probably forgot to sign out", "warn", "/app/visitors", None)

    worker = people.worker_for_user(db, caller.user)
    if worker is not None:
        job = db.scalars(select(DailyJob).where(DailyJob.worker_id == worker.id, DailyJob.job_date == today, DailyJob.status == "issued", DailyJob.deleted_at.is_(None))).first()
        if job is not None:
            add(f"job-issued:{job.id}", "jobs", f"You have a job today: {job.site_name}", "Open My work to accept it", "info", "/app/my-work", job.issued_at)
    return out


@notifications_router.get("")
def list_notifications(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Unread items first. Read items stay only while the thing they point at is still true."""
    signals = _signals(db, caller)
    read = set(db.scalars(select(NotificationRead.notification_id).where(NotificationRead.user_id == caller.user.id)).all())
    items = [{**s, "read": s["id"] in read} for s in signals]
    unread = [i for i in items if not i["read"]]
    per_category: dict[str, int] = {}
    for item in unread:
        per_category[item["category"]] = per_category.get(item["category"], 0) + 1
    return {"items": unread + [i for i in items if i["read"]][:10], "unread_count": len(unread), "per_category_unread": per_category}


def _mark(db: Session, caller: Caller, ids: list[str]) -> int:
    have = set(db.scalars(select(NotificationRead.notification_id).where(NotificationRead.user_id == caller.user.id, NotificationRead.notification_id.in_(ids))).all()) if ids else set()
    new = [i for i in dict.fromkeys(ids) if i not in have]
    for nid in new:
        db.add(NotificationRead(org_id=caller.org_id, user_id=caller.user.id, notification_id=nid))
    db.commit()
    return len(new)


@notifications_router.post("/mark-all-read")
def mark_all_read(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"marked": _mark(db, caller, [s["id"] for s in _signals(db, caller)])}


@notifications_router.post("/{notification_id}/read")
def mark_read(notification_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Mark one as read. Only an item this person can actually see can be marked; repeating it is harmless."""
    if notification_id not in {s["id"] for s in _signals(db, caller)}:
        raise HTTPException(404, "Notification not found")
    _mark(db, caller, [notification_id])
    return {"ok": True}
