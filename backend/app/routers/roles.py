"""Role catalogue: built-in roles (read-only) and custom roles."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import permissions as perms
from ..db import get_db
from ..deps import Caller, ensure_can_grant, require_permission
from ..models import AuditLog, Role, User, utcnow
from ..services import audit, get_role, iso

router = APIRouter(prefix="/api/admin/roles", tags=["roles"])


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str | None = ""
    permission_tokens: list[str] = Field(default_factory=list)
    legacy_role: str = "worker"


class RolePatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = None
    permission_tokens: list[str] | None = None
    is_active: bool | None = None
    legacy_role: str | None = None


PHONE_ROLE_KEYS = {"worker", "supervisor", "contractor", "admin"}


def _user_counts(db: Session, org_id: str) -> dict[str, int]:
    rows = db.execute(
        select(User.role_id, func.count()).where(User.org_id == org_id, User.deleted_at.is_(None)).group_by(User.role_id)
    ).all()
    return {role_id: int(count) for role_id, count in rows}


def _role_out(role: Role, user_count: int = 0) -> dict[str, Any]:
    return {
        "role_id": role.role_id,
        "name": role.name,
        "description": role.description,
        "is_system": role.is_system,
        "is_active": role.is_active,
        "legacy_role": role.legacy_role,
        "permissions": role.permissions,
        "permission_tokens": perms.matrix_to_tokens(role.permissions),
        "user_count": user_count,
        "created_at": iso(role.created_at),
        "updated_at": iso(role.updated_at),
    }


def _matrix(tokens: list[str]) -> perms.Matrix:
    try:
        return perms.tokens_to_matrix(tokens)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _load(db: Session, caller: Caller, role_id: str) -> Role:
    role = get_role(db, caller.org_id, role_id)
    if role is None:
        raise HTTPException(404, "Role not found")
    return role


@router.get("")
def list_roles(
    caller: Caller = Depends(require_permission("users", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    roles = db.scalars(
        select(Role).where(Role.org_id == caller.org_id, Role.deleted_at.is_(None))
        .order_by(Role.is_system.desc(), func.lower(Role.name), Role.role_id)
    ).all()
    counts = _user_counts(db, caller.org_id)
    return {"count": len(roles), "roles": [_role_out(r, counts.get(r.role_id, 0)) for r in roles]}


@router.post("", status_code=201)
def create_role(
    body: RoleCreate,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    role_id = perms.slugify(body.name)
    if not role_id:
        raise HTTPException(422, "Role name must contain letters or numbers")
    if get_role(db, caller.org_id, role_id, include_deleted=True) is not None:
        raise HTTPException(409, f"role_id '{role_id}' already exists")
    if body.legacy_role not in PHONE_ROLE_KEYS - {"admin"}:
        raise HTTPException(422, "legacy_role must be worker, supervisor or contractor")
    matrix = _matrix(body.permission_tokens)
    ensure_can_grant(caller, matrix)
    role = Role(org_id=caller.org_id, role_id=role_id, name=body.name.strip(), description=body.description or "",
                is_system=False, legacy_role=body.legacy_role, permissions=matrix)
    db.add(role)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="role.created",
          target_type="role", target_id=role_id, detail={"name": role.name, "tokens": perms.matrix_to_tokens(matrix)})
    db.commit()
    return _role_out(role)


@router.get("/{role_id}")
def get_role_detail(
    role_id: str, caller: Caller = Depends(require_permission("users", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    role = _load(db, caller, role_id)
    return _role_out(role, _user_counts(db, caller.org_id).get(role.role_id, 0))


@router.patch("/{role_id}")
def patch_role(
    role_id: str,
    body: RolePatch,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Change a custom role. The change applies to everyone holding the role straight away;
    their individual overrides are left untouched."""
    role = _load(db, caller, role_id)
    if role.is_system:
        raise HTTPException(400, "cannot modify system role")
    patch = body.model_dump(exclude_unset=True)
    changes: dict[str, Any] = {}
    if patch.get("name") is not None and patch["name"].strip() != role.name:
        changes["name"] = {"from": role.name, "to": patch["name"].strip()}
        role.name = patch["name"].strip()
    if patch.get("description") is not None and patch["description"] != role.description:
        changes["description"] = {"from": role.description, "to": patch["description"]}
        role.description = patch["description"]
    if patch.get("legacy_role") is not None and patch["legacy_role"] != role.legacy_role:
        if patch["legacy_role"] not in PHONE_ROLE_KEYS - {"admin"}:
            raise HTTPException(422, "legacy_role must be worker, supervisor or contractor")
        changes["legacy_role"] = {"from": role.legacy_role, "to": patch["legacy_role"]}
        role.legacy_role = patch["legacy_role"]
    if patch.get("permission_tokens") is not None:
        matrix = _matrix(patch["permission_tokens"])
        before = perms.matrix_to_tokens(role.permissions)
        after = perms.matrix_to_tokens(matrix)
        if before != after:
            added = sorted(set(after) - set(before))
            ensure_can_grant(caller, perms.tokens_to_matrix(added))
            changes["tokens"] = {"added": added, "removed": sorted(set(before) - set(after))}
            role.permissions = matrix
    if patch.get("is_active") is not None and patch["is_active"] != role.is_active:
        changes["is_active"] = {"from": role.is_active, "to": patch["is_active"]}
        role.is_active = patch["is_active"]
    if changes:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="role.updated",
              target_type="role", target_id=role.role_id, detail=changes)
        db.commit()
    return _role_out(role, _user_counts(db, caller.org_id).get(role.role_id, 0))


@router.delete("/{role_id}")
def delete_role(
    role_id: str,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    role = _load(db, caller, role_id)
    if role.is_system:
        raise HTTPException(400, "cannot delete system role")
    assigned = _user_counts(db, caller.org_id).get(role.role_id, 0)
    if assigned:
        raise HTTPException(409, f"role is assigned to {assigned} users")
    role.deleted_at = utcnow()
    role.is_active = False
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="role.deleted",
          target_type="role", target_id=role.role_id)
    db.commit()
    return {"deleted": True, "role_id": role.role_id, "soft": True}


@router.get("/{role_id}/assignees-count")
def assignees_count(
    role_id: str, caller: Caller = Depends(require_permission("users", "edit")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    role = _load(db, caller, role_id)
    return {"role_id": role.role_id, "count": _user_counts(db, caller.org_id).get(role.role_id, 0)}


@router.get("/{role_id}/audit")
def role_audit(
    role_id: str,
    limit: int = Query(50, ge=1, le=200),
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    role = get_role(db, caller.org_id, role_id, include_deleted=True)
    if role is None:
        raise HTTPException(404, "Role not found")
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.org_id == caller.org_id, AuditLog.target_type == "role", AuditLog.target_id == role.role_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id)
        .limit(limit)
    ).all()
    entries = [
        {"id": r.id, "action": r.action, "actor_user_id": r.actor_user_id, "detail": r.detail, "at": iso(r.created_at)}
        for r in rows
    ]
    return {"count": len(entries), "entries": entries}
