"""The phone app's server side: module allocation and the phone home (stage 8).

Module allocation decides what the phone shows a person: which tabs and menu entries. It is
worked out here, on the server, from three layers (the person's own setting, then their role's,
then the defaults for that kind of role) and the phone never decides for itself.

It is NOT the security boundary. Every screen the phone opens calls the same routes as the
office, with the same permission and organisation checks. Switching a module on cannot give
anyone access their permissions do not; switching it off only removes it from their phone.
"""
import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Caller, get_caller, require_admin, require_permission
from ..models import DailyJob, LeaveRequest, Organisation, PhoneModuleSetting, Role, Site, SiteAttendance, TimesheetDay, User, utcnow
from ..people import today_for, worker_for_user
from ..services import audit, effective_for, get_role, iso
from .. import permissions as perms

router = APIRouter(tags=["phone"])

BUCKETS = {"worker": "Workers", "supervisor": "Supervisors and office staff", "contractor": "Contractors", "admin": "Administrators"}
# key -> (label, where it opens, permission needed as (resource, action) or None, built?)
MODULES: dict[str, tuple[str, str, tuple[str, str] | None, bool]] = {
    "sign_on": ("Site sign-on", "/app/scan", None, True),
    "daily_jobs": ("Today's job", "/app/my-work", None, True),
    "forms": ("Forms", "/app/forms", ("forms", "view"), True),
    "pre_start": ("Pre-starts", "/app/r/pre-starts", ("pre_starts", "view"), True),
    "site_diary": ("Site diary", "/app/r/site-diary", ("site_diary", "view"), True),
    "hazard": ("Hazards", "/app/r/incidents", ("hazards", "view"), True),
    "incident": ("Incidents", "/app/r/incidents", ("incidents", "view"), True),
    "inspection": ("Inspections", "/app/r/inspections", ("inspections", "view"), True),
    "swms": ("SWMS", "/app/r/swms", ("swms", "view"), True),
    "inductions": ("Inductions and training", "/app/inductions", ("inductions", "view"), True),
    "certifications": ("Certifications", "/app/certifications", ("certifications", "view"), True),
    "plant_vehicles": ("Fleet and equipment", "/app/fleet", ("assets", "view"), True),
    "document_library": ("Document library", "/app/documents", ("documents", "view"), True),
    "contractors": ("My company (contractors)", "/app/suppliers", ("contractors", "view"), True),
    "suppliers": ("Suppliers", "/app/suppliers", ("suppliers", "view"), True),
    "workers": ("Workers", "/app/workers", ("workers", "view"), True),
    "users_directory": ("Users", "/app/settings/users", ("users", "view"), True),
    "leave": ("My leave", "/app/my-leave", None, True),
    "timesheets": ("My timesheet", "/app/my-timesheet", None, True),
    "payslips": ("My payslips", "/app/my-payslips", None, True),
    "profile": ("My profile", "/app/profile", None, True),
    "ask_intel": ("Ask (AI assistant)", "", None, False),
    "compliance_snapshot": ("Compliance snapshot", "", None, False),
}
_ON = {
    "worker": {"pre_start", "site_diary", "hazard", "incident", "inspection", "swms", "inductions", "certifications", "sign_on", "profile", "forms", "daily_jobs", "leave", "timesheets", "payslips"},
    "supervisor": {"pre_start", "site_diary", "hazard", "incident", "inspection", "swms", "inductions", "plant_vehicles", "certifications", "ask_intel", "sign_on", "profile", "forms", "document_library",
                   "contractors", "suppliers", "workers", "compliance_snapshot", "daily_jobs", "leave", "timesheets", "payslips"},
    "contractor": {"hazard", "incident", "swms", "inductions", "sign_on", "profile", "forms", "document_library", "contractors"},
    "admin": set(MODULES),
}
DEFAULTS = {bucket: {key: key in on for key in MODULES} for bucket, on in _ON.items()}


