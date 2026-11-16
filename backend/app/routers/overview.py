"""Dashboard numbers and search across registers (stage 9a).

Two rules shape this file:

1. Every number on the dashboard is produced by the same function the register itself uses for its
   own counts or list. A dashboard figure therefore cannot disagree with the screen it links to,
   and it carries the same organisation and own-records scoping.
2. One number that cannot be worked out is reported as unavailable. It is never shown as zero, and
   it never takes the other numbers down with it.
"""
import logging
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import safety as safety_kinds
from ..db import get_db
from ..deps import Caller, get_caller
from ..models import AuditLog, Organisation, User
from ..people import today_for
from ..services import iso
from . import comms, documents, fleet, fuel, leave, renewals, service, sites, suppliers, timesheets, users, workers
from . import safety as safety_routes

log = logging.getLogger("portal.overview")
router = APIRouter(tags=["overview"])


def _safety_list(kind_key: str) -> Callable[..., dict[str, Any]]:
    """The register's own list function, looked up by the name it was registered under."""
    for candidate in (safety_routes.swms_router, *safety_routes.routers):
        for route in candidate.routes:
            if getattr(route, "name", None) == f"{kind_key}_list":
                return route.endpoint
    raise LookupError(kind_key)


def _safety_total(caller: Caller, db: Session, kind_key: str, status: str) -> int:
    return int(_safety_list(kind_key)(status=status, q=None, category=None, date_from=None, date_to=None, include_archived=False, include_superseded=False,
                                      limit=1, offset=0, caller=caller, db=db)["total"])


