"""The one place a message can leave the portal.

Every email and text goes through `deliver`. It decides, in this order:

  1. duplicate     a message with the same dedupe key already exists: return that one
  2. safe mode     on (the default, and it can be locked on by the server): hold
  3. no request    called from a background job rather than a person's action: hold
  4. provider      simulator: record as simulated; live and tested: send; otherwise hold
  5. budget        texts stop when the day's estimated spend reaches the limit

Nothing else in the code calls a provider's send. A held message is a record, not a queue that
drains by itself: someone has to press Send again once the reason is fixed.
"""
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import integrations
from .config import get_settings
from .models import Organisation, OutboxAttempt, OutboxMessage, utcnow

HOLD_TEXT = {
    "safe_mode": "Safe mode is on, so nothing is sent. The message is kept here.",
    "not_connected": "{provider} is not connected, so this was not sent.",
    "no_request": "This was prepared by an automatic job. Automatic jobs never send; a person has to send it.",
    "budget": "Today's limit for text messages has been reached, so this was not sent.",
    "no_body": "This message carried a one-time link, which is never stored. Create a new link to send it again.",
}
PROVIDER = {"email": "Email (Microsoft 365)", "sms": "Text messaging (TextMagic)"}
OPEN_STATES = ("held", "queued", "sent", "simulated")


def safe_mode(org: Organisation) -> dict[str, Any]:
    """Whether sending is blocked, and who decided. The server setting wins over the organisation's."""
    env_value = (get_settings().comms_safe_mode or "on").strip().lower()
    env_locked = env_value != "off"
    org_value = "off" if (org.details or {}).get("comms_safe_mode") == "off" else "on"
    return {"effective": "on" if env_locked or org_value == "on" else "off", "env_locked": env_locked,
            "env_value": "on" if env_locked else "off", "org_value": org_value}


def _hold(message: OutboxMessage, code: str) -> None:
    message.status = "held"
    message.hold_code = code
    message.hold_reason = HOLD_TEXT[code].format(provider=PROVIDER.get(message.channel, "The provider"))


def _over_budget(db: Session, org: Organisation) -> bool:
    config = integrations.get_config(db, org.id, "textmagic")
    if config is None:
        return False
    settings = integrations.effective_settings(integrations.CATALOGUE["textmagic"], config)
    budget, each = float(settings.get("daily_budget_aud") or 0), float(settings.get("estimated_cost_per_sms_aud") or 0)
    if each <= 0:
        return False
    try:
        zone = ZoneInfo(org.timezone or "Australia/Hobart")
    except Exception:
        zone = ZoneInfo("Australia/Hobart")
    start = datetime.combine(datetime.now(zone).date(), time.min, zone).astimezone(timezone.utc)
    sent = db.scalar(select(func.count()).select_from(OutboxMessage).where(
        OutboxMessage.org_id == org.id, OutboxMessage.channel == "sms", OutboxMessage.status == "sent", OutboxMessage.sent_at >= start)) or 0
    return (sent + 1) * each > budget


def _attempt(db: Session, message: OutboxMessage, body: str | None, request: Request | None, actor: str | None) -> None:
    """Apply the rules to one message and record what happened."""
    org = db.get(Organisation, message.org_id)
    message.hold_code = message.hold_reason = None
    if safe_mode(org)["effective"] == "on":
        _hold(message, "safe_mode")
    elif request is None:
        _hold(message, "no_request")
    elif body is None:
        _hold(message, "no_body")
    elif message.channel == "sms" and _over_budget(db, org):
        _hold(message, "budget")
    else:
        result = integrations.send(db, message.org_id, message.channel, recipient=message.recipient, subject=message.subject, body=body)
        if result is None:
            _hold(message, "not_connected")
        else:
            message.attempts += 1
            message.status = result.outcome
            message.last_error = result.detail[:400] if result.outcome == "failed" else None
            if result.outcome == "sent":
                message.sent_at = utcnow()
                message.provider_message_id = result.provider_message_id
            db.add(OutboxAttempt(org_id=message.org_id, message_id=message.id, outcome=result.outcome, detail=result.detail[:400], actor_user_id=actor))
            return
    db.add(OutboxAttempt(org_id=message.org_id, message_id=message.id, outcome="held", detail=message.hold_reason, actor_user_id=actor))


def deliver(db: Session, request: Request | None, *, org_id: str, channel: str, purpose: str, recipient: str, subject: str,
            body: str | None, store_body: bool, related_type: str | None, related_id: str | None, created_by: str | None,
            dedupe_key: str | None = None) -> OutboxMessage:
    """Record a message and, if every rule allows it, send it. Returns the outbox row either way.

    `store_body=False` is for messages containing a one-time link: the text is used for this
    attempt only and is never written to the database.
    """
    if dedupe_key:
        existing = db.scalars(select(OutboxMessage).where(
            OutboxMessage.org_id == org_id, OutboxMessage.dedupe_key == dedupe_key, OutboxMessage.deleted_at.is_(None),
            OutboxMessage.status.in_(OPEN_STATES))).first()
        if existing is not None:
            return existing
    message = OutboxMessage(org_id=org_id, channel=channel, purpose=purpose, recipient=recipient, subject=subject[:300],
                            body_text=body if store_body else None, dedupe_key=dedupe_key, related_type=related_type,
                            related_id=related_id, created_by=created_by, status="queued")
    db.add(message)
    db.flush()
    _attempt(db, message, body, request, created_by)
    return message


def retry(db: Session, request: Request, message: OutboxMessage, actor: str | None) -> None:
    """Try again. Only a message whose text was kept can be retried; a sent message never is."""
    _attempt(db, message, message.body_text, request, actor)


def note_for(message: OutboxMessage | None, *, no_recipient: str = "No contact detail is recorded, so no message was prepared.") -> str:
    """One plain sentence about what happened to a message, for the screen that created it."""
    if message is None:
        return no_recipient
    if message.status == "sent":
        return "Sent. The provider accepted it for delivery."
    if message.status == "simulated":
        return "Handled by the simulator. Nothing was really sent."
    if message.status == "failed":
        return f"Sending failed: {message.last_error or 'the provider refused it'}"
    return message.hold_reason or "Not sent."


def link_note(message: OutboxMessage | None) -> str:
    """For screens that show a one-time link: say whether it was also sent."""
    if message is not None and message.status == "sent":
        return f"Emailed to {message.recipient}. The link is also shown here once, in case it does not arrive."
    return note_for(message, no_recipient="No email address is recorded for them, so no message was prepared.") + " Pass this link to the person directly."
