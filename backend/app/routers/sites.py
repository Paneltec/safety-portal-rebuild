"""Sites, QR sign-on and visitors (stage 4b).

Three kinds of caller use these routes, and they are deliberately not interchangeable:

  office     signed in, with `sites` / `sites_visitors` permissions: manages sites and the registers
  worker     signed in, any role: signs themself on and off at a site they have the QR code for
  visitor    not signed in: can only read the public site header and sign in/out at that one site

A QR token never reveals the organisation id, the roster, other visitors or any other site.
"""
import csv
import hashlib
import io
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

import segno
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import people
from ..config import get_settings
from ..db import get_db
from ..deps import Caller, get_caller, require_permission
from ..files import read_bytes
from ..models import Company, Organisation, Site, SiteAttendance, StoredFile, User, new_id, utcnow
from ..pdf import site_sign_pdf
from ..services import audit, iso, portal_base_url

router = APIRouter(prefix="/api/sites", tags=["sites"])
scan_router = APIRouter(prefix="/api/scan", tags=["site sign-on"])
public_router = APIRouter(prefix="/api/public", tags=["site sign-on (public)"])
visitors_router = APIRouter(prefix="/api/admin/visitors", tags=["visitors"])

DEFAULT_DECLARATION = (
    "I will follow the site rules and the directions of site staff, wear the required protective equipment, "
    "stay with my host where asked, and report any hazard, incident or injury before I leave."
)
VISITOR_LIMIT_PER_HOUR = 10
STALE_AFTER_HOURS = 16
MAX_ROWS = 500


# ---------- request bodies ----------

class QuestionIn(BaseModel):
    id: str | None = Field(None, max_length=40)
    type: Literal["yesno", "text", "choice"]
    label: str = Field(min_length=2, max_length=200)
    required: bool = True
    choices: list[str] | None = Field(None, max_length=12)


class SiteIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    address: str | None = Field(None, max_length=300)
    suburb: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=40)
    postcode: str | None = Field(None, max_length=12)
    description: str | None = Field(None, max_length=4000)
    company_id: str | None = None
    kind: Literal["project", "depot", "other"] = "project"
    active: bool = True
    gps_lat: float | None = Field(None, ge=-90, le=90)
    gps_long: float | None = Field(None, ge=-180, le=180)
    signon_questions: list[QuestionIn] = Field(default_factory=list, max_length=20)
    visitor_declaration: str | None = Field(None, max_length=2000)


class SitePatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    address: str | None = Field(None, max_length=300)
    suburb: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=40)
    postcode: str | None = Field(None, max_length=12)
    description: str | None = Field(None, max_length=4000)
    company_id: str | None = None
    kind: Literal["project", "depot", "other"] | None = None
    active: bool | None = None
    gps_lat: float | None = Field(None, ge=-90, le=90)
    gps_long: float | None = Field(None, ge=-180, le=180)
    signon_questions: list[QuestionIn] | None = Field(None, max_length=20)
    visitor_declaration: str | None = Field(None, max_length=2000)


class AnswerIn(BaseModel):
    question_id: str
    value: Any = None


class Gps(BaseModel):
    gps_lat: float | None = Field(None, ge=-90, le=90)
    gps_long: float | None = Field(None, ge=-180, le=180)
    gps_accuracy_m: float | None = Field(None, ge=0, le=100000)


class WorkerSignOn(Gps):
    answers: list[AnswerIn] = Field(default_factory=list, max_length=20)


class VisitorSignIn(Gps):
    name: str = Field(min_length=2, max_length=200)
    company: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=40)
    purpose: str | None = Field(None, max_length=300)
    host: str | None = Field(None, max_length=200)
    induction_acknowledged: bool = False
    answers: list[AnswerIn] = Field(default_factory=list, max_length=20)
    # Sent by the page so a double tap or a retry on a weak signal cannot create two visits.
    client_ref: str | None = Field(None, min_length=8, max_length=64)


class VisitorCorrection(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    company: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=40)
    purpose: str | None = Field(None, max_length=300)
    host: str | None = Field(None, max_length=200)
    signed_out_at: datetime | None = None
    reason: str = Field(min_length=3, max_length=300)


class BulkIds(BaseModel):
    ids: list[str] = Field(max_length=500)


# ---------- helpers ----------

