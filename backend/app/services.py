"""Shared building blocks used by the routers: roles, effective permissions,
sessions, one-time links, audit and the outbound-message hold."""
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import permissions as perms
from .config import get_settings
from .models import (
    AuditLog,
    AuthToken,
    Organisation,
    OutboxMessage,
    Role,
    User,
    UserPermissionOverride,
    UserSession,
    utcnow,
)
from .security import hash_token, new_token


# ---------- organisations and roles ----------

def seed_system_roles(db: Session, org_id: str) -> None:
    """Create any missing built-in roles for an organisation. Safe to run repeatedly."""
    existing = set(db.scalars(select(Role.role_id).where(Role.org_id == org_id)))
    for key, meta in perms.SYSTEM_ROLES.items():
        if key not in existing:
            db.add(
                Role(
                    org_id=org_id,
                    role_id=key,
                    name=meta["name"],
                    description="Built-in role",
                    is_system=True,
                    legacy_role=meta["legacy_role"],
                    permissions=perms.system_role_matrix(key),
                )
            )
    db.flush()


def create_organisation(db: Session, name: str) -> Organisation:
    org = Organisation(name=name)
    db.add(org)
    db.flush()
    seed_system_roles(db, org.id)
    return org


def get_role(db: Session, org_id: str, role_id: str, *, include_deleted: bool = False) -> Role | None:
    stmt = select(Role).where(Role.org_id == org_id, Role.role_id == role_id)
    if not include_deleted:
        stmt = stmt.where(Role.deleted_at.is_(None))
    return db.scalars(stmt).first()


def require_assignable_role(db: Session, org_id: str, role_id: str) -> Role:
    role = get_role(db, org_id, role_id)
    if role is None:
        raise HTTPException(404, f"role_id '{role_id}' not found")
    if not role.is_active:
        raise HTTPException(400, "role is not active")
    return role


# ---------- effective permissions ----------

def is_payroll_owner(db: Session, user: User) -> bool:
    org = db.get(Organisation, user.org_id)
    return org is not None and org.payroll_owner_user_id == user.id


def is_account_holder(db: Session, user: User) -> bool:
    org = db.get(Organisation, user.org_id)
    return org is not None and org.owner_user_id == user.id


def get_overrides(db: Session, user_id: str) -> UserPermissionOverride | None:
    return db.get(UserPermissionOverride, user_id)


def effective_for(db: Session, user: User) -> perms.Matrix:
    role = get_role(db, user.org_id, user.role_id, include_deleted=True)
    # A missing or inactive role grants nothing; explicit overrides still apply.
    role_matrix = role.permissions if role is not None and role.is_active and role.deleted_at is None else {}
    override_row = get_overrides(db, user.id)
    return perms.effective_permissions(
        role_matrix,
        override_row.overrides if override_row else None,
        payroll_owner=is_payroll_owner(db, user),
    )


def user_out(db: Session, user: User) -> dict[str, Any]:
    role = get_role(db, user.org_id, user.role_id, include_deleted=True)
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        # `role` is the built-in key, or the phone role key for a custom role.
        "role": user.role_id if role is not None and role.is_system else (role.legacy_role if role else "worker"),
        "role_id": user.role_id,
        "role_name": role.name if role else user.role_id,
        "role_locked": user.role_locked,
        "org_id": user.org_id,
        "company_id": user.company_id,
        "workspace_ids": list(user.workspace_ids or []),
        "worker_id": user.worker_id,
        "status": user.status,
        "activation_status": "pending" if user.status == "invited" else "active",
        "must_change_password": user.must_change_password,
        "last_login_at": iso(user.last_login_at),
        "created_at": iso(user.created_at),
        "deleted_at": iso(user.deleted_at),
        "is_test": user.is_test,
        "is_account_holder": is_account_holder(db, user),
    }


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def active_admin_count(db: Session, org_id: str, *, excluding_user_id: str | None = None) -> int:
    stmt = select(func.count()).select_from(User).where(
        User.org_id == org_id, User.role_id == "admin", User.status == "active", User.deleted_at.is_(None)
    )
    if excluding_user_id:
        stmt = stmt.where(User.id != excluding_user_id)
    return int(db.scalar(stmt) or 0)


# ---------- sessions ----------

def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def create_session(db: Session, user: User, request: Request, *, remember_me: bool) -> tuple[UserSession, str]:
    settings = get_settings()
    token = new_token()
    lifetime = (
        timedelta(days=settings.remember_me_ttl_days) if remember_me else timedelta(hours=settings.session_ttl_hours)
    )
    session = UserSession(
        org_id=user.org_id,
        user_id=user.id,
        token_hash=hash_token(token),
        remember_me=remember_me,
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:400] or None,
        expires_at=utcnow() + lifetime,
    )
    db.add(session)
    db.flush()
    return session, token


