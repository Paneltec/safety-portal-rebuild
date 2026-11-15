"""User administration: accounts, role assignment, per-user permission overrides."""
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import permissions as perms
from ..db import get_db
from ..deps import Caller, ensure_can_grant, require_admin, require_permission
from ..models import AuthToken, PermissionPreset, Role, User, UserPermissionOverride, utcnow
from ..security import hash_password, password_policy_error
from ..services import (
    active_admin_count,
    audit,
    effective_for,
    get_overrides,
    get_role,
    hold_message,
    iso,
    is_payroll_owner,
    issue_auth_token,
    link_for,
    require_assignable_role,
    revoke_user_sessions,
    user_out,
)

router = APIRouter(tags=["users"])


class CreateUserIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    role_id: str = "worker"
    company_id: str | None = None


class UpdateUserIn(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    role: str | None = None  # built-in role key; same effect as role_id
    role_id: str | None = None
    workspace_ids: list[str] | None = None
    status: Literal["active", "invited", "disabled"] | None = None
    role_locked: bool | None = None
    company_id: str | None = None


class SetPasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=128)
    must_change_password: bool = True


class PermissionsIn(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)
    reasons: dict[str, Any] = Field(default_factory=dict)


class ApplyPresetIn(BaseModel):
    preset_key: str | None = None  # a role_id from the catalogue
    preset_id: str | None = None  # a custom preset


class BulkAssignRoleIn(BaseModel):
    user_ids: list[str] = Field(max_length=500)
    role_id: str


class BulkRestrictIn(BaseModel):
    user_ids: list[str] = Field(min_length=1, max_length=500)
    resource: str
    action: str
    value: bool = False
    reason: str | None = None


def _get_user(db: Session, caller: Caller, user_id: str, *, include_deleted: bool = False) -> User:
    """Load a user inside the caller's organisation. Other tenants' IDs look like missing records."""
    user = db.get(User, user_id)
    if user is None or user.org_id != caller.org_id or (user.deleted_at is not None and not include_deleted):
        raise HTTPException(404, "User not found")
    return user


def _guard_role_change(db: Session, caller: Caller, target: User, new_role_id: str) -> None:
    if new_role_id == target.role_id:
        return
    if (new_role_id == "admin" or target.role_id == "admin") and not caller.is_admin:
        raise HTTPException(403, "Only an admin can grant or remove the admin role")
    if target.role_id == "admin" and target.status == "active" and active_admin_count(db, caller.org_id, excluding_user_id=target.id) == 0:
        raise HTTPException(400, "Cannot change the role of the last active admin in this organisation")
    if target.id == caller.user.id and not caller.is_admin:
        raise HTTPException(403, "You cannot change your own role")
    new_role = get_role(db, caller.org_id, new_role_id)
    if new_role is not None:
        ensure_can_grant(caller, new_role.permissions)


def _permissions_payload(db: Session, user: User) -> dict[str, Any]:
    role = get_role(db, user.org_id, user.role_id, include_deleted=True)
    row = get_overrides(db, user.id)
    owner = is_payroll_owner(user)
    return {
        "user_id": user.id,
        "role": user.role_id,
        "role_defaults": role.permissions if role else {},
        "overrides": row.overrides if row else {},
        "reasons": row.reasons if row else {},
        "applied_preset_key": row.applied_preset_key if row else None,
        "effective": effective_for(db, user),
        "schema": {"actions": perms.ACTIONS, "resources": perms.RESOURCES},
        "payroll_access": {"is_owner": owner, "can_manage": owner, "grants": []},
    }


# ---------- list and create ----------

