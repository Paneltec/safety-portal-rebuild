"""Organisation basics and service metadata. Branding and URL settings grow in a later stage."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import permissions as perms
from ..config import get_settings
from ..db import get_db
from ..deps import Caller, get_caller
from ..models import Company, Organisation

router = APIRouter(tags=["organisation"])


@router.get("/api/")
def root() -> dict[str, str]:
    settings = get_settings()
    return {"service": "Safety Portal API", "version": settings.app_version}


@router.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Readiness: reports the database separately so one failure does not hide the rest."""
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001 - reported, never raised
        database = "unavailable"
    return {"status": "ok" if database == "ok" else "degraded", "database": database, "version": settings.app_version}


@router.get("/api/org")
def get_org(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    org = db.get(Organisation, caller.org_id)
    if org is None:
        raise HTTPException(404, "Organisation not found")
    return {
        "id": org.id,
        "name": org.name,
        "timezone": org.timezone,
        "staff_login_url": org.staff_login_url,
        "public_portal_url": org.public_portal_url,
    }


@router.get("/api/org/companies")
def list_companies(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Company).where(Company.org_id == caller.org_id).order_by(func.lower(Company.name), Company.id)
    ).all()
    return [{"id": c.id, "name": c.name, "code": c.code} for c in rows]


@router.get("/api/permissions/schema")
def permissions_schema(caller: Caller = Depends(get_caller)) -> dict[str, Any]:
    """Catalogue used to draw the permission matrix."""
    return {
        "actions": perms.ACTIONS,
        "resources": {
            key: {
                **meta,
                "supported_actions": [a for a in perms.ACTIONS if perms.supported(key, a)],
                "managed_by": (
                    "payroll_grants" if key in perms.GRANT_ONLY_RESOURCES
                    else "admin_override" if key in perms.OVERRIDE_ONLY_RESOURCES
                    else "roles"
                ),
            }
            for key, meta in perms.RESOURCES.items()
        },
        "team_scoped_resources": perms.TEAM_SCOPED_RESOURCES,
    }
