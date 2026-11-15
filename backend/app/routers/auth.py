"""Sign-in, sessions, own profile, invitations and password resets."""
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Caller, get_caller
from ..models import Organisation, User, utcnow
from ..security import hash_password, hash_token, new_token, password_policy_error, verify_password
from ..services import (
    audit,
    create_session,
    find_valid_auth_token,
    hold_message,
    iso,
    issue_auth_token,
    link_for,
    revoke_user_sessions,
    user_out,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

INVALID_LOGIN = "Email or password is incorrect"
LOCKED = (
    "Too many wrong tries. Please wait 5 minutes and try again, "
    "or ask your administrator to unlock the account."
)


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool | None = False


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class UpdateProfileIn(BaseModel):
    name: str | None = None
    email: EmailStr | None = None


class ForgotIn(BaseModel):
    email: EmailStr


class TokenIn(BaseModel):
    token: str = Field(min_length=1)


class RedeemIn(BaseModel):
    token: str = Field(min_length=1)
    password: str
    confirm_password: str


def _token_response(db: Session, user: User, token: str, session) -> dict[str, Any]:
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": iso(session.expires_at),
        "user": user_out(db, user),
    }


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    email = body.email.strip().lower()
    user = db.scalars(select(User).where(User.email == email, User.deleted_at.is_(None))).first()
    now = utcnow()

    if user is not None and user.locked_until is not None and user.locked_until > now:
        raise HTTPException(423, LOCKED)

    password_ok = verify_password(user.password_hash if user else None, body.password)
    if user is None:
        raise HTTPException(401, INVALID_LOGIN)

    if not password_ok:
        user.failed_login_attempts += 1
        detail = INVALID_LOGIN
        status = 401
        if user.failed_login_attempts >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            user.failed_login_attempts = 0
            audit(db, request, org_id=user.org_id, actor_id=None, action="user.locked",
                  target_type="user", target_id=user.id, detail={"reason": "too_many_failed_logins"})
            detail, status = LOCKED, 423
        db.commit()
        raise HTTPException(status, detail)

    if user.status == "disabled":
        raise HTTPException(401, "Account disabled — contact your administrator")
    if user.status != "active":
        raise HTTPException(403, "Your account is being set up. Please contact your administrator to activate it.")

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    session, token = create_session(db, user, request, remember_me=bool(body.remember_me))
    audit(db, request, org_id=user.org_id, actor_id=user.id, action="auth.login",
          target_type="session", target_id=session.id, detail={"remember_me": session.remember_me})
    db.commit()
    return _token_response(db, user, token, session)


@router.get("/me")
def me(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    org = db.get(Organisation, caller.org_id)
    return {
        **user_out(db, caller.user),
        "org_name": org.name if org else "",
        "org_timezone": org.timezone if org else "Australia/Hobart",
        "effective_permissions": caller.effective,
        "session": {
            "jti": caller.session.id,
            "expires_at": iso(caller.session.expires_at),
            "remember_me": caller.session.remember_me,
        },
    }


@router.post("/logout")
def logout(request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, bool]:
    caller.session.revoked_at = utcnow()
    caller.session.revoke_reason = "logout"
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="auth.logout",
          target_type="session", target_id=caller.session.id)
    db.commit()
    return {"ok": True}


