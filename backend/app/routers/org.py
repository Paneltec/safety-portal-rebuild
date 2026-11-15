"""Organisation settings, branding, companies, first-run setup and service metadata."""
import hmac
import re
import time
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import permissions as perms
from ..config import get_settings
from ..db import get_db
from ..deps import Caller, get_caller, require_admin
from ..files import file_response, save_upload
from ..models import Company, Organisation, StoredFile, User, utcnow
from ..security import hash_password, password_policy_error
from ..services import audit, create_organisation, create_session, iso, user_out

router = APIRouter(tags=["organisation"])

DETAIL_FIELDS = [
    "trading_name", "abn", "address_line1", "address_line2", "suburb", "state", "postcode", "country",
    "contact_name", "contact_email", "contact_phone", "emergency_contact_phone",
    "after_hours_contact_name", "after_hours_contact_phone", "website_url",
]
LOGO_TYPES = {"image/png", "image/jpeg", "image/webp"}
LOGO_MAX_BYTES = 2 * 1024 * 1024


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = None
    staff_login_url: str | None = None
    public_portal_url: str | None = None
    portal_url: str | None = None  # baseline name for public_portal_url
    trading_name: str | None = Field(default=None, max_length=200)
    abn: str | None = Field(default=None, max_length=20)
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    suburb: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=50)
    postcode: str | None = Field(default=None, max_length=12)
    country: str | None = Field(default=None, max_length=80)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=40)
    emergency_contact_phone: str | None = Field(default=None, max_length=40)
    after_hours_contact_name: str | None = Field(default=None, max_length=200)
    after_hours_contact_phone: str | None = Field(default=None, max_length=40)
    website_url: str | None = None
    website: str | None = None  # baseline alias for website_url


class CompanyIn(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=40)


class CompaniesPatch(BaseModel):
    companies: list[CompanyIn] = Field(max_length=50)


class SetupIn(BaseModel):
    setup_key: str
    org_name: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str
    confirm_password: str


class TransferIn(BaseModel):
    user_id: str


def clean_url(value: str | None, field: str) -> str | None:
    """Accept only plain http(s) addresses with no embedded credentials."""
    if value is None or not value.strip():
        return None
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise HTTPException(422, f"{field} must be a web address starting with http:// or https://")
    return value[:500]


def org_out(db: Session, org: Organisation) -> dict[str, Any]:
    details = org.details or {}
    holder = db.get(User, org.owner_user_id) if org.owner_user_id else None
    return {
        "id": org.id,
        "name": org.name,
        "timezone": org.timezone,
        "staff_login_url": org.staff_login_url,
        "public_portal_url": org.public_portal_url,
        "logo_file_id": org.logo_file_id,
        "logo_url": f"/api/org/logo/{org.logo_file_id}" if org.logo_file_id else None,
        "owner_user_id": org.owner_user_id,
        "account_holder": {"id": holder.id, "name": holder.name} if holder else None,
        "payroll_owner_user_id": org.payroll_owner_user_id,
        **{key: details.get(key) for key in DETAIL_FIELDS},
        "updated_at": iso(org.updated_at),
    }


# ---------- service metadata ----------

@router.get("/api/")
def root() -> dict[str, str]:
    return {"service": "Safety Portal API", "version": get_settings().app_version}


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