def revoke_user_sessions(db: Session, user_id: str, reason: str, *, except_session_id: str | None = None) -> int:
    stmt = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoke_reason=reason)
    )
    if except_session_id:
        stmt = stmt.where(UserSession.id != except_session_id)
    return db.execute(stmt).rowcount or 0


def session_out(session: UserSession, *, current_id: str | None = None, user: User | None = None) -> dict[str, Any]:
    now = utcnow()
    if session.revoked_at is not None:
        state = "revoked"
    elif session.expires_at <= now:
        state = "expired"
    else:
        state = "active"
    out: dict[str, Any] = {
        "jti": session.id,
        "user_id": session.user_id,
        "state": state,
        "remember_me": session.remember_me,
        "ip": session.ip,
        "user_agent": session.user_agent,
        "created_at": iso(session.created_at),
        "last_seen_at": iso(session.last_seen_at),
        "expires_at": iso(session.expires_at),
        "revoked_at": iso(session.revoked_at),
        "revoke_reason": session.revoke_reason,
        "is_current": session.id == current_id,
    }
    if user is not None:
        out["user_name"] = user.name
        out["user_email"] = user.email
    return out


# ---------- one-time links ----------

def issue_auth_token(db: Session, user: User, kind: str, *, created_by: str | None) -> tuple[AuthToken, str]:
    """Create a single-use invite or reset link. Earlier unused links of the same kind are revoked."""
    settings = get_settings()
    db.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.kind == kind, AuthToken.used_at.is_(None), AuthToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    token = new_token()
    ttl = timedelta(hours=settings.invite_ttl_hours) if kind == "invite" else timedelta(minutes=settings.reset_ttl_minutes)
    row = AuthToken(
        org_id=user.org_id,
        user_id=user.id,
        kind=kind,
        token_hash=hash_token(token),
        expires_at=utcnow() + ttl,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row, token


def find_valid_auth_token(db: Session, token: str, kind: str) -> AuthToken:
    row = db.scalars(select(AuthToken).where(AuthToken.token_hash == hash_token(token), AuthToken.kind == kind)).first()
    if row is None or row.used_at is not None or row.revoked_at is not None:
        raise HTTPException(400, "This link has already been used or revoked.")
    if row.expires_at <= utcnow():
        raise HTTPException(400, "This link has expired. Ask your administrator for a new one.")
    return row


def portal_base_url(request: Request) -> str:
    """Public address of the portal, used in one-time links.

    The configured PUBLIC_BASE_URL always wins. Without it, use the address the
    administrator's browser is on (Origin), so a reverse proxy cannot leak the
    internal API host into a link.
    """
    configured = get_settings().public_base_url.rstrip("/")
    if configured:
        return configured
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin.startswith(("http://", "https://")) and "/" not in origin.split("://", 1)[1]:
        return origin
    return str(request.base_url).rstrip("/")


def link_for(request: Request, path: str, token: str) -> str:
    return f"{portal_base_url(request)}{path}?token={token}"


# ---------- audit and outbound messages ----------

_SECRET_KEYS = {"password", "password_hash", "token", "access_token", "token_hash", "current_password", "new_password"}


def audit(
    db: Session,
    request: Request | None,
    *,
    org_id: str,
    actor_id: str | None,
    action: str,
    target_type: str,
    target_id: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    safe = {k: v for k, v in (detail or {}).items() if k not in _SECRET_KEYS}
    db.add(
        AuditLog(
            org_id=org_id,
            actor_user_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=safe,
            request_id=getattr(request.state, "request_id", None) if request is not None else None,
        )
    )


def hold_message(
    db: Session,
    *,
    org_id: str,
    channel: str,
    purpose: str,
    recipient: str,
    subject: str,
    related_type: str | None,
    related_id: str | None,
    created_by: str | None,
) -> OutboxMessage:
    """The single outbound boundary. Stage 1 never delivers: every message is held.

    The message body (and any link token) is deliberately not stored here.
    """
    message = OutboxMessage(
        org_id=org_id,
        channel=channel,
        purpose=purpose,
        recipient=recipient,
        subject=subject,
        status="held",
        hold_reason="Delivery is not connected yet (stage 1). Nothing was sent.",
        related_type=related_type,
        related_id=related_id,
        created_by=created_by,
    )
    db.add(message)
    return message