class BucketsIn(BaseModel):
    buckets: dict[Literal["worker", "supervisor", "contractor", "admin"], dict[str, bool]]


class OverrideIn(BaseModel):
    role_id: str | None = None
    user_id: str | None = None
    module_key: str
    value: bool | None  # None removes the exception, so the setting is inherited again


def bucket_of(role: Role | None) -> str:
    """The kind of role. Anything unrecognised gets the most restrictive set, never the widest."""
    legacy = role.legacy_role if role is not None else None
    return legacy if legacy in BUCKETS else "contractor"


def _rows(db: Session, org_id: str) -> dict[tuple[str, str], PhoneModuleSetting]:
    return {(r.scope, r.subject): r for r in db.scalars(select(PhoneModuleSetting).where(PhoneModuleSetting.org_id == org_id)).all()}


def resolve(db: Session, user: User, rows: dict[tuple[str, str], PhoneModuleSetting] | None = None) -> dict[str, Any]:
    """What one person's phone shows, and where each answer came from."""
    rows = rows if rows is not None else _rows(db, user.org_id)
    role = get_role(db, user.org_id, user.role_id, include_deleted=True)
    bucket = bucket_of(role)
    layers = [("person", (rows[("user", user.id)].modules if ("user", user.id) in rows else {})), ("role", (rows[("role", user.role_id)].modules if ("role", user.role_id) in rows else {})),
              ("default", {**DEFAULTS[bucket], **(rows[("bucket", bucket)].modules if ("bucket", bucket) in rows else {})})]
    effective = effective_for(db, user)
    out = []
    for key, (label, path, needs, built) in MODULES.items():
        source, allocated = next(((name, layer[key]) for name, layer in layers if isinstance(layer.get(key), bool)), ("default", False))
        permitted = needs is None or perms.can(effective, *needs)
        out.append({"key": key, "label": label, "path": path, "allocated": allocated, "from": source, "available": built, "permitted": permitted,
                    "shown": allocated and built and permitted,
                    "why_not": None if allocated and built and permitted else "Not built in this version" if not built else "Switched off for this person" if not allocated
                    else "Their permissions do not include this"})
    return {"bucket": bucket, "bucket_label": BUCKETS[bucket], "role_id": user.role_id, "modules": out, "shown": [m["key"] for m in out if m["shown"]]}