@router.get("/api/public/branding")
def public_branding(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Name and logo for the sign-in page. Only shown when the install has a single organisation,
    so one tenant's identity is never shown to another's staff."""
    orgs = db.scalars(select(Organisation).limit(2)).all()
    if len(orgs) == 1:
        org = orgs[0]
        return {"name": org.name, "logo_url": f"/api/org/logo/{org.logo_file_id}" if org.logo_file_id else None}
    return {"name": "Safety Portal", "logo_url": None}


# ---------- first-run setup ----------

_setup_failures: list[float] = []


def _needs_setup(db: Session) -> bool:
    return (db.scalar(select(func.count()).select_from(User)) or 0) == 0


@router.get("/api/setup/status")
def setup_status(db: Session = Depends(get_db)) -> dict[str, bool]:
    return {"needs_setup": _needs_setup(db), "setup_key_configured": bool(get_settings().setup_key)}


@router.post("/api/setup", status_code=201)
def run_setup(body: SetupIn, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Create the organisation and its account holder. Works once, on an empty install,
    and only for someone who knows the SETUP_KEY from the server's configuration."""
    settings = get_settings()
    if not settings.setup_key:
        raise HTTPException(503, "Setup is not enabled. Set SETUP_KEY in the server's .env file and restart.")
    now = time.monotonic()
    _setup_failures[:] = [t for t in _setup_failures if now - t < 60]
    if len(_setup_failures) >= 5:
        raise HTTPException(429, "Too many wrong setup keys. Wait a minute and try again.")
    if not hmac.compare_digest(body.setup_key.encode(), settings.setup_key.encode()):
        _setup_failures.append(now)
        raise HTTPException(403, "The setup key is not correct")
    if body.password != body.confirm_password:
        raise HTTPException(400, "The two passwords do not match")
    error = password_policy_error(body.password)
    if error:
        raise HTTPException(400, error)

    # Serialise concurrent attempts so only one account holder can ever be created this way.
    db.execute(text("LOCK TABLE users IN EXCLUSIVE MODE"))
    if not _needs_setup(db):
        raise HTTPException(409, "This portal is already set up. Sign in instead.")
    org = create_organisation(db, body.org_name.strip())
    user = User(org_id=org.id, email=body.email.strip().lower(), name=body.name.strip(), role_id="admin",
                status="active", password_hash=hash_password(body.password), last_password_change_at=utcnow(),
                last_login_at=utcnow())
    db.add(user)
    db.flush()
    org.owner_user_id = user.id
    org.payroll_owner_user_id = user.id
    session, token = create_session(db, user, request, remember_me=False)
    audit(db, request, org_id=org.id, actor_id=user.id, action="org.setup_completed",
          target_type="organisation", target_id=org.id, detail={"account_holder": user.id})
    db.commit()
    return {"access_token": token, "token_type": "bearer", "expires_at": iso(session.expires_at), "user": user_out(db, user)}


# ---------- organisation settings ----------

def _org(db: Session, caller: Caller) -> Organisation:
    org = db.get(Organisation, caller.org_id)
    if org is None:
        raise HTTPException(404, "Org not found")
    return org


@router.get("/api/org")
def get_org(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    return org_out(db, _org(db, caller))


@router.patch("/api/org")
def patch_org(
    body: OrgPatch, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if "portal_url" in patch:
        patch.setdefault("public_portal_url", patch.pop("portal_url"))
    if "website" in patch:
        patch.setdefault("website_url", patch.pop("website"))
    if not patch:
        raise HTTPException(400, "No fields to update")
    org = _org(db, caller)
    changes: dict[str, Any] = {}

    def record(field: str, old: Any, new: Any) -> bool:
        if old != new:
            changes[field] = {"from": old, "to": new}
            return True
        return False

    if "name" in patch and patch["name"] is not None:
        name = patch["name"].strip()
        if not name:
            raise HTTPException(422, "Organisation name cannot be empty")
        if record("name", org.name, name):
            org.name = name
    if "timezone" in patch and patch["timezone"] is not None:
        if patch["timezone"] not in available_timezones():
            raise HTTPException(422, "Unknown time zone")
        ZoneInfo(patch["timezone"])
        if record("timezone", org.timezone, patch["timezone"]):
            org.timezone = patch["timezone"]
    for field in ("staff_login_url", "public_portal_url"):
        if field in patch:
            value = clean_url(patch[field], field)
            if record(field, getattr(org, field), value):
                setattr(org, field, value)
    details = dict(org.details or {})
    for field in DETAIL_FIELDS:
        if field in patch:
            value = patch[field]
            if field == "website_url":
                value = clean_url(value, field)
            elif isinstance(value, str):
                value = value.strip() or None
            if field == "abn" and value and not re.fullmatch(r"[0-9 ]{9,14}", value):
                raise HTTPException(422, "ABN must be 11 digits")
            if record(field, details.get(field), value):
                details[field] = value
    org.details = details
    if changes:
        audit(db, request, org_id=org.id, actor_id=caller.user.id, action="org.updated",
              target_type="organisation", target_id=org.id, detail=changes)
        db.commit()
    return org_out(db, org)


@router.post("/api/org/account-holder")
def transfer_account_holder(
    body: TransferIn, request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Hand the account to another active admin. Only the current holder can do this.
    Payroll ownership is a separate decision and does not move."""
    org = _org(db, caller)
    if org.owner_user_id != caller.user.id:
        raise HTTPException(403, "Only the account holder can transfer the account")
    target = db.get(User, body.user_id)
    if target is None or target.org_id != org.id or target.deleted_at is not None:
        raise HTTPException(404, "User not found")
    if target.role_id != "admin" or target.status != "active":
        raise HTTPException(400, "The new account holder must be an active admin")
    org.owner_user_id = target.id
    audit(db, request, org_id=org.id, actor_id=caller.user.id, action="org.account_holder_transferred",
          target_type="organisation", target_id=org.id, detail={"from": caller.user.id, "to": target.id})
    db.commit()
    return org_out(db, org)


# ---------- logo ----------

@router.post("/api/org/logo/upload")
def upload_logo(
    request: Request,
    file: UploadFile = File(...),
    caller: Caller = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    org = _org(db, caller)
    stored = save_upload(db, org_id=org.id, upload=file, purpose="logo", created_by=caller.user.id,
                         max_bytes=LOGO_MAX_BYTES, allowed_mimes=LOGO_TYPES)
    previous = org.logo_file_id
    org.logo_file_id = stored.id
    audit(db, request, org_id=org.id, actor_id=caller.user.id, action="org.logo_replaced",
          target_type="organisation", target_id=org.id, detail={"from": previous, "to": stored.id, "sha256": stored.sha256})
    db.commit()
    return {"logo_file_id": stored.id, "logo_gridfs_id": stored.id, "logo_url": f"/api/org/logo/{stored.id}",
            "logo_filename": stored.original_name}


@router.delete("/api/org/logo")
def remove_logo(request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, bool]:
    org = _org(db, caller)
    if org.logo_file_id:
        audit(db, request, org_id=org.id, actor_id=caller.user.id, action="org.logo_removed",
              target_type="organisation", target_id=org.id, detail={"from": org.logo_file_id})
        org.logo_file_id = None
        db.commit()
    return {"ok": True}


@router.get("/api/org/logo/{file_id}")
def get_logo(file_id: str, db: Session = Depends(get_db)):
    """Logos are branding, shown on the sign-in page and in emails, so this route needs no session.
    It only ever serves files stored as a logo (decision D-016)."""
    stored = db.get(StoredFile, file_id)
    if stored is None or stored.purpose != "logo" or stored.deleted_at is not None:
        raise HTTPException(404, "Logo not found")
    return file_response(stored, inline=True, cache=True)


# ---------- companies ----------

def _companies(db: Session, org_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(select(Company).where(Company.org_id == org_id).order_by(func.lower(Company.name), Company.id)).all()
    return [{"id": c.id, "name": c.name, "code": c.code} for c in rows]


@router.get("/api/org/companies")
def list_companies(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"companies": _companies(db, caller.org_id)}


@router.put("/api/org/companies")
def replace_companies(
    body: CompaniesPatch, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Replace the list of divisions. Existing ids are kept so links to them survive a rename.
    A division that people are still assigned to cannot be removed."""
    existing = {c.id: c for c in db.scalars(select(Company).where(Company.org_id == caller.org_id))}
    seen_codes: set[str] = set()
    keep: set[str] = set()
    for item in body.companies:
        name = item.name.strip()
        code = perms.slugify(item.code or name).upper()[:40]
        if not name or not code:
            raise HTTPException(422, "Every company needs an id and name")
        if code in seen_codes:
            raise HTTPException(409, f"Duplicate company id: {code}")
        seen_codes.add(code)
        if item.id:
            company = existing.get(item.id)
            if company is None:
                raise HTTPException(404, "Company not found")
            company.name, company.code = name, code
            keep.add(company.id)
        else:
            company = Company(org_id=caller.org_id, name=name, code=code)
            db.add(company)
            db.flush()
            keep.add(company.id)
    for company_id, company in existing.items():
        if company_id in keep:
            continue
        in_use = db.scalar(select(func.count()).select_from(User).where(User.company_id == company_id)) or 0
        if in_use:
            raise HTTPException(409, f"{company.name} still has {in_use} people assigned. Move them first.")
        db.delete(company)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Two companies cannot share the same code") from exc
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="org.companies_replaced",
          target_type="organisation", target_id=caller.org_id, detail={"codes": sorted(seen_codes)})
    db.commit()
    return {"companies": _companies(db, caller.org_id)}


# ---------- permission catalogue ----------

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