def _tiles(caller: Caller, db: Session) -> list[dict[str, Any]]:
    """Each entry: section, key, label, tone when above zero, what the number means, where it leads, and how to work it out."""
    can = caller.can
    out: list[dict[str, Any]] = []
    cache: dict[str, Any] = {}

    def once(name: str, fn: Callable[[], Any]) -> Any:
        if name not in cache:
            cache[name] = fn()
        return cache[name]

    def add(section: str, key: str, label: str, tone: str, definition: str, path: str, fn: Callable[[], int]) -> None:
        out.append({"section": section, "key": key, "label": label, "tone": tone, "definition": definition, "path": path, "fn": fn})

    if can("certifications", "view"):
        certs = lambda: once("certs", lambda: workers.expiry_count(caller=caller, db=db))  # noqa: E731
        scope = "active workers" if can("workers", "team_view") else "your own record"
        add("People", "certs_expired", "Certificates expired", "bad", f"Current certificates of {scope} whose expiry date has passed.", "/app/certifications?status=expired", lambda: certs()["expired"])
        add("People", "certs_expiring", "Certificates expiring soon", "warn", f"Current certificates of {scope} that expire within the warning period set under Certifications.",
            "/app/certifications?status=expiring", lambda: certs()["expiring_soon"])
        add("People", "certs_unknown", "Certificates with no expiry date", "warn", f"Current certificates of {scope} with no expiry date recorded and not marked as never expiring. These are not counted as valid.",
            "/app/certifications?status=unknown", lambda: certs()["unknown"])
    if can("workers", "view") and can("workers", "team_view"):
        add("People", "leave_waiting", "Leave requests waiting", "warn", "Leave requests that nobody has approved or declined yet.", "/app/leave",
            lambda: once("leave", lambda: leave.summary(caller=caller, db=db))["waiting"])
        add("People", "on_leave_today", "On leave today", "neutral", "Workers with approved leave that covers today.", "/app/leave",
            lambda: once("leave", lambda: leave.summary(caller=caller, db=db))["on_leave_today"])
        add("People", "workers_active", "Active workers", "neutral", "Worker records that are active and not archived.", "/app/workers",
            lambda: workers.list_workers(q=None, status="active", company_id=None, linked=None, limit=1, offset=0, caller=caller, db=db)["counts"]["active"])

    for kind in safety_kinds.KINDS.values():
        if not can(kind.resource, "view"):
            continue
        mine = " that you created" if safety_routes._own_only(caller, kind) else ""
        if kind.workflow == "review":
            add("Safety", f"{kind.key}_submitted", f"{kind.plural} waiting for review", "warn", f"{kind.plural}{mine} that have been submitted and not yet approved, rejected or sent back.",
                f"/app/r/{kind.prefix}?status=submitted", lambda k=kind.key: _safety_total(caller, db, k, "submitted"))
        else:
            add("Safety", f"{kind.key}_open", f"{kind.plural} open", "warn", f"{kind.plural}{mine} with the status Open. Those marked In progress are counted separately in the register.",
                f"/app/r/{kind.prefix}?status=open", lambda k=kind.key: _safety_total(caller, db, k, "open"))

    if can("renewals", "view"):
        add("Suppliers and sites", "renewals_ready", "Renewals ready to review", "warn", "Document requests where the supplier or worker has uploaded files and nobody has accepted or rejected them yet.",
            "/app/renewals", lambda: renewals.list_renewals(status="submitted", entity_type=None, entity_id=None, limit=1, offset=0, caller=caller, db=db)["total"])
    if can("suppliers", "view") or can("contractors", "view"):
        add("Suppliers and sites", "suppliers_active", "Active suppliers", "neutral", "Suppliers and contractors you can see that are active and not archived.", "/app/suppliers",
            lambda: suppliers.list_suppliers(q=None, status="active", kind="all", limit=1, offset=0, caller=caller, db=db)["counts"]["active"])
    if can("sites_visitors", "view"):
        add("Suppliers and sites", "visitors_on_site", "Visitors on site now", "neutral", "Visitors who have signed in and not yet signed out. Workers signed on to a site are shown on the Sites screen.",
            "/app/visitors", lambda: sites.visitor_counts(caller=caller, db=db)["on_site"])

    if can("assets", "view"):
        svc = lambda: once("service", lambda: service.summary(caller=caller, db=db))  # noqa: E731
        add("Fleet and fuel", "service_overdue", "Services overdue", "bad", "Service schedules past their due date, kilometres or hours.", "/app/fleet/service", lambda: svc()["overdue"])
        add("Fleet and fuel", "service_due_soon", "Services due soon", "warn", "Service schedules inside their warning margin.", "/app/fleet/service", lambda: svc()["due_soon"])
        add("Fleet and fuel", "service_unknown", "Services that cannot be worked out", "warn", "Service schedules with no starting point or no meter reading, so the due point is unknown. These are not counted as up to date.",
            "/app/fleet/service", lambda: svc()["unknown"])
        add("Fleet and fuel", "fuel_to_check", "Fuel fills to check", "warn", "Fuel fills with at least one open flag on the fuel check list.", "/app/fuel",
            lambda: fuel.anomalies(status="open", rule=None, asset_id=None, date_from=None, date_to=None, page=1, page_size=1, caller=caller, db=db)["total"])

    if can("documents", "view"):
        add("Records", "files_missing", "Library files that cannot be opened", "bad", "Document library entries whose stored file is missing from the server's file storage.", "/app/documents",
            lambda: documents.counts(caller=caller, db=db)["files_missing_binary"])
    if can("notifications", "view"):
        box = lambda: once("outbox", lambda: comms.list_outbox(status=None, channel=None, limit=1, offset=0, caller=caller, db=db))["counts"]  # noqa: E731
        add("Records", "outbox_failed", "Messages that failed to send", "bad", "Emails and texts the portal tried to send and the provider refused or did not answer.", "/app/settings/outbox", lambda: box()["failed"])
        add("Records", "outbox_held", "Messages held, not sent", "neutral", "Emails and texts prepared but not sent, because safe mode is on, there is no connection, or they carry a link passed on by hand.",
            "/app/settings/outbox", lambda: box()["held"])
    if can("payroll", "view"):
        add("Pay (test only)", "timesheets_waiting", "Timesheet days waiting this week", "warn", "Timesheet days in the current pay week that workers have sent and nobody has approved or sent back.",
            "/app/pay/timesheets", lambda: timesheets.summary(week_start=None, caller=caller, db=db)["by_status"]["submitted"]["days"])
    return out


@router.get("/api/dashboard/metrics")
def metrics(request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """What needs attention, limited to what the caller may see. Each figure says what it counts and links to the screen that lists it."""
    org = db.get(Organisation, caller.org_id)
    tiles = []
    for tile in _tiles(caller, db):
        fn = tile.pop("fn")
        try:
            tile["count"], tile["available"] = int(fn()), True
        except Exception:  # noqa: BLE001 - one figure failing must not hide the others, and must not read as zero
            db.rollback()
            log.exception("dashboard figure %s failed (request %s)", tile["key"], getattr(request.state, "request_id", "-"))
            tile["count"], tile["available"] = None, False
        tiles.append(tile)
    attention = [t for t in tiles if t["available"] and t["count"] and t["tone"] in ("bad", "warn")]
    attention.sort(key=lambda t: (t["tone"] != "bad", -t["count"]))
    recent = None
    if caller.is_admin or caller.can("audit_exports", "audit_view"):
        rows = db.scalars(select(AuditLog).where(AuditLog.org_id == caller.org_id).order_by(AuditLog.created_at.desc()).limit(8)).all()
        names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_({r.actor_user_id for r in rows if r.actor_user_id}))).all()} if rows else {}
        recent = [{"at": iso(r.created_at), "who": names.get(r.actor_user_id or "", "The portal" if r.actor_user_id is None else "Someone no longer listed"), "action": r.action,
                   "target_type": r.target_type} for r in rows]
    return {"today": today_for(org).isoformat(), "timezone": org.timezone, "tiles": tiles, "attention": attention, "unavailable": [t["key"] for t in tiles if not t["available"]],
            "recent_activity": recent}


