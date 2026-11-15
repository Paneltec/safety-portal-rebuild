"""Authentication and authorisation dependencies.

Every protected route resolves the caller from the bearer token, then checks
permission on the server. Hiding a button in the UI is never the enforcement.
"""
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import permissions as perms
from .config import get_settings
from .db import get_db
from .models import User, UserSession, utcnow
from .security import hash_token
from .services import effective_for

# While a password change is required, only these paths are reachable.
_PASSWORD_CHANGE_ALLOWED = {"/api/auth/me", "/api/auth/change-password", "/api/auth/logout"}


@dataclass
class Caller:
    user: User
    session: UserSession
    effective: perms.Matrix

    @property
    def org_id(self) -> str:
        return self.user.org_id

    @property
    def is_admin(self) -> bool:
        return self.user.role_id == "admin"

    def can(self, resource: str, action: str) -> bool:
        return perms.can(self.effective, resource, action)


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Not signed in", headers={"WWW-Authenticate": "Bearer"})
    return token.strip()


def get_caller(request: Request, db: Session = Depends(get_db)) -> Caller:
    settings = get_settings()
    token = _bearer(request)
    session = db.scalars(select(UserSession).where(UserSession.token_hash == hash_token(token))).first()
    now = utcnow()
    if session is None or session.revoked_at is not None:
        raise HTTPException(401, "Your session has ended. Please sign in again.")
    if session.expires_at <= now:
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    if not session.remember_me and now - session.last_seen_at > timedelta(minutes=settings.idle_timeout_minutes):
        session.revoked_at = now
        session.revoke_reason = "idle_timeout"
        db.commit()
        raise HTTPException(401, "You were signed out after a period of inactivity.")

    user = db.get(User, session.user_id)
    if user is None or user.deleted_at is not None or user.status != "active":
        raise HTTPException(401, "Account disabled — contact your administrator")

    if user.must_change_password and request.url.path not in _PASSWORD_CHANGE_ALLOWED:
        raise HTTPException(403, "Password change required")

    # Record activity at most once a minute to avoid a write on every request.
    if now - session.last_seen_at > timedelta(seconds=60):
        session.last_seen_at = now
        db.commit()

    return Caller(user=user, session=session, effective=effective_for(db, user))


def require_permission(resource: str, action: str):
    def dependency(caller: Caller = Depends(get_caller)) -> Caller:
        if not caller.can(resource, action):
            raise HTTPException(403, "You do not have permission to do this")
        return caller

    return dependency


def require_admin(caller: Caller = Depends(get_caller)) -> Caller:
    if not caller.is_admin:
        raise HTTPException(403, "Admin only")
    return caller


def ensure_can_grant(caller: Caller, matrix: perms.Matrix) -> None:
    """A non-admin can never hand out access they do not hold themselves."""
    if caller.is_admin:
        return
    for resource, cells in matrix.items():
        for action, value in cells.items():
            if value is True and not caller.can(resource, action):
                raise HTTPException(403, "You cannot grant access that you do not hold yourself")
