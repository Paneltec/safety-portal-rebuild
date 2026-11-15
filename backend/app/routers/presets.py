"""Named permission presets. Built-in presets mirror the role catalogue and are read-only."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import permissions as perms
from ..db import get_db
from ..deps import Caller, ensure_can_grant, require_permission
from ..models import PermissionPreset, Role, User, UserPermissionOverride, utcnow
from ..services import audit, iso

router = APIRouter(prefix="/api/permission-presets", tags=["permission presets"])


class PresetIn(BaseModel):
    label: str
    description: str | None = ""
    icon: str | None = Field(default=None, max_length=60)
    permissions: dict[str, Any] = Field(default_factory=dict)


def _out(preset: PermissionPreset) -> dict[str, Any]:
    return {
        "id": preset.id,
        "key": preset.id,
        "label": preset.label,
        "description": preset.description,
        "icon": preset.icon,
        "permissions": preset.permissions,
        "is_builtin": False,
        "created_at": iso(preset.created_at),
        "updated_at": iso(preset.updated_at),
    }


def _clean(caller: Caller, body: PresetIn) -> tuple[str, perms.Matrix]:
    label = body.label.strip()
    if not label:
        raise HTTPException(400, "Label is required")
    try:
        # Presets can never carry the specially controlled resources.
        matrix = perms.clean_overrides(body.permissions, allow_special=False)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    ensure_can_grant(caller, matrix)
    return label[:120], matrix


def _load(db: Session, caller: Caller, preset_id: str) -> PermissionPreset:
    preset = db.get(PermissionPreset, preset_id)
    if preset is None or preset.org_id != caller.org_id or preset.deleted_at is not None:
        if _is_builtin(db, caller, preset_id):
            raise HTTPException(400, "Built-in presets are read-only")
        raise HTTPException(404, "Preset not found")
    return preset


def _is_builtin(db: Session, caller: Caller, key: str) -> bool:
    return db.scalars(
        select(Role.id).where(Role.org_id == caller.org_id, Role.role_id == key, Role.deleted_at.is_(None))
    ).first() is not None


@router.get("")
def list_presets(
    caller: Caller = Depends(require_permission("users", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    roles = db.scalars(
        select(Role).where(Role.org_id == caller.org_id, Role.deleted_at.is_(None), Role.is_active.is_(True))
        .order_by(Role.is_system.desc(), func.lower(Role.name))
    ).all()
    custom = db.scalars(
        select(PermissionPreset)
        .where(PermissionPreset.org_id == caller.org_id, PermissionPreset.deleted_at.is_(None))
        .order_by(func.lower(PermissionPreset.label), PermissionPreset.id)
    ).all()
    return {
        "built_in": [
            {"key": r.role_id, "label": r.name, "description": r.description, "permissions": r.permissions,
             "is_builtin": True, "is_system": r.is_system}
            for r in roles
        ],
        "custom": [_out(p) for p in custom],
    }


@router.post("", status_code=201)
def create_preset(
    body: PresetIn,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    label, matrix = _clean(caller, body)
    preset = PermissionPreset(org_id=caller.org_id, label=label, description=body.description or "",
                              icon=body.icon, permissions=matrix, created_by=caller.user.id)
    db.add(preset)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="preset.created",
          target_type="permission_preset", target_id=preset.id, detail={"label": label})
    db.commit()
    return _out(preset)


@router.put("/{preset_id}")
def update_preset(
    preset_id: str,
    body: PresetIn,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Editing a preset does not change users it was applied to earlier."""
    preset = _load(db, caller, preset_id)
    label, matrix = _clean(caller, body)
    preset.label = label
    preset.description = body.description or ""
    preset.icon = body.icon
    preset.permissions = matrix
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="preset.updated",
          target_type="permission_preset", target_id=preset.id, detail={"label": label})
    db.commit()
    return _out(preset)


@router.delete("/{preset_id}")
def delete_preset(
    preset_id: str,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    if _is_builtin(db, caller, preset_id):
        raise HTTPException(400, "Built-in presets cannot be deleted")
    preset = _load(db, caller, preset_id)
    preset.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="preset.deleted",
          target_type="permission_preset", target_id=preset.id, detail={"label": preset.label})
    db.commit()
    return {"ok": True}


@router.post("/{preset_id}/duplicate", status_code=201)
def duplicate_preset(
    preset_id: str,
    request: Request,
    caller: Caller = Depends(require_permission("users", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Copy a custom preset, or a built-in role's matrix, into a new editable preset."""
    source = db.get(PermissionPreset, preset_id)
    if source is not None and source.org_id == caller.org_id and source.deleted_at is None:
        label, matrix, description = source.label, source.permissions, source.description
    else:
        role = db.scalars(
            select(Role).where(Role.org_id == caller.org_id, Role.role_id == preset_id, Role.deleted_at.is_(None))
        ).first()
        if role is None:
            raise HTTPException(404, "Preset not found")
        special = perms.GRANT_ONLY_RESOURCES | perms.OVERRIDE_ONLY_RESOURCES
        label, description = role.name, role.description
        matrix = {r: dict(cells) for r, cells in role.permissions.items() if r not in special}
    ensure_can_grant(caller, matrix)
    copy = PermissionPreset(org_id=caller.org_id, label=f"{label} (copy)"[:120], description=description,
                            permissions=matrix, created_by=caller.user.id)
    db.add(copy)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="preset.duplicated",
          target_type="permission_preset", target_id=copy.id, detail={"from": preset_id})
    db.commit()
    return _out(copy)


@router.get("/{preset_id}/assignees")
def preset_assignees(
    preset_id: str, caller: Caller = Depends(require_permission("users", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Users whose current overrides came from applying this preset."""
    rows = db.execute(
        select(User.id, User.name, User.email)
        .join(UserPermissionOverride, UserPermissionOverride.user_id == User.id)
        .where(User.org_id == caller.org_id, User.deleted_at.is_(None),
               UserPermissionOverride.applied_preset_key == preset_id)
        .order_by(func.lower(User.name), User.id)
    ).all()
    return {"count": len(rows), "users": [{"id": i, "name": n, "email": e} for i, n, e in rows]}