@router.get("/api/users")
def list_users(
    q: str | None = Query(None, max_length=100),
    status: Literal["active", "invited", "disabled"] | None = None,
    role_id: str | None = None,
    include_deleted: bool = False,
    hide_test: bool = True,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    caller: Caller = Depends(require_permission("users", "view")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(User).where(User.org_id == caller.org_id)
    if not include_deleted:
        stmt = stmt.where(User.deleted_at.is_(None))
    if hide_test:
        stmt = stmt.where(User.is_test.is_(False))
    if status:
        stmt = stmt.where(User.status == status)
    if role_id:
        stmt = stmt.where(User.role_id == role_id)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(User.name).like(like), User.email.like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(func.lower(User.name), User.id).limit(limit).offset(offset)).all()
    return {"items": [user_out(db, u) for u in rows], "total": int(total), "limit": limit, "offset": offset}


@router.post("/api/users", status_code=201)
def create_user(
    body: CreateUserIn,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create an invited account. This never creates a worker record (spec section 3)."""
    email = body.email.strip().lower()
    if db.scalars(select(User.id).where(User.email == email)).first():
        raise HTTPException(409, "Email already in use")
    role = require_assignable_role(db, caller.org_id, body.role_id)
    if role.role_id == "admin" and not caller.is_admin:
        raise HTTPException(403, "Only an admin can grant or remove the admin role")
    ensure_can_grant(caller, role.permissions)
    if body.company_id is not None:
        from ..models import Company

        company = db.get(Company, body.company_id)
        if company is None or company.org_id != caller.org_id:
            raise HTTPException(404, "Company not found")
    user = User(org_id=caller.org_id, email=email, name=body.name.strip(), role_id=role.role_id,
                company_id=body.company_id, status="invited")
    db.add(user)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="user.created",
          target_type="user", target_id=user.id, detail={"email": email, "role_id": role.role_id})
    db.commit()
    return user_out(db, user)


# ---------- bulk operations (static paths registered before /{user_id}) ----------

@router.post("/api/users/bulk-assign-role")
def bulk_assign_role(
    body: BulkAssignRoleIn,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not body.user_ids:
        raise HTTPException(400, "user_ids empty")
    if body.role_id == "admin":
        raise HTTPException(400, "admin role must be assigned one user at a time")
    role = require_assignable_role(db, caller.org_id, body.role_id)
    updated: list[str] = []
    skipped: list[dict[str, str]] = []
    for user_id in dict.fromkeys(body.user_ids):
        user = db.get(User, user_id)
        if user is None or user.org_id != caller.org_id or user.deleted_at is not None:
            skipped.append({"user_id": user_id, "reason": "not found"})
            continue
        if user.role_locked:
            skipped.append({"user_id": user_id, "reason": "role is locked"})
            continue
        try:
            _guard_role_change(db, caller, user, role.role_id)
        except HTTPException as exc:
            skipped.append({"user_id": user_id, "reason": str(exc.detail)})
            continue
        if user.role_id != role.role_id:
            audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="user.role_changed",
                  target_type="user", target_id=user.id, detail={"from": user.role_id, "to": role.role_id, "bulk": True})
            user.role_id = role.role_id
        updated.append(user_id)
    db.commit()
    return {
        "role_id": role.role_id,
        "role_name": role.name,
        "updated": len(updated),
        "skipped": len(skipped),
        "detail": {"updated": updated, "skipped": skipped},
    }


@router.post("/api/permissions/bulk-restrict")
def bulk_restrict(
    body: BulkRestrictIn,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Set one permission cell (default: deny) for many users at once."""
    if body.resource not in perms.RESOURCES:
        raise HTTPException(400, f"Unknown resource: {body.resource}")
    if body.action not in perms.ACTIONS:
        raise HTTPException(400, f"Unknown action: {body.action}")
    try:
        perms.clean_overrides({body.resource: {body.action: body.value}}, allow_special=caller.is_admin)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    ensure_can_grant(caller, {body.resource: {body.action: body.value}})
    updated: list[str] = []
    missing: list[str] = []
    skipped: list[str] = []
    for user_id in dict.fromkeys(body.user_ids):
        user = db.get(User, user_id)
        if user is None or user.org_id != caller.org_id or user.deleted_at is not None:
            missing.append(user_id)
            continue
        if not caller.is_admin and (user.id == caller.user.id or user.role_id == "admin"):
            skipped.append(user_id)
            continue
        row = get_overrides(db, user.id)
        if row is None:
            row = UserPermissionOverride(user_id=user.id, org_id=user.org_id, overrides={}, reasons={})
            db.add(row)
        overrides = {r: dict(cells) for r, cells in (row.overrides or {}).items()}
        overrides.setdefault(body.resource, {})[body.action] = body.value
        row.overrides = overrides
        if body.reason:
            reasons = dict(row.reasons or {})
            reasons[f"{body.resource}.{body.action}"] = body.reason
            row.reasons = reasons
        row.updated_by = caller.user.id
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="user.permissions_changed",
              target_type="user", target_id=user.id,
              detail={"cell": f"{body.resource}.{body.action}", "value": body.value, "bulk": True})
        updated.append(user_id)
    db.commit()
    return {"ok": True, "updated": updated, "missing_user_ids": missing, "skipped_user_ids": skipped,
            "resource": body.resource,
            "action": body.action, "value": body.value, "reason": body.reason}


# ---------- single user ----------

@router.get("/api/users/{user_id}")
def get_user(
    user_id: str, caller: Caller = Depends(require_permission("users", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    user = _get_user(db, caller, user_id, include_deleted=True)
    return {**user_out(db, user), "effective_permissions": effective_for(db, user)}


@router.patch("/api/users/{user_id}")
def update_user(
    user_id: str,
    body: UpdateUserIn,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(400, "No fields to update")
    user = _get_user(db, caller, user_id)
    changes: dict[str, Any] = {}

    if "name" in patch and patch["name"] is not None:
        name = patch["name"].strip()
        if not name:
            raise HTTPException(400, "Name cannot be empty")
        if name != user.name:
            changes["name"] = {"from": user.name, "to": name}
            user.name = name

    if "email" in patch and patch["email"] is not None:
        email = patch["email"].strip().lower()
        if email != user.email:
            if db.scalars(select(User.id).where(User.email == email, User.id != user.id)).first():
                raise HTTPException(400, "Email already in use")
            changes["email"] = {"from": user.email, "to": email}
            user.email = email

    new_role_id = patch.get("role_id") or patch.get("role")
    if new_role_id and new_role_id != user.role_id:
        role = require_assignable_role(db, caller.org_id, new_role_id)
        _guard_role_change(db, caller, user, role.role_id)
        changes["role_id"] = {"from": user.role_id, "to": role.role_id}
        user.role_id = role.role_id

    if "status" in patch and patch["status"] is not None and patch["status"] != user.status:
        new_status = patch["status"]
        if new_status == "disabled":
            if user.id == caller.user.id:
                raise HTTPException(400, "Can't disable your own account")
            if user.role_id == "admin" and active_admin_count(db, caller.org_id, excluding_user_id=user.id) == 0:
                raise HTTPException(400, "Cannot disable the last active admin in this organisation")
            revoke_user_sessions(db, user.id, "account_disabled")
        if new_status == "active" and user.password_hash is None:
            raise HTTPException(400, "This account has no password yet. Send an invitation or set a password first.")
        changes["status"] = {"from": user.status, "to": new_status}
        user.status = new_status

    if "role_locked" in patch and patch["role_locked"] is not None and patch["role_locked"] != user.role_locked:
        changes["role_locked"] = {"from": user.role_locked, "to": patch["role_locked"]}
        user.role_locked = patch["role_locked"]

    if "workspace_ids" in patch and patch["workspace_ids"] is not None:
        ids = list(dict.fromkeys(patch["workspace_ids"]))
        if ids != list(user.workspace_ids or []):
            changes["workspace_ids"] = {"from": list(user.workspace_ids or []), "to": ids}
            user.workspace_ids = ids

    if "company_id" in patch and patch["company_id"] != user.company_id:
        if patch["company_id"] is not None:
            from ..models import Company

            company = db.get(Company, patch["company_id"])
            if company is None or company.org_id != caller.org_id:
                raise HTTPException(404, "Company not found")
        changes["company_id"] = {"from": user.company_id, "to": patch["company_id"]}
        user.company_id = patch["company_id"]

    if changes:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="user.updated",
              target_type="user", target_id=user.id, detail=changes)
        db.commit()
    return user_out(db, user)


@router.delete("/api/users/{user_id}")
def delete_user(
    user_id: str,
    request: Request,
    hard: bool = False,
    caller: Caller = Depends(require_permission("users", "delete")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Soft-delete (disable and hide). A hard delete is only allowed after a soft delete."""
    user = _get_user(db, caller, user_id, include_deleted=True)
    if user.id == caller.user.id:
        raise HTTPException(400, "Can't disable your own account")
    if hard:
        if user.deleted_at is None:
            raise HTTPException(400, "User must be soft-deleted first (set deleted_at) before hard delete")
        if not caller.is_admin:
            raise HTTPException(403, "Admin only")
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="user.hard_deleted",
              target_type="user", target_id=user.id, detail={"email": user.email, "name": user.name})
        db.delete(user)
        db.commit()
        return {"ok": True, "hard_deleted": True}
    if user.deleted_at is not None:
        return {"ok": True, "status": "disabled", "deleted_at": iso(user.deleted_at)}
    if user.role_id == "admin" and user.status == "active" and active_admin_count(db, caller.org_id, excluding_user_id=user.id) == 0:
        raise HTTPException(400, "Cannot delete the last active admin in this org")
    user.status = "disabled"
    user.deleted_at = utcnow()
    revoke_user_sessions(db, user.id, "account_deleted")
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="user.deleted",
          target_type="user", target_id=user.id)
    db.commit()
    return {"ok": True, "status": "disabled", "deleted_at": iso(user.deleted_at)}


# ---------- account access actions ----------

@router.post("/api/users/{user_id}/force-signout")
def force_signout(
    user_id: str,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = _get_user(db, caller, user_id)
    keep = caller.session.id if user.id == caller.user.id else None
    revoked = revoke_user_sessions(db, user.id, "forced_signout", except_session_id=keep)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="user.forced_signout",
          target_type="user", target_id=user.id, detail={"revoked_sessions": revoked})
    db.commit()
    return {"ok": True, "user_id": user.id, "revoked_sessions": revoked}


@router.post("/api/users/{user_id}/unlock")
def unlock_user(
    user_id: str, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, bool]:
    user = _get_user(db, caller, user_id)
    user.failed_login_attempts = 0
    user.locked_until = None
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="user.unlocked",
          target_type="user", target_id=user.id)
    db.commit()
    return {"ok": True}


@router.post("/api/users/{user_id}/set-password")
def admin_set_password(
    user_id: str,
    body: SetPasswordIn,
    request: Request,
    caller: Caller = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """An admin sets a temporary password. By default the user must change it at next sign-in."""
    user = _get_user(db, caller, user_id)
    error = password_policy_error(body.password)
    if error:
        raise HTTPException(400, error)
    user.password_hash = hash_password(body.password)
    user.must_change_password = body.must_change_password
    user.last_password_change_at = utcnow()
    user.failed_login_attempts = 0
    user.locked_until = None
    if user.status == "invited":
        user.status = "active"
    keep = caller.session.id if user.id == caller.user.id else None
    revoke_user_sessions(db, user.id, "password_set_by_admin", except_session_id=keep)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="user.password_set_by_admin",
          target_type="user", target_id=user.id, detail={"must_change_password": body.must_change_password})
    db.commit()
    return {"ok": True, "user_id": user.id}


def _send_link(db: Session, request: Request, caller: Caller, user: User, kind: str) -> dict[str, Any]:
    if user.status == "disabled":
        raise HTTPException(400, "This account is disabled. Re-enable it before sending a link.")
    row, token = issue_auth_token(db, user, kind, created_by=caller.user.id)
    path = "/accept-invite" if kind == "invite" else "/reset-password"
    hold_message(db, org_id=caller.org_id, channel="email",
                 purpose="invitation" if kind == "invite" else "password_reset", recipient=user.email,
                 subject="Your Safety Portal invitation" if kind == "invite" else "Reset your password",
                 related_type="auth_token", related_id=row.id, created_by=caller.user.id)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"user.{kind}_link_created",
          target_type="user", target_id=user.id, detail={"expires_at": iso(row.expires_at)})
    db.commit()
    # The link is shown once to the administrator. It is never stored or logged.
    return {
        "ok": True,
        "channel": "manual",
        "delivery": "held",
        "delivery_note": "Email delivery is not connected yet, so nothing was sent. Pass this link to the person directly.",
        "expires_at": iso(row.expires_at),
        "link": link_for(request, path, token),
    }


@router.post("/api/users/{user_id}/invite", status_code=201)
def send_invite(
    user_id: str, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    user = _get_user(db, caller, user_id)
    if user.status == "active":
        raise HTTPException(400, "This account is already active. Send a password reset instead.")
    return _send_link(db, request, caller, user, "invite")


@router.post("/api/users/{user_id}/reset-password")
def send_reset(
    user_id: str, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    user = _get_user(db, caller, user_id)
    return _send_link(db, request, caller, user, "reset")


@router.get("/api/users/{user_id}/access-status")
def access_status(
    user_id: str, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    user = _get_user(db, caller, user_id, include_deleted=True)
    now = utcnow()
    invite = db.scalars(
        select(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.kind == "invite", AuthToken.used_at.is_(None),
               AuthToken.revoked_at.is_(None), AuthToken.expires_at > now)
        .order_by(AuthToken.created_at.desc())
    ).first()
    locked = user.locked_until is not None and user.locked_until > now
    if user.deleted_at is not None:
        state = "deleted"
    elif user.status == "disabled":
        state = "disabled"
    elif locked:
        state = "locked"
    elif user.status == "invited":
        state = "invite_pending" if invite else "not_invited"
    else:
        state = "active"
    return {
        "state": state,
        "last_login_at": iso(user.last_login_at),
        "last_password_change_at": iso(user.last_password_change_at),
        "invite_expires_at": iso(invite.expires_at) if invite else None,
        "locked_until": iso(user.locked_until) if locked else None,
        "failed_login_attempts": user.failed_login_attempts,
        "must_change_password": user.must_change_password,
    }


# ---------- per-user permissions ----------

@router.get("/api/users/{user_id}/permissions")
def get_permissions(
    user_id: str, caller: Caller = Depends(require_permission("users", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return _permissions_payload(db, _get_user(db, caller, user_id))


def _write_overrides(
    db: Session, request: Request, caller: Caller, user: User, overrides: perms.Matrix,
    reasons: dict[str, Any], preset_key: str | None, action: str,
) -> None:
    if user.id == caller.user.id and not caller.is_admin:
        raise HTTPException(403, "You cannot change your own permissions")
    if user.role_id == "admin" and not caller.is_admin:
        raise HTTPException(403, "Only an admin can change an admin's permissions")
    row = get_overrides(db, user.id)
    before = row.overrides if row else {}
    # Only newly granted cells need the "cannot grant what you do not hold" check.
    newly_granted = {
        r: {a: True for a, v in cells.items() if v is True and (before or {}).get(r, {}).get(a) is not True}
        for r, cells in overrides.items()
    }
    ensure_can_grant(caller, newly_granted)
    if row is None:
        row = UserPermissionOverride(user_id=user.id, org_id=user.org_id)
        db.add(row)
    row.overrides = overrides
    row.reasons = {str(k): str(v)[:500] for k, v in reasons.items() if v}
    row.applied_preset_key = preset_key
    row.updated_by = caller.user.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=action,
          target_type="user", target_id=user.id, detail={"before": before, "after": overrides, "preset": preset_key})
    db.commit()


@router.put("/api/users/{user_id}/permissions")
def put_permissions(
    user_id: str,
    body: PermissionsIn,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Replace the user's explicit allow/deny cells. Cells not listed fall back to the role default."""
    user = _get_user(db, caller, user_id)
    try:
        overrides = perms.clean_overrides(body.overrides, allow_special=caller.is_admin)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _write_overrides(db, request, caller, user, overrides, body.reasons, None, "user.permissions_changed")
    return _permissions_payload(db, user)


@router.post("/api/users/{user_id}/permissions/reset")
def reset_permissions(
    user_id: str,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = _get_user(db, caller, user_id)
    _write_overrides(db, request, caller, user, {}, {}, None, "user.permissions_reset")
    return _permissions_payload(db, user)


@router.post("/api/users/{user_id}/permissions/apply-preset")
def apply_preset(
    user_id: str,
    body: ApplyPresetIn,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Make this user's effective access match a preset, without changing their role.

    Only the cells that differ from the user's own role default are stored as overrides.
    """
    user = _get_user(db, caller, user_id)
    if bool(body.preset_key) == bool(body.preset_id):
        raise HTTPException(400, "Provide exactly one of preset_key or preset_id")
    if body.preset_key:
        source: Role | None = get_role(db, caller.org_id, body.preset_key)
        if source is None:
            raise HTTPException(404, "Preset not found")
        if source.role_id == "admin" and not caller.is_admin:
            raise HTTPException(403, "Only an admin can grant or remove the admin role")
        target_matrix, key, label, builtin = source.permissions, source.role_id, source.name, True
    else:
        preset = db.get(PermissionPreset, body.preset_id)
        if preset is None or preset.org_id != caller.org_id or preset.deleted_at is not None:
            raise HTTPException(404, "Preset not found")
        target_matrix, key, label, builtin = preset.permissions, preset.id, preset.label, False

    role = get_role(db, user.org_id, user.role_id, include_deleted=True)
    role_matrix = role.permissions if role else {}
    special = perms.GRANT_ONLY_RESOURCES | perms.OVERRIDE_ONLY_RESOURCES
    existing = get_overrides(db, user.id)
    # Specially controlled cells (for example comms safe mode) are never touched by a preset.
    overrides: perms.Matrix = {
        r: dict(cells) for r, cells in ((existing.overrides if existing else {}) or {}).items() if r in special
    }
    for resource, cells in target_matrix.items():
        if resource not in perms.RESOURCES or resource in special:
            continue
        for action, value in cells.items():
            if not isinstance(value, bool) or not perms.supported(resource, action):
                continue
            if value != (role_matrix.get(resource, {}).get(action) is True):
                overrides.setdefault(resource, {})[action] = value
    _write_overrides(db, request, caller, user, overrides, {}, key, "user.preset_applied")
    payload = _permissions_payload(db, user)
    return {
        "user_id": user.id,
        "applied_preset": {"key": key, "label": label, "is_builtin": builtin},
        "overrides": payload["overrides"],
        "effective": payload["effective"],
    }