def _org(db: Session, org_id: str) -> Organisation:
    return db.get(Organisation, org_id)


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _questions(items: list[QuestionIn]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        qid = (item.id or "").strip() or f"q_{secrets.token_hex(4)}"
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", qid):
            raise HTTPException(422, "A question id may only contain letters, numbers, hyphens and underscores")
        if qid in seen:
            raise HTTPException(422, "Two sign-on questions have the same id")
        seen.add(qid)
        question: dict[str, Any] = {"id": qid, "type": item.type, "label": item.label.strip(), "required": item.required}
        if item.type == "choice":
            choices = list(dict.fromkeys(c.strip() for c in (item.choices or []) if c and c.strip()))
            if len(choices) < 2:
                raise HTTPException(422, f"“{item.label.strip()}” needs at least two choices")
            question["choices"] = choices
        out.append(question)
    return out


def _answers(site: Site, supplied: list[AnswerIn]) -> list[dict[str, Any]]:
    """Check answers against the site's current questions and keep the wording that was asked."""
    given = {a.question_id: a.value for a in supplied}
    out: list[dict[str, Any]] = []
    problems: list[str] = []
    for q in site.signon_questions or []:
        value = given.get(q["id"])
        if isinstance(value, str):
            value = value.strip()
        if value in (None, ""):
            if q.get("required"):
                problems.append(f"{q['label']}: an answer is needed")
            continue
        if q["type"] == "yesno":
            if not isinstance(value, bool):
                problems.append(f"{q['label']}: answer yes or no")
                continue
        elif q["type"] == "choice":
            if value not in q.get("choices", []):
                problems.append(f"{q['label']}: choose one of the listed answers")
                continue
        else:
            if not isinstance(value, str) or len(value) > 500:
                problems.append(f"{q['label']}: write a short answer")
                continue
        out.append({"question_id": q["id"], "label": q["label"], "value": value})
    if problems:
        raise HTTPException(422, {"message": "Some sign-in questions need attention", "errors": [{"field_id": "", "message": p} for p in problems]})
    return out


def _gps(body: Gps) -> dict[str, Any] | None:
    if body.gps_lat is None or body.gps_long is None:
        return None
    return {"lat": body.gps_lat, "long": body.gps_long, "accuracy_m": body.gps_accuracy_m}


def _address(site: Site) -> str:
    return ", ".join(x for x in (site.address, site.suburb, site.state, site.postcode) if x)


def _get_site(db: Session, caller: Caller, site_id: str, *, include_deleted: bool = False) -> Site:
    site = db.get(Site, site_id)
    if site is None or site.org_id != caller.org_id or (site.deleted_at is not None and not include_deleted):
        raise HTTPException(404, "Site not found")
    return site


def _site_by_token(db: Session, scan_token: str) -> Site:
    """Resolve a QR token. Unknown, rotated, inactive and deleted all look the same to the caller."""
    site = db.scalars(select(Site).where(Site.scan_token == scan_token)).first() if scan_token else None
    if site is None or site.deleted_at is not None or not site.active:
        raise HTTPException(404, "This sign-in code is not in use. Ask site staff for the current one.")
    return site


def _open_count(db: Session, site_ids: list[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {sid: {"workers": 0, "visitors": 0} for sid in site_ids}
    if not site_ids:
        return out
    rows = db.execute(
        select(SiteAttendance.site_id, SiteAttendance.kind, func.count())
        .where(SiteAttendance.site_id.in_(site_ids), SiteAttendance.signed_out_at.is_(None), SiteAttendance.deleted_at.is_(None))
        .group_by(SiteAttendance.site_id, SiteAttendance.kind)
    ).all()
    for sid, kind, n in rows:
        out[sid]["workers" if kind == "worker" else "visitors"] = int(n)
    return out


def site_out(db: Session, request: Request, caller: Caller, site: Site, *, detail: bool = False, on_site: dict[str, int] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": site.id, "name": site.name, "address": site.address, "suburb": site.suburb, "state": site.state,
        "postcode": site.postcode, "address_full": _address(site), "kind": site.kind, "active": site.active,
        "company_id": site.company_id,
        "source": ({"system": site.source_system, "company": site.source_company, "id": site.source_id} if site.source_system else None),
        "on_site": on_site if on_site is not None else _open_count(db, [site.id])[site.id],
        "updated_at": iso(site.updated_at), "deleted_at": iso(site.deleted_at),
    }
    if detail:
        out.update({
            "description": site.description, "gps_lat": site.gps_lat, "gps_long": site.gps_long,
            "signon_questions": list(site.signon_questions or []),
            "visitor_declaration": site.visitor_declaration, "default_visitor_declaration": DEFAULT_DECLARATION,
        })
        # The QR address is the key to signing in at this site, so only people who manage sites see it.
        if caller.can("sites", "edit"):
            out["sign_on_url"] = f"{portal_base_url(request)}/sign-on/{site.scan_token}"
            # Without PUBLIC_BASE_URL the address is taken from this request, which may not be the
            # address a phone at the gate can reach. The screen warns before anyone prints a sign.
            out["sign_on_address_configured"] = bool(get_settings().public_base_url.strip())
    return out


def attendance_out(row: SiteAttendance, site: Site | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    open_ = row.signed_out_at is None
    end = row.signed_out_at or now
    return {
        "id": row.id, "kind": row.kind, "site_id": row.site_id, "site_name": site.name if site else None,
        "name": row.name, "company": row.company, "phone": row.phone, "purpose": row.purpose, "host": row.host,
        "worker_id": row.worker_id, "user_id": row.user_id,
        "declaration_acknowledged": row.declaration_acknowledged, "answers": list(row.answers or []), "gps": row.gps,
        "source": row.source, "signed_in_at": iso(row.signed_in_at), "signed_out_at": iso(row.signed_out_at),
        "sign_out_source": row.sign_out_source, "on_site": open_,
        "minutes": max(0, int((end - row.signed_in_at).total_seconds() // 60)),
        # Still "on site" long after a working day almost always means a missed sign-out.
        "stale": open_ and now - row.signed_in_at > timedelta(hours=STALE_AFTER_HOURS),
        "archived_at": iso(row.archived_at), "deleted_at": iso(row.deleted_at),
    }


def _day_range(org: Organisation, date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    """Whole local days in the organisation's timezone, as UTC instants."""
    try:
        zone = ZoneInfo(org.timezone or "Australia/Hobart")
    except Exception:
        zone = ZoneInfo("Australia/Hobart")
    start = datetime.combine(date_from, time.min, zone).astimezone(timezone.utc) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time.min, zone).astimezone(timezone.utc) if date_to else None
    return start, end


def _close(row: SiteAttendance, *, source: str, by: str | None) -> bool:
    """Sign a visit out. Returns False when it was already closed (the first time stands)."""
    if row.signed_out_at is not None:
        return False
    row.signed_out_at = max(utcnow(), row.signed_in_at)
    row.sign_out_source = source
    row.signed_out_by = by
    return True


def _csv(rows: list[SiteAttendance], sites: dict[str, Site], zone_name: str) -> Response:
    try:
        zone = ZoneInfo(zone_name)
    except Exception:
        zone = ZoneInfo("Australia/Hobart")

    def local(value: datetime | None) -> str:
        return value.astimezone(zone).strftime("%d/%m/%Y %H:%M") if value else ""

    def safe(value: Any) -> str:
        text = "" if value is None else str(value)
        # A cell starting with one of these would run as a formula when the file is opened in Excel.
        return "'" + text if text[:1] in ("=", "+", "-", "@") else text

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Site", "Type", "Name", "Company", "Phone", "Purpose", "Host", f"Signed in ({zone_name})",
                     f"Signed out ({zone_name})", "Signed out by", "Minutes on site", "Answers"])
    now = utcnow()
    for row in rows:
        out = attendance_out(row, sites.get(row.site_id), now=now)
        answers = "; ".join(f"{a['label']}: {'Yes' if a['value'] is True else 'No' if a['value'] is False else a['value']}" for a in out["answers"])
        writer.writerow([safe(out["site_name"]), "Worker" if row.kind == "worker" else "Visitor", safe(row.name), safe(row.company),
                         safe(row.phone), safe(row.purpose), safe(row.host), local(row.signed_in_at), local(row.signed_out_at),
                         {"self": "Themself", "office": "Office"}.get(row.sign_out_source or "", ""), out["minutes"] if row.signed_out_at else "", safe(answers)])
    return Response("﻿" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="site-attendance.csv"'})


# ---------- sites register ----------

@router.get("")
def list_sites(
    request: Request,
    q: str | None = Query(None, max_length=100),
    status: Literal["active", "inactive", "all"] = "active",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    caller: Caller = Depends(require_permission("sites", "view")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Site).where(Site.org_id == caller.org_id, Site.deleted_at.is_(None))
    base = stmt
    if status != "all":
        stmt = stmt.where(Site.active.is_(status == "active"))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Site.name).like(like), func.lower(func.coalesce(Site.address, "")).like(like),
                              func.lower(func.coalesce(Site.suburb, "")).like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(func.lower(Site.name), Site.id).limit(limit).offset(offset)).all()
    counts = _open_count(db, [s.id for s in rows])
    return {
        "items": [site_out(db, request, caller, s, on_site=counts[s.id]) for s in rows],
        "total": int(total), "limit": limit, "offset": offset,
        "counts": {
            "active": int(db.scalar(select(func.count()).select_from(base.where(Site.active.is_(True)).subquery())) or 0),
            "inactive": int(db.scalar(select(func.count()).select_from(base.where(Site.active.is_(False)).subquery())) or 0),
        },
    }


def _company(db: Session, caller: Caller, company_id: str | None) -> None:
    if company_id is not None:
        company = db.get(Company, company_id)
        if company is None or company.org_id != caller.org_id:
            raise HTTPException(404, "Company not found")


@router.post("", status_code=201)
def create_site(
    body: SiteIn, request: Request,
    caller: Caller = Depends(require_permission("sites", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    _company(db, caller, body.company_id)
    site = Site(
        org_id=caller.org_id, name=body.name.strip(), address=_text(body.address), suburb=_text(body.suburb),
        state=_text(body.state), postcode=_text(body.postcode), description=_text(body.description),
        company_id=body.company_id, kind=body.kind, active=body.active, gps_lat=body.gps_lat, gps_long=body.gps_long,
        signon_questions=_questions(body.signon_questions), visitor_declaration=_text(body.visitor_declaration),
        scan_token=secrets.token_urlsafe(24), created_by=caller.user.id,
    )
    db.add(site)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="site.created",
          target_type="site", target_id=site.id, detail={"name": site.name})
    db.commit()
    return site_out(db, request, caller, site, detail=True)


@router.get("/recycle-bin")
def recycle_bin(
    request: Request, caller: Caller = Depends(require_permission("sites", "delete")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    rows = db.scalars(select(Site).where(Site.org_id == caller.org_id, Site.deleted_at.is_not(None)).order_by(Site.deleted_at.desc())).all()
    return {"items": [site_out(db, request, caller, s) for s in rows]}


@router.get("/my-attendance")
def my_attendance(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Where the signed-in person is currently signed on."""
    rows = db.execute(
        select(SiteAttendance, Site).join(Site, Site.id == SiteAttendance.site_id)
        .where(SiteAttendance.org_id == caller.org_id, SiteAttendance.kind == "worker", SiteAttendance.user_id == caller.user.id,
               SiteAttendance.signed_out_at.is_(None), SiteAttendance.deleted_at.is_(None))
        .order_by(SiteAttendance.signed_in_at.desc())
    ).all()
    return {"items": [attendance_out(a, s) for a, s in rows]}


@router.get("/{site_id}")
def get_site(
    site_id: str, request: Request,
    caller: Caller = Depends(require_permission("sites", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return site_out(db, request, caller, _get_site(db, caller, site_id), detail=True)


@router.patch("/{site_id}")
def update_site(
    site_id: str, body: SitePatch, request: Request,
    caller: Caller = Depends(require_permission("sites", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    site = _get_site(db, caller, site_id)
    data = body.model_dump(exclude_unset=True)
    changed: list[str] = []
    for key, value in data.items():
        if key == "signon_questions":
            value = _questions(body.signon_questions or [])
        elif key == "company_id":
            _company(db, caller, value)
        elif key in ("name",):
            if value is None:
                continue
            value = value.strip()
        elif key in ("kind", "active"):
            if value is None:
                continue
        elif isinstance(value, str):
            value = _text(value)
        if getattr(site, key) != value:
            setattr(site, key, value)
            changed.append(key)
    if changed:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="site.updated",
              target_type="site", target_id=site.id, detail={"fields": changed})
    db.commit()
    return site_out(db, request, caller, site, detail=True)


@router.delete("/{site_id}")
def delete_site(
    site_id: str, request: Request,
    caller: Caller = Depends(require_permission("sites", "delete")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Soft delete. Anyone still signed on is signed out by the office so occupancy stays true."""
    site = _get_site(db, caller, site_id)
    site.deleted_at = utcnow()
    closed = 0
    for row in db.scalars(select(SiteAttendance).where(SiteAttendance.site_id == site.id, SiteAttendance.signed_out_at.is_(None),
                                                       SiteAttendance.deleted_at.is_(None))).all():
        closed += _close(row, source="office", by=caller.user.id)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="site.deleted",
          target_type="site", target_id=site.id, detail={"signed_out": closed})
    db.commit()
    return {"ok": True, "signed_out": closed}


@router.post("/{site_id}/restore")
def restore_site(
    site_id: str, request: Request,
    caller: Caller = Depends(require_permission("sites", "delete")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    site = _get_site(db, caller, site_id, include_deleted=True)
    if site.deleted_at is not None:
        site.deleted_at = None
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="site.restored", target_type="site", target_id=site.id)
        db.commit()
    return site_out(db, request, caller, site, detail=True)


@router.post("/{site_id}/rotate-token")
def rotate_token(
    site_id: str, request: Request,
    caller: Caller = Depends(require_permission("sites", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Issue a new QR code. Every sign printed before stops working, so reprint and replace it."""
    site = _get_site(db, caller, site_id)
    site.scan_token = secrets.token_urlsafe(24)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="site.qr_rotated", target_type="site", target_id=site.id)
    db.commit()
    return site_out(db, request, caller, site, detail=True)


@router.get("/{site_id}/qr.png")
def qr_png(
    site_id: str, request: Request,
    caller: Caller = Depends(require_permission("sites", "edit")), db: Session = Depends(get_db),
) -> Response:
    site = _get_site(db, caller, site_id)
    buffer = io.BytesIO()
    segno.make(f"{portal_base_url(request)}/sign-on/{site.scan_token}", error="m").save(buffer, kind="png", scale=8, border=3)
    return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{site_id}/qr-signage.pdf")
def qr_signage(
    site_id: str, request: Request,
    caller: Caller = Depends(require_permission("sites", "edit")), db: Session = Depends(get_db),
) -> Response:
    """A printable A4 gate sign."""
    site = _get_site(db, caller, site_id)
    org = _org(db, caller.org_id)
    logo = read_bytes(db.get(StoredFile, org.logo_file_id)) if org.logo_file_id else None
    data = site_sign_pdf(org_name=(org.details or {}).get("trading_name") or org.name, logo=logo, site_name=site.name,
                         address=_address(site), url=f"{portal_base_url(request)}/sign-on/{site.scan_token}")
    name = re.sub(r"[^A-Za-z0-9]+", "-", site.name).strip("-") or "site"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="sign-in-{name}.pdf"', "Cache-Control": "no-store"})


# ---------- who is on site, and the log ----------

@router.get("/{site_id}/active-signons")
def active_signons(
    site_id: str, caller: Caller = Depends(require_permission("sites", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    site = _get_site(db, caller, site_id)
    rows = db.scalars(
        select(SiteAttendance).where(SiteAttendance.site_id == site.id, SiteAttendance.signed_out_at.is_(None),
                                     SiteAttendance.deleted_at.is_(None)).order_by(SiteAttendance.signed_in_at)
    ).all()
    now = utcnow()
    return {"items": [attendance_out(r, site, now=now) for r in rows]}


@router.delete("/{site_id}/active-signons/{signon_id}")
def office_sign_off(
    site_id: str, signon_id: str, request: Request,
    caller: Caller = Depends(require_permission("sites", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Sign someone out from the office (for example at the end of the day). The visit stays in the log."""
    site = _get_site(db, caller, site_id)
    row = db.get(SiteAttendance, signon_id)
    if row is None or row.site_id != site.id or row.deleted_at is not None:
        raise HTTPException(404, "Sign-on not found")
    changed = _close(row, source="office", by=caller.user.id)
    if changed:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="site.attendance_signed_out",
              target_type="site", target_id=site.id, detail={"attendance_id": row.id, "kind": row.kind})
        db.commit()
    return {"ok": True, "already": not changed, **attendance_out(row, site)}


def _log_rows(db: Session, org: Organisation, site: Site, date_from: date | None, date_to: date | None, kind: str | None) -> list[SiteAttendance]:
    if date_from and date_to and date_to < date_from:
        raise HTTPException(422, "The end date is before the start date")
    start, end = _day_range(org, date_from, date_to)
    stmt = select(SiteAttendance).where(SiteAttendance.site_id == site.id, SiteAttendance.deleted_at.is_(None))
    if start:
        stmt = stmt.where(SiteAttendance.signed_in_at >= start)
    if end:
        stmt = stmt.where(SiteAttendance.signed_in_at < end)
    if kind:
        stmt = stmt.where(SiteAttendance.kind == kind)
    return list(db.scalars(stmt.order_by(SiteAttendance.signed_in_at.desc()).limit(MAX_ROWS + 1)).all())


@router.get("/{site_id}/signon-log")
def signon_log(
    site_id: str, date_from: date | None = None, date_to: date | None = None,
    kind: Literal["worker", "visitor"] | None = None,
    caller: Caller = Depends(require_permission("sites", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """History for one site, newest first. At most 500 rows; narrow the dates for older periods."""
    site = _get_site(db, caller, site_id)
    rows = _log_rows(db, _org(db, caller.org_id), site, date_from, date_to, kind)
    now = utcnow()
    return {"items": [attendance_out(r, site, now=now) for r in rows[:MAX_ROWS]], "truncated": len(rows) > MAX_ROWS, "limit": MAX_ROWS}


@router.post("/{site_id}/signon-log/export")
def export_log(
    site_id: str, request: Request, date_from: date | None = None, date_to: date | None = None,
    kind: Literal["worker", "visitor"] | None = None,
    caller: Caller = Depends(require_permission("sites", "view")), db: Session = Depends(get_db),
) -> Response:
    site = _get_site(db, caller, site_id)
    org = _org(db, caller.org_id)
    rows = _log_rows(db, org, site, date_from, date_to, kind)[:MAX_ROWS]
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="site.attendance_exported",
          target_type="site", target_id=site.id, detail={"rows": len(rows)})
    db.commit()
    return _csv(rows, {site.id: site}, org.timezone or "Australia/Hobart")


@router.post("/{site_id}/signoff")
def sign_off(site_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The signed-in person signs themself off a site. Repeating it changes nothing."""
    site = _get_site(db, caller, site_id)
    return _self_sign_off(db, caller, site)


# ---------- signed-in sign-on by QR ----------

def _public_site(db: Session, site: Site) -> dict[str, Any]:
    org = _org(db, site.org_id)
    return {
        "site_name": site.name, "address": _address(site),
        "organisation": (org.details or {}).get("trading_name") or org.name, "timezone": org.timezone,
        "questions": list(site.signon_questions or []),
        "visitor_declaration": site.visitor_declaration or DEFAULT_DECLARATION,
    }


def _my_open(db: Session, caller: Caller, site: Site) -> SiteAttendance | None:
    return db.scalars(
        select(SiteAttendance).where(SiteAttendance.site_id == site.id, SiteAttendance.kind == "worker",
                                     SiteAttendance.user_id == caller.user.id, SiteAttendance.signed_out_at.is_(None),
                                     SiteAttendance.deleted_at.is_(None))
    ).first()


def _self_sign_off(db: Session, caller: Caller, site: Site) -> dict[str, Any]:
    row = _my_open(db, caller, site)
    if row is None:
        return {"ok": True, "already": True, "on_site": False}
    _close(row, source="self", by=caller.user.id)
    db.commit()
    return {"ok": True, "already": False, **attendance_out(row, site)}


@scan_router.get("/site/{scan_token}")
def scan_site(scan_token: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """What anyone holding the QR code may see: the site's name and address, whose site it is,
    and what will be asked at sign-in. Nothing else."""
    return _public_site(db, _site_by_token(db, scan_token))


@scan_router.get("/site/{scan_token}/me")
def scan_site_me(scan_token: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    site = _site_by_token(db, scan_token)
    if site.org_id != caller.org_id:
        # A login from another organisation is a visitor here.
        raise HTTPException(403, "Your login belongs to a different organisation. Sign in as a visitor instead.")
    row = _my_open(db, caller, site)
    return {**_public_site(db, site), "site_id": site.id, "signed_on": attendance_out(row, site) if row else None}


@scan_router.post("/site/{scan_token}/sign-on", status_code=201)
def worker_sign_on(
    scan_token: str, body: WorkerSignOn, response: Response,
    caller: Caller = Depends(get_caller), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Sign the signed-in person on. Sending it twice returns the visit already open."""
    site = _site_by_token(db, scan_token)
    if site.org_id != caller.org_id:
        raise HTTPException(403, "Your login belongs to a different organisation. Sign in as a visitor instead.")
    existing = _my_open(db, caller, site)
    if existing is not None:
        response.status_code = 200
        return {"already": True, **attendance_out(existing, site)}
    worker = people.worker_for_user(db, caller.user)
    row = SiteAttendance(
        org_id=site.org_id, site_id=site.id, kind="worker", user_id=caller.user.id, worker_id=worker.id if worker else None,
        name=people.worker_name(worker) if worker else caller.user.name, answers=_answers(site, body.answers),
        gps=_gps(body), source="qr", declaration_acknowledged=False,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Two requests raced; the first one opened the visit.
        db.rollback()
        existing = _my_open(db, caller, site)
        if existing is None:
            raise
        response.status_code = 200
        return {"already": True, **attendance_out(existing, site)}
    return {"already": False, **attendance_out(row, site)}


@scan_router.post("/site/{scan_token}/sign-off")
def worker_sign_off(scan_token: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    site = _site_by_token(db, scan_token)
    if site.org_id != caller.org_id:
        raise HTTPException(403, "Your login belongs to a different organisation.")
    return _self_sign_off(db, caller, site)


# ---------- visitors (no login) ----------

def _ip_hash(request: Request, site: Site) -> str:
    ip = request.client.host if request.client else "unknown"
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return hashlib.sha256(f"{site.id}:{forwarded or ip}".encode()).hexdigest()


def _visitor_public(row: SiteAttendance, site: Site) -> dict[str, Any]:
    return {"visitor_id": row.id, "name": row.name, "site_name": site.name, "signed_in_at": iso(row.signed_in_at),
            "signed_out_at": iso(row.signed_out_at), "on_site": row.signed_out_at is None}


@public_router.get("/site/{scan_token}/form")
def visitor_form(scan_token: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _public_site(db, _site_by_token(db, scan_token))


@public_router.post("/visitor/site/{scan_token}/signin", status_code=201)
def visitor_sign_in(
    scan_token: str, body: VisitorSignIn, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Record a visitor arriving. Needs the site rules ticked. Limited to 10 an hour from one address."""
    site = _site_by_token(db, scan_token)
    if not body.induction_acknowledged:
        raise HTTPException(422, "Please tick the box to accept the site rules before signing in")
    name = " ".join(body.name.split())
    phone = _text(body.phone)

    def same_visit() -> SiteAttendance | None:
        if body.client_ref:
            found = db.scalars(select(SiteAttendance).where(SiteAttendance.site_id == site.id, SiteAttendance.client_ref == body.client_ref)).first()
            if found is not None:
                return found
        # The same person signing in again while still on site is the same visit.
        stmt = select(SiteAttendance).where(
            SiteAttendance.site_id == site.id, SiteAttendance.kind == "visitor", SiteAttendance.signed_out_at.is_(None),
            SiteAttendance.deleted_at.is_(None), func.lower(SiteAttendance.name) == name.lower(),
        )
        stmt = stmt.where(SiteAttendance.phone == phone) if phone else stmt.where(SiteAttendance.phone.is_(None))
        return db.scalars(stmt).first()

    existing = same_visit()
    if existing is not None:
        response.status_code = 200
        return {"already": True, **_visitor_public(existing, site)}

    ip_hash = _ip_hash(request, site)
    recent = db.scalar(select(func.count()).select_from(SiteAttendance).where(
        SiteAttendance.site_id == site.id, SiteAttendance.kind == "visitor", SiteAttendance.ip_hash == ip_hash,
        SiteAttendance.signed_in_at > utcnow() - timedelta(hours=1))) or 0
    if recent >= VISITOR_LIMIT_PER_HOUR:
        raise HTTPException(429, "Too many sign-ins from this device in the last hour. Please see site staff.")

    row = SiteAttendance(
        id=new_id(), org_id=site.org_id, site_id=site.id, kind="visitor", name=name, company=_text(body.company), phone=phone,
        purpose=_text(body.purpose), host=_text(body.host), declaration_acknowledged=True,
        answers=_answers(site, body.answers), gps=_gps(body), source="qr", client_ref=body.client_ref, ip_hash=ip_hash,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = same_visit()
        if existing is None:
            raise
        response.status_code = 200
        return {"already": True, **_visitor_public(existing, site)}
    return {"already": False, **_visitor_public(row, site)}


def _visitor_by_token(db: Session, visitor_id: str, token: str) -> tuple[SiteAttendance, Site]:
    """A visitor row is only reachable with the QR token of the site it belongs to."""
    site = _site_by_token(db, token)
    row = db.get(SiteAttendance, visitor_id)
    if row is None or row.site_id != site.id or row.kind != "visitor" or row.deleted_at is not None:
        raise HTTPException(404, "Visit not found")
    return row, site


@public_router.get("/visitor/{visitor_id}")
def visitor_status(visitor_id: str, token: str = Query(..., max_length=80), db: Session = Depends(get_db)) -> dict[str, Any]:
    row, site = _visitor_by_token(db, visitor_id, token)
    return _visitor_public(row, site)


@public_router.post("/visitor/{visitor_id}/sign-out")
def visitor_sign_out(visitor_id: str, token: str = Query(..., max_length=80), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Sign a visitor out. Repeating it keeps the first sign-out time."""
    row, site = _visitor_by_token(db, visitor_id, token)
    changed = _close(row, source="self", by=None)
    if changed:
        db.commit()
    return {"already": not changed, **_visitor_public(row, site)}


# ---------- office visitor register ----------

def _visitor_stmt(caller: Caller, *, include_archived: bool, include_deleted: bool):
    stmt = select(SiteAttendance, Site).join(Site, Site.id == SiteAttendance.site_id).where(
        SiteAttendance.org_id == caller.org_id, SiteAttendance.kind == "visitor")
    if not include_deleted:
        stmt = stmt.where(SiteAttendance.deleted_at.is_(None))
    if not include_archived:
        stmt = stmt.where(SiteAttendance.archived_at.is_(None))
    return stmt


def _visitor_filters(db: Session, caller: Caller, stmt, site_id, on_site, date_from, date_to, q):
    if date_from and date_to and date_to < date_from:
        raise HTTPException(422, "The end date is before the start date")
    start, end = _day_range(_org(db, caller.org_id), date_from, date_to)
    if site_id:
        stmt = stmt.where(SiteAttendance.site_id == site_id)
    if on_site is True:
        stmt = stmt.where(SiteAttendance.signed_out_at.is_(None))
    elif on_site is False:
        stmt = stmt.where(SiteAttendance.signed_out_at.is_not(None))
    if start:
        stmt = stmt.where(SiteAttendance.signed_in_at >= start)
    if end:
        stmt = stmt.where(SiteAttendance.signed_in_at < end)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(SiteAttendance.name).like(like), func.lower(func.coalesce(SiteAttendance.company, "")).like(like),
                              func.lower(func.coalesce(SiteAttendance.host, "")).like(like)))
    return stmt


@visitors_router.get("")
def list_visitors(
    site_id: str | None = None, on_site: bool | None = None, date_from: date | None = None, date_to: date | None = None,
    q: str | None = Query(None, max_length=100), include_archived: bool = False, include_deleted: bool = False,
    caller: Caller = Depends(require_permission("sites_visitors", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Visitor sign-ins, newest first. At most 500 rows; narrow the dates for older periods."""
    stmt = _visitor_filters(db, caller, _visitor_stmt(caller, include_archived=include_archived, include_deleted=include_deleted),
                            site_id, on_site, date_from, date_to, q)
    pairs = db.execute(stmt.order_by(SiteAttendance.signed_in_at.desc()).limit(MAX_ROWS + 1)).all()
    now = utcnow()
    return {"items": [attendance_out(a, s, now=now) for a, s in pairs[:MAX_ROWS]], "truncated": len(pairs) > MAX_ROWS, "limit": MAX_ROWS}


@visitors_router.get("/category-counts")
def visitor_counts(
    caller: Caller = Depends(require_permission("sites_visitors", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """On site now, signed in today (local day), and all time. Same population as the default list."""
    org = _org(db, caller.org_id)
    base = select(func.count()).select_from(SiteAttendance).where(
        SiteAttendance.org_id == caller.org_id, SiteAttendance.kind == "visitor",
        SiteAttendance.deleted_at.is_(None), SiteAttendance.archived_at.is_(None))
    start, end = _day_range(org, people.today_for(org), people.today_for(org))
    return {
        "on_site": int(db.scalar(base.where(SiteAttendance.signed_out_at.is_(None))) or 0),
        "today": int(db.scalar(base.where(SiteAttendance.signed_in_at >= start, SiteAttendance.signed_in_at < end)) or 0),
        "total": int(db.scalar(base) or 0),
    }


@visitors_router.get("/export.csv")
def export_visitors(
    request: Request, site_id: str | None = None, on_site: bool | None = None, date_from: date | None = None,
    date_to: date | None = None, q: str | None = Query(None, max_length=100),
    caller: Caller = Depends(require_permission("sites_visitors", "view")), db: Session = Depends(get_db),
) -> Response:
    stmt = _visitor_filters(db, caller, _visitor_stmt(caller, include_archived=False, include_deleted=False), site_id, on_site, date_from, date_to, q)
    pairs = db.execute(stmt.order_by(SiteAttendance.signed_in_at.desc()).limit(MAX_ROWS)).all()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="visitors.exported",
          target_type="organisation", target_id=caller.org_id, detail={"rows": len(pairs)})
    db.commit()
    return _csv([a for a, _ in pairs], {s.id: s for _, s in pairs}, _org(db, caller.org_id).timezone or "Australia/Hobart")


@visitors_router.post("/bulk-delete")
def bulk_delete_visitors(
    body: BulkIds, request: Request,
    caller: Caller = Depends(require_permission("sites_visitors", "delete")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Soft-delete up to 500 visitor rows. Rows that cannot be deleted are reported, not silently skipped."""
    deleted, skipped = 0, []
    now = utcnow()
    for vid in dict.fromkeys(body.ids):
        row = db.get(SiteAttendance, vid)
        if row is None or row.org_id != caller.org_id or row.kind != "visitor":
            skipped.append({"id": vid, "reason": "not_found"})
        elif row.deleted_at is not None:
            skipped.append({"id": vid, "reason": "already_deleted"})
        else:
            _close(row, source="office", by=caller.user.id)
            row.deleted_at, row.deleted_by = now, caller.user.id
            deleted += 1
    if deleted:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="visitors.bulk_deleted",
              target_type="organisation", target_id=caller.org_id, detail={"deleted": deleted})
        db.commit()
    return {"deleted": deleted, "skipped": skipped}


def _get_visitor(db: Session, caller: Caller, visitor_id: str, *, include_deleted: bool = False) -> tuple[SiteAttendance, Site]:
    row = db.get(SiteAttendance, visitor_id)
    if row is None or row.org_id != caller.org_id or row.kind != "visitor" or (row.deleted_at is not None and not include_deleted):
        raise HTTPException(404, "Visitor not found")
    return row, db.get(Site, row.site_id)


@visitors_router.get("/{visitor_id}")
def get_visitor(
    visitor_id: str, caller: Caller = Depends(require_permission("sites_visitors", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    row, site = _get_visitor(db, caller, visitor_id)
    return attendance_out(row, site)


@visitors_router.patch("/{visitor_id}")
def correct_visitor(
    visitor_id: str, body: VisitorCorrection, request: Request,
    caller: Caller = Depends(require_permission("sites_visitors", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """An authorised correction. A reason is required and the earlier values go to the audit trail."""
    row, site = _get_visitor(db, caller, visitor_id)
    data = body.model_dump(exclude_unset=True)
    before: dict[str, Any] = {}
    for key in ("name", "company", "phone", "purpose", "host"):
        if key in data:
            value = " ".join(data[key].split()) if key == "name" and data[key] else _text(data[key])
            if key == "name" and not value:
                continue
            if getattr(row, key) != value:
                before[key] = getattr(row, key)
                setattr(row, key, value)
    if data.get("signed_out_at") is not None:
        when = data["signed_out_at"]
        if when.tzinfo is None:
            raise HTTPException(422, "The sign-out time must include its timezone")
        if when < row.signed_in_at:
            raise HTTPException(422, "The sign-out time is before the sign-in time")
        if when > utcnow() + timedelta(minutes=1):
            raise HTTPException(422, "The sign-out time is in the future")
        if row.signed_out_at != when:
            before["signed_out_at"] = iso(row.signed_out_at)
            row.signed_out_at, row.sign_out_source, row.signed_out_by = when, "office", caller.user.id
    if before:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="visitor.corrected",
              target_type="site_attendance", target_id=row.id, detail={"reason": body.reason.strip(), "before": before})
        db.commit()
    return attendance_out(row, site)


@visitors_router.post("/{visitor_id}/force-signout")
def force_sign_out(
    visitor_id: str, request: Request,
    caller: Caller = Depends(require_permission("sites_visitors", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Sign a visitor out from the office. Writes a record only; nobody is notified."""
    row, site = _get_visitor(db, caller, visitor_id)
    changed = _close(row, source="office", by=caller.user.id)
    if changed:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="visitor.signed_out_by_office",
              target_type="site_attendance", target_id=row.id)
        db.commit()
    return {"already": not changed, **attendance_out(row, site)}


@visitors_router.delete("/{visitor_id}")
def delete_visitor(
    visitor_id: str, request: Request,
    caller: Caller = Depends(require_permission("sites_visitors", "delete")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    row, _site = _get_visitor(db, caller, visitor_id, include_deleted=True)
    if row.deleted_at is not None:
        return {"ok": True, "already": True}
    _close(row, source="office", by=caller.user.id)
    row.deleted_at, row.deleted_by = utcnow(), caller.user.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="visitor.deleted",
          target_type="site_attendance", target_id=row.id)
    db.commit()
    return {"ok": True, "already": False}


@visitors_router.post("/{visitor_id}/archive")
def archive_visitor(
    visitor_id: str, request: Request,
    caller: Caller = Depends(require_permission("sites_visitors", "delete")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    row, site = _get_visitor(db, caller, visitor_id)
    if row.signed_out_at is None:
        raise HTTPException(409, "This visitor is still on site. Sign them out before archiving.")
    if row.archived_at is None:
        row.archived_at = utcnow()
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="visitor.archived",
              target_type="site_attendance", target_id=row.id)
        db.commit()
    return attendance_out(row, site)


@visitors_router.post("/{visitor_id}/unarchive")
def unarchive_visitor(
    visitor_id: str, request: Request,
    caller: Caller = Depends(require_permission("sites_visitors", "delete")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    row, site = _get_visitor(db, caller, visitor_id)
    if row.archived_at is not None:
        row.archived_at = None
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="visitor.unarchived",
              target_type="site_attendance", target_id=row.id)
        db.commit()
    return attendance_out(row, site)