@router.get("/api/me/mobile-modules")
def my_modules(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """What this person's phone shows. Worked out on the server for the signed-in person only."""
    resolved = resolve(db, caller.user)
    return {**resolved, "flat": {m["key"]: m["shown"] for m in resolved["modules"]}}


@router.get("/api/settings/mobile-modules")
def get_settings(caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = _rows(db, caller.org_id)
    roles = {r.role_id: r for r in db.scalars(select(Role).where(Role.org_id == caller.org_id, Role.deleted_at.is_(None))).all()}
    users = {u.id: u.name for u in db.scalars(select(User).where(User.org_id == caller.org_id, User.id.in_([s for (scope, s) in rows if scope == "user"]))).all()}
    return {"modules": [{"key": k, "label": v[0], "available": v[3]} for k, v in MODULES.items()], "buckets": [{"key": b, "label": label} for b, label in BUCKETS.items()],
            "defaults": DEFAULTS, "bucket_settings": {b: {**DEFAULTS[b], **(rows[("bucket", b)].modules if ("bucket", b) in rows else {})} for b in BUCKETS},
            "role_overrides": [{"role_id": s, "role": roles[s].name if s in roles else s, "bucket": bucket_of(roles.get(s)), "modules": r.modules} for (scope, s), r in rows.items() if scope == "role" and r.modules],
            "user_overrides": [{"user_id": s, "user": users.get(s, "Removed user"), "modules": r.modules} for (scope, s), r in rows.items() if scope == "user" and r.modules],
            "roles": [{"id": r.role_id, "name": r.name, "bucket": bucket_of(r)} for r in sorted(roles.values(), key=lambda r: r.name.lower())],
            "note": "This decides what each person's phone shows. It does not give anyone access: permissions do that."}


def _row(db: Session, org_id: str, scope: str, subject: str, rows: dict[tuple[str, str], PhoneModuleSetting]) -> PhoneModuleSetting:
    row = rows.get((scope, subject))
    if row is None:
        row = PhoneModuleSetting(org_id=org_id, scope=scope, subject=subject, modules={})
        db.add(row)
        rows[(scope, subject)] = row
    return row


@router.put("/api/settings/mobile-modules")
def put_buckets(body: BucketsIn, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Set the defaults for each kind of role. Exceptions made for a role or a person are left exactly as they are."""
    rows = _rows(db, caller.org_id)
    changes = 0
    for bucket, modules in body.buckets.items():
        unknown = [k for k in modules if k not in MODULES]
        if unknown:
            raise HTTPException(422, f"Unknown module: {unknown[0]}")
        row = _row(db, caller.org_id, "bucket", bucket, rows)
        current = {**DEFAULTS[bucket], **(row.modules or {})}
        changes += sum(1 for k, v in modules.items() if current.get(k) != v)
        row.modules, row.updated_by = {**(row.modules or {}), **modules}, caller.user.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="phone_modules.defaults_changed", target_type="organisation", target_id=caller.org_id, detail={"changes": changes})
    db.commit()
    return {**get_settings(caller, db), "changes": changes}


@router.patch("/api/settings/mobile-modules/overrides")
def set_override(body: OverrideIn, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Make, change or remove an exception for one role or one person."""
    if body.module_key not in MODULES:
        raise HTTPException(422, f"Unknown module: {body.module_key}")
    if bool(body.role_id) == bool(body.user_id):
        raise HTTPException(422, "Give a role or a person, not both")
    if body.role_id:
        role = get_role(db, caller.org_id, body.role_id)
        if role is None:
            raise HTTPException(404, "Role not found")
        scope, subject = "role", role.role_id
    else:
        user = db.get(User, body.user_id)
        if user is None or user.org_id != caller.org_id or user.deleted_at is not None:
            raise HTTPException(404, "User not found")
        scope, subject = "user", user.id
    rows = _rows(db, caller.org_id)
    row = _row(db, caller.org_id, scope, subject, rows)
    modules = dict(row.modules or {})
    if body.value is None:
        modules.pop(body.module_key, None)
    else:
        modules[body.module_key] = body.value
    row.modules, row.updated_by = modules, caller.user.id
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="phone_modules.exception_changed", target_type=scope, target_id=subject, detail={"module": body.module_key, "value": body.value})
    db.commit()
    return {"scope": scope, "subject": subject, "modules": modules}


@router.delete("/api/settings/mobile-modules/overrides/{scope}/{subject}", status_code=204)
def clear_overrides(scope: Literal["role", "user"], subject: str, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> Response:
    """Remove every exception for one role or one person, so they follow the defaults again."""
    row = db.scalars(select(PhoneModuleSetting).where(PhoneModuleSetting.org_id == caller.org_id, PhoneModuleSetting.scope == scope, PhoneModuleSetting.subject == subject)).first()
    if row is None:
        raise HTTPException(404, "No exceptions are set for this")
    db.delete(row)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="phone_modules.exceptions_cleared", target_type=scope, target_id=subject)
    db.commit()
    return Response(status_code=204)


@router.get("/api/mobile/preview-user")
def preview_user(user_id: str, caller: Caller = Depends(require_permission("users", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """What a particular person's phone shows, and why each thing is or is not there. Reads only."""
    user = db.get(User, user_id)
    if user is None or user.org_id != caller.org_id or user.deleted_at is not None:
        raise HTTPException(404, "User not found")
    return {"user": {"id": user.id, "name": user.name, "status": user.status}, **resolve(db, user)}


@router.get("/api/mobile/home")
def home(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The phone's first screen: who you are, what is on today, and what needs doing, limited to the
    modules switched on for you. Nothing here is cached on the device."""
    org = db.get(Organisation, caller.org_id)
    today = today_for(org)
    resolved = resolve(db, caller.user)
    shown = set(resolved["shown"])
    worker = worker_for_user(db, caller.user)
    out: dict[str, Any] = {"user": {"name": caller.user.name, "first_name": caller.user.name.split(" ")[0], "linked_to_worker": worker is not None}, "org": org.name,
                           "today": today.isoformat(), "modules": [m for m in resolved["modules"] if m["shown"]], "todo": []}
    if "daily_jobs" in shown and worker is not None:
        job = db.scalars(select(DailyJob).where(DailyJob.org_id == org.id, DailyJob.worker_id == worker.id, DailyJob.job_date == today, DailyJob.status.in_(("issued", "accepted")))).first()
        site = db.get(Site, job.site_id) if job is not None and job.site_id else None
        out["today_job"] = {"id": job.id, "site": site.name if site else job.site_name, "status": job.status, "start_time": job.start_time} if job is not None else None
        if job is not None and job.status == "issued":
            out["todo"].append({"text": "Accept or decline today's job", "path": "/app/my-work"})
    if "sign_on" in shown:
        visits = db.scalars(select(SiteAttendance).where(SiteAttendance.org_id == org.id, SiteAttendance.kind == "worker", SiteAttendance.user_id == caller.user.id, SiteAttendance.signed_out_at.is_(None),
                                                         SiteAttendance.deleted_at.is_(None))).all()
        sites = {s.id: s.name for s in db.scalars(select(Site).where(Site.id.in_({v.site_id for v in visits}))).all()} if visits else {}
        out["signed_on"] = [{"site": sites.get(v.site_id, ""), "since": iso(v.signed_in_at)} for v in visits]
    if "timesheets" in shown and worker is not None:
        start = today - dt.timedelta(days=today.weekday())
        days = db.scalars(select(TimesheetDay).where(TimesheetDay.worker_id == worker.id, TimesheetDay.work_date >= start - dt.timedelta(days=7), TimesheetDay.work_date <= today)).all()
        back = sum(1 for d in days if d.status == "rejected")
        unsent = sum(1 for d in days if d.status == "draft" and (d.paid_hours > 0 or d.kind != "work"))
        if back:
            out["todo"].append({"text": f"{back} timesheet day{'s' if back != 1 else ''} sent back to fix", "path": "/app/my-timesheet"})
        if unsent:
            out["todo"].append({"text": f"{unsent} timesheet day{'s' if unsent != 1 else ''} not sent yet", "path": "/app/my-timesheet"})
    if "leave" in shown and worker is not None:
        waiting = db.scalar(select(func.count()).select_from(LeaveRequest).where(LeaveRequest.worker_id == worker.id, LeaveRequest.status == "requested")) or 0
        out["leave_waiting"] = waiting
    out["native_apps"] = {"android": None, "ios": None, "note": "There is no Android or iPhone app to download. Add this page to your home screen instead."}
    return out


NO_NATIVE_APP = "There is no Android or iPhone app for this portal. Open the portal in the phone's browser and add it to the home screen."


@router.get("/api/mobile/downloads/android/version")
def android_version(caller: Caller = Depends(get_caller)) -> dict[str, Any]:
    """No native app has been built or signed. Said plainly, so nothing can mistake a missing file for a fault."""
    return {"available": False, "version": None, "reason": NO_NATIVE_APP}


@router.get("/api/mobile/downloads/android/latest.apk")
def android_download(caller: Caller = Depends(get_caller)) -> None:
    raise HTTPException(404, NO_NATIVE_APP)