# ---------- search ----------

def _kinds(request: Request, caller: Caller, db: Session, q: str, limit: int) -> dict[str, tuple[str, Callable[[], list[dict[str, Any]]]]]:
    """Kind -> (label, search). Each search calls the register's own list with its own scoping, so a result is always a record the caller could open."""
    can = caller.can
    out: dict[str, tuple[str, Callable[[], list[dict[str, Any]]]]] = {}

    def hit(title: str, subtitle: str | None, path: str) -> dict[str, Any]:
        return {"title": title, "subtitle": subtitle or None, "path": path}

    if can("workers", "view"):
        out["workers"] = ("Workers", lambda: [hit(w["name"], w.get("position"), f"/app/workers/{w['id']}")
                                              for w in workers.list_workers(q=q, status="active", company_id=None, linked=None, limit=limit, offset=0, caller=caller, db=db)["items"]])
    if can("sites", "view"):
        out["sites"] = ("Sites", lambda: [hit(s["name"], s.get("address_full"), f"/app/sites/{s['id']}")
                                          for s in sites.list_sites(request=request, q=q, status="active", limit=limit, offset=0, caller=caller, db=db)["items"]])
    if can("suppliers", "view") or can("contractors", "view"):
        out["suppliers"] = ("Suppliers", lambda: [hit(s["name"], s.get("trade"), f"/app/suppliers/{s['id']}")
                                                  for s in suppliers.list_suppliers(q=q, status="active", kind="all", limit=limit, offset=0, caller=caller, db=db)["items"]])
    if can("assets", "view"):
        out["assets"] = ("Fleet and equipment", lambda: [hit(a["name"], " · ".join(x for x in (a.get("registration"), a.get("make"), a.get("model")) if x), f"/app/fleet/{a['id']}")
                                                         for a in fleet.list_assets(kind=None, status=None, q=q, source=None, site_id=None, category_id=None, tag=None, page=1, page_size=limit,
                                                                                    caller=caller, db=db)["items"]])
    if can("documents", "view"):
        out["documents"] = ("Document library", lambda: [hit(d["filename"], d.get("folder_path"), f"/app/documents/{d['folder_id']}")
                                                         for d in documents.search(q=q, folder_id=None, recursive=True, include_archived=False, limit=limit, caller=caller, db=db)["results"]])
    for kind in safety_kinds.KINDS.values():
        if can(kind.resource, "view"):
            out[kind.key] = (kind.plural, lambda k=kind: [hit(r["title"], " · ".join(x for x in (r.get("record_date"), r.get("status_label")) if x), f"/app/r/{k.prefix}/{r['id']}")
                                                         for r in _safety_list(k.key)(status=None, q=q, category=None, date_from=None, date_to=None, include_archived=False,
                                                                                      include_superseded=False, limit=limit, offset=0, caller=caller, db=db)["items"]])
    if can("users", "view"):
        out["users"] = ("Users", lambda: [hit(u["name"], u.get("email"), f"/app/settings/users/{u['id']}")
                                          for u in users.list_users(q=q, status=None, role_id=None, include_deleted=False, hide_test=True, limit=limit, offset=0, caller=caller, db=db)["items"]])
    return out


@router.get("/api/search")
def search(request: Request, q: str = Query(min_length=2, max_length=80), kinds: str | None = Query(None, max_length=300), limit: int = Query(5, ge=1, le=20),
           caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Find records by name across the registers the caller may see. Names and titles only; the text inside files and forms is not searched."""
    term = q.strip()
    meaningful = len(term.replace("%", "").replace("_", "").strip()) >= 2  # the pattern characters alone would match everything
    available = _kinds(request, caller, db, term, limit)
    wanted = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else list(available)
    groups, failed = [], []
    for key in wanted:
        if key not in available:
            continue  # not a register, or not one this person may see: say nothing about it
        label, fn = available[key]
        try:
            items = fn() if meaningful else []
        except Exception:  # noqa: BLE001 - one register failing must not empty the others
            db.rollback()
            log.exception("search in %s failed (request %s)", key, getattr(request.state, "request_id", "-"))
            failed.append(label)
            continue
        if items:
            groups.append({"kind": key, "label": label, "items": items, "more": len(items) >= limit})
    return {"query": term, "groups": groups, "total": sum(len(g["items"]) for g in groups), "searched": [available[k][0] for k in wanted if k in available], "failed": failed,
            "note": "Searches names and titles. It does not read inside files or form answers."}