@router.post("/refresh")
def refresh(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Slide the session window and rotate the bearer token. The old token stops working."""
    settings = get_settings()
    session = caller.session
    token = new_token()
    session.token_hash = hash_token(token)
    lifetime = (
        timedelta(days=settings.remember_me_ttl_days) if session.remember_me else timedelta(hours=settings.session_ttl_hours)
    )
    session.expires_at = utcnow() + lifetime
    session.last_seen_at = utcnow()
    db.commit()
    return _token_response(db, caller.user, token, session)


@router.post("/change-password")
def change_password(
    body: ChangePasswordIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)
) -> dict[str, Any]:
    user = caller.user
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(401, "Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(400, "New password must differ from the current one")
    error = password_policy_error(body.new_password)
    if error:
        raise HTTPException(400, error)

    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.last_password_change_at = utcnow()
    # Sign out everywhere else, and rotate this session's token.
    revoke_user_sessions(db, user.id, "password_changed", except_session_id=caller.session.id)
    token = new_token()
    caller.session.token_hash = hash_token(token)
    audit(db, request, org_id=user.org_id, actor_id=user.id, action="auth.password_changed",
          target_type="user", target_id=user.id)
    db.commit()
    return {"ok": True, "access_token": token, "token_type": "bearer"}


@router.post("/update-profile")
def update_profile(
    body: UpdateProfileIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)
) -> dict[str, Any]:
    user = caller.user
    changes: dict[str, Any] = {}
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Name cannot be empty")
        if name != user.name:
            changes["name"] = {"from": user.name, "to": name}
            user.name = name
    if body.email is not None:
        email = body.email.strip().lower()
        if email != user.email:
            taken = db.scalars(select(User.id).where(User.email == email, User.id != user.id)).first()
            if taken:
                raise HTTPException(400, "Email already in use")
            changes["email"] = {"from": user.email, "to": email}
            user.email = email
    if body.name is None and body.email is None:
        raise HTTPException(400, "No fields to update")
    if changes:
        audit(db, request, org_id=user.org_id, actor_id=user.id, action="user.profile_updated",
              target_type="user", target_id=user.id, detail=changes)
        db.commit()
    return {"ok": True, "user": user_out(db, user)}


@router.post("/forgot-password")
def forgot_password(body: ForgotIn, request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    """Always answers ok, so the response never reveals whether an email is registered."""
    email = body.email.strip().lower()
    user = db.scalars(select(User).where(User.email == email, User.deleted_at.is_(None))).first()
    if user is not None and user.status == "active":
        row, token = issue_auth_token(db, user, "reset", created_by=None)
        text = (f"Hello {user.name},\n\nUse this link to choose a new password:\n{link_for(request, '/reset-password', token)}\n\n"
                "It works once. If you did not ask for this, ignore this message.")
        # Sent only when email is connected and safe mode is off; otherwise held, and an
        # administrator creates a reset link instead. The text is never stored.
        hold_message(db, org_id=user.org_id, channel="email", purpose="password_reset", recipient=user.email,
                     subject="Reset your Safety Portal password", related_type="auth_token", related_id=row.id, created_by=None,
                     request=request, body=text, store_body=False)
        audit(db, request, org_id=user.org_id, actor_id=None, action="auth.reset_requested",
              target_type="user", target_id=user.id)
        db.commit()
    return {"ok": True}


def _validate(db: Session, token: str, kind: str) -> dict[str, Any]:
    row = find_valid_auth_token(db, token, kind)
    user = db.get(User, row.user_id)
    if user is None or user.deleted_at is not None or user.status == "disabled":
        raise HTTPException(400, "This link has already been used or revoked.")
    org = db.get(Organisation, user.org_id)
    return {
        "user_email": user.email,
        "user_name": user.name,
        "org_name": org.name if org else "",
        "expires_at": iso(row.expires_at),
    }


def _redeem(db: Session, request: Request, body: RedeemIn, kind: str) -> dict[str, Any]:
    row = find_valid_auth_token(db, body.token, kind)
    user = db.get(User, row.user_id)
    if user is None or user.deleted_at is not None or user.status == "disabled":
        raise HTTPException(400, "This link has already been used or revoked.")
    if body.password != body.confirm_password:
        raise HTTPException(400, "The two passwords do not match")
    error = password_policy_error(body.password)
    if error:
        raise HTTPException(400, error)

    row.used_at = utcnow()
    user.password_hash = hash_password(body.password)
    user.must_change_password = False
    user.last_password_change_at = utcnow()
    user.failed_login_attempts = 0
    user.locked_until = None
    if user.status == "invited":
        # Both link kinds are issued by an authorised administrator for an invited account.
        user.status = "active"
    revoke_user_sessions(db, user.id, f"{kind}_redeemed")
    user.last_login_at = utcnow()
    session, token = create_session(db, user, request, remember_me=False)
    audit(db, request, org_id=user.org_id, actor_id=user.id, action=f"auth.{kind}_redeemed",
          target_type="user", target_id=user.id)
    db.commit()
    return _token_response(db, user, token, session)


@router.post("/invite/validate")
def invite_validate(body: TokenIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _validate(db, body.token, "invite")


@router.post("/invite/redeem")
def invite_redeem(body: RedeemIn, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _redeem(db, request, body, "invite")


@router.post("/reset/validate")
def reset_validate(body: TokenIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _validate(db, body.token, "reset")


@router.post("/reset/redeem")
def reset_redeem(body: RedeemIn, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _redeem(db, request, body, "reset")


@router.post("/signup", status_code=410)
def signup_retired() -> dict[str, str]:
    """Public signup is retired. Accounts are created by an administrator or by import."""
    raise HTTPException(410, "Public signup is retired. Ask your administrator for an invitation.")
