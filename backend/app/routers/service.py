"""Service schedules, service records, what is due, and service reminders (stage 6b)."""
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import fleet, reminders, service
from ..db import get_db
from ..deps import Caller, require_permission
from ..models import Asset, AssetSchedule, AssetServiceRecord, Organisation, User, Worker, utcnow
from ..people import today_for, worker_name
from ..services import audit, iso

router = APIRouter(prefix="/api/assets", tags=["fleet"])
presets_router = APIRouter(prefix="/api/fleet", tags=["fleet"])

RecordKind = Literal["service", "repair", "inspection", "other"]
KIND_LABELS = {"service": "Service", "repair": "Repair", "inspection": "Inspection", "other": "Other"}


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    interval_days: int | None = Field(default=None, ge=1, le=3650)
    interval_km: float | None = Field(default=None, gt=0, le=1_000_000)
    interval_hours: float | None = Field(default=None, gt=0, le=100_000)
    warn_days: int = Field(default=14, ge=0, le=365)
    warn_km: float = Field(default=500, ge=0, le=100_000)
    warn_hours: float = Field(default=25, ge=0, le=10_000)
    baseline_date: dt.date | None = None
    baseline_km: float | None = Field(default=None, ge=0, le=5_000_000)
    baseline_hours: float | None = Field(default=None, ge=0, le=500_000)
    assigned_worker_id: str | None = None
    notes: str | None = Field(default=None, max_length=4000)
    active: bool = True


class ChecklistItem(BaseModel):
    item: str = Field(min_length=1, max_length=200)
    result: Literal["ok", "attention", "na"] = "ok"
    note: str | None = Field(default=None, max_length=400)


class RecordIn(BaseModel):
    kind: RecordKind = "service"
    performed_on: dt.date
    schedule_id: str | None = None
    odometer_km: float | None = None
    engine_hours: float | None = None
    description: str | None = Field(default=None, max_length=8000)
    technician: str | None = Field(default=None, max_length=160)
    cost: float | str | None = None
    checklist: list[ChecklistItem] = Field(default_factory=list, max_length=100)


class ReminderScanIn(BaseModel):
    dry_run: bool = True


def _org(db: Session, org_id: str) -> Organisation:
    return db.get(Organisation, org_id)


def _schedule(db: Session, asset: Asset, sid: str) -> AssetSchedule:
    row = db.get(AssetSchedule, sid)
    if row is None or row.asset_id != asset.id or row.deleted_at is not None:
        raise HTTPException(404, "Schedule not found")
    return row


def _record(db: Session, asset: Asset, rid: str) -> AssetServiceRecord:
    row = db.get(AssetServiceRecord, rid)
    if row is None or row.asset_id != asset.id or row.deleted_at is not None:
        raise HTTPException(404, "Service record not found")
    return row


def _schedule_values(db: Session, org_id: str, body: ScheduleIn) -> dict[str, Any]:
    data = body.model_dump()
    data["name"] = data["name"].strip()
    if not data["name"]:
        raise HTTPException(422, "Name is required")
    if not (data["interval_days"] or data["interval_km"] or data["interval_hours"]):
        raise HTTPException(422, "Set how often: every so many days, kilometres or hours")
    if data["assigned_worker_id"]:
        worker = db.get(Worker, data["assigned_worker_id"])
        if worker is None or worker.org_id != org_id or worker.deleted_at is not None:
            raise HTTPException(422, "Worker not found")
    data["notes"] = (data["notes"] or "").strip() or None
    return data


def _schedule_out(db: Session, s: AssetSchedule, asset: Asset, last: AssetServiceRecord | None, today: dt.date) -> dict[str, Any]:
    worker = db.get(Worker, s.assigned_worker_id) if s.assigned_worker_id else None
    return {"id": s.id, "name": s.name, "interval_days": s.interval_days, "interval_km": s.interval_km, "interval_hours": s.interval_hours,
            "warn_days": s.warn_days, "warn_km": s.warn_km, "warn_hours": s.warn_hours, "baseline_date": s.baseline_date.isoformat() if s.baseline_date else None,
            "baseline_km": s.baseline_km, "baseline_hours": s.baseline_hours, "assigned_worker_id": s.assigned_worker_id,
            "assigned_worker": worker_name(worker) if worker else None, "notes": s.notes, "active": s.active,
            **(service.evaluate(s, asset, last, today) if s.active else {"status": "off", "status_label": "Switched off", "parts": [], "summary": "Switched off", "last_done": None})}


def _record_out(r: AssetServiceRecord, names: dict[str, str]) -> dict[str, Any]:
    return {"id": r.id, "kind": r.kind, "kind_label": KIND_LABELS.get(r.kind, r.kind), "performed_on": r.performed_on.isoformat(), "schedule_id": r.schedule_id,
            "schedule_name": r.schedule_name, "odometer_km": r.odometer_km, "engine_hours": r.engine_hours, "description": r.description, "technician": r.technician,
            "cost": f"{r.cost:.2f}" if r.cost is not None else None, "checklist": r.checklist or [],
            "needs_attention": sum(1 for c in (r.checklist or []) if c.get("result") == "attention"),
            "recorded_by": names.get(r.created_by or ""), "created_at": iso(r.created_at)}


def _money(value: float | str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise HTTPException(422, "Cost must be a number") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal("10000000"):
        raise HTTPException(422, "Cost must be between 0 and 10,000,000")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------- across the fleet (registered before the per-asset routes) ----------

def _due(db: Session, org: Organisation) -> list[tuple[AssetSchedule, Asset, dict[str, Any]]]:
    today = today_for(org)
    pairs = db.execute(select(AssetSchedule, Asset).join(Asset, Asset.id == AssetSchedule.asset_id).where(
        AssetSchedule.org_id == org.id, AssetSchedule.deleted_at.is_(None), AssetSchedule.active.is_(True), Asset.deleted_at.is_(None), Asset.status != "archived")).all()
    last = service.last_done(db, [s for s, _ in pairs])
    out = [(s, a, service.evaluate(s, a, last.get(s.id), today)) for s, a in pairs]
    out.sort(key=lambda x: (-service.RANK[x[2]["status"]], x[1].name.lower(), x[0].name.lower()))
    return out


@router.get("/service/inbox")
def inbox(status: str | None = None, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Every active schedule that is overdue, due soon or cannot be worked out, worst first."""
    rows = [(s, a, r) for s, a, r in _due(db, _org(db, caller.org_id)) if r["status"] != "ok" and (not status or r["status"] == status)]
    workers = {w.id: worker_name(w) for w in db.scalars(select(Worker).where(Worker.id.in_({s.assigned_worker_id for s, _, _ in rows if s.assigned_worker_id}))).all()} if rows else {}
    return {"items": [{"schedule_id": s.id, "schedule": s.name, "asset_id": a.id, "asset": fleet.asset_label(a), "simulated": a.source_system == "simulator",
                       "status": r["status"], "status_label": r["status_label"], "summary": r["summary"], "assigned_worker": workers.get(s.assigned_worker_id or "")}
                      for s, a, r in rows[:500]], "total": len(rows), "can_edit": caller.can("assets", "edit")}


@router.get("/service/summary")
def summary(caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    counts = {"ok": 0, "due_soon": 0, "overdue": 0, "unknown": 0}
    for _, _, result in _due(db, _org(db, caller.org_id)):
        counts[result["status"]] += 1
    return {**counts, "schedules": sum(counts.values())}


@router.post("/service/scan-reminders")
def scan_reminders(body: ReminderScanIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Prepare one reminder email per due service to the person it is assigned to. Lists first (dry run is
    the default); asking twice does not prepare a second one for the same due point. Whether a prepared
    reminder is sent or held is decided by safe mode and the email connection."""
    org = _org(db, caller.org_id)
    results = []
    for s, a, r in _due(db, org):
        if r["status"] not in ("overdue", "due_soon"):
            continue
        worker = db.get(Worker, s.assigned_worker_id) if s.assigned_worker_id else None
        label = f"{fleet.asset_label(a)}: {s.name} ({r['summary']})"
        if worker is None:
            results.append({"label": label, "outcome": "no_one_assigned"})
            continue
        text = (f"Hello {worker.preferred_name or worker.first_name},\n\n{s.name} for {fleet.asset_label(a)} is {'overdue' if r['status'] == 'overdue' else 'due soon'}: "
                f"{r['summary']}.\n\nPlease arrange it and record the service in the portal.\n\n{org.name}")
        results.append(reminders._prepare(db, request, org=org, key=f"service-reminder:{s.id}:{service.marker(r)}:{r['status']}", recipient=worker.email,
                                          subject=f"Service {'overdue' if r['status'] == 'overdue' else 'due soon'}: {a.name}", body=text, related_type="asset_schedule",
                                          related_id=s.id, user_id=caller.user.id, dry_run=body.dry_run, label=label))
    if not body.dry_run:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="service.reminders_prepared", target_type="organisation", target_id=caller.org_id,
              detail=reminders.summarise(results, dry_run=False)["counts"])
        db.commit()
    return reminders.summarise(results, dry_run=body.dry_run)


@presets_router.get("/service-schedule-presets")
def presets(caller: Caller = Depends(require_permission("assets", "view"))) -> dict[str, Any]:
    """Starting points for a new schedule. They are common intervals, not a manufacturer's requirement."""
    return {"items": service.PRESETS, "note": "Check the manufacturer's service book for the right interval."}


# ---------- schedules ----------

@router.get("/{asset_id}/schedules")
def list_schedules(asset_id: str, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    rows = db.scalars(select(AssetSchedule).where(AssetSchedule.asset_id == asset.id, AssetSchedule.deleted_at.is_(None)).order_by(AssetSchedule.name)).all()
    last = service.last_done(db, list(rows))
    today = today_for(_org(db, caller.org_id))
    return {"items": [_schedule_out(db, s, asset, last.get(s.id), today) for s in rows], "can_edit": caller.can("assets", "edit")}


@router.post("/{asset_id}/schedules", status_code=201)
def create_schedule(asset_id: str, body: ScheduleIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    row = AssetSchedule(org_id=caller.org_id, asset_id=asset.id, created_by=caller.user.id, **_schedule_values(db, caller.org_id, body))
    db.add(row)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="service_schedule.created", target_type="asset", target_id=asset.id, detail={"name": row.name})
    db.commit()
    return _schedule_out(db, row, asset, None, today_for(_org(db, caller.org_id)))


@router.get("/{asset_id}/schedules/{sid}")
def get_schedule(asset_id: str, sid: str, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    row = _schedule(db, asset, sid)
    return _schedule_out(db, row, asset, service.last_done(db, [row]).get(row.id), today_for(_org(db, caller.org_id)))


@router.put("/{asset_id}/schedules/{sid}")
def update_schedule(asset_id: str, sid: str, body: ScheduleIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Change how often, the margins or who it is assigned to. Past service records are not touched."""
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    row = _schedule(db, asset, sid)
    for key, value in _schedule_values(db, caller.org_id, body).items():
        setattr(row, key, value)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="service_schedule.updated", target_type="asset", target_id=asset.id, detail={"name": row.name})
    db.commit()
    return _schedule_out(db, row, asset, service.last_done(db, [row]).get(row.id), today_for(_org(db, caller.org_id)))


@router.delete("/{asset_id}/schedules/{sid}", status_code=204)
def delete_schedule(asset_id: str, sid: str, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> Response:
    """Stop the schedule. Records of work already done under it stay, still showing its name."""
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    row = _schedule(db, asset, sid)
    row.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="service_schedule.deleted", target_type="asset", target_id=asset.id, detail={"name": row.name})
    db.commit()
    return Response(status_code=204)


# ---------- records ----------

def _record_values(db: Session, asset: Asset, body: RecordIn, today: dt.date) -> dict[str, Any]:
    if body.performed_on > today:
        raise HTTPException(422, "The date of the work cannot be in the future")
    schedule = _schedule(db, asset, body.schedule_id) if body.schedule_id else None
    odometer = fleet.finite(body.odometer_km, "Odometer", maximum=5_000_000)
    hours = fleet.finite(body.engine_hours, "Hour meter", maximum=500_000)
    if schedule is not None:
        if schedule.interval_km and odometer is None:
            raise HTTPException(422, f"{schedule.name} runs on kilometres, so the odometer reading at the service is needed")
        if schedule.interval_hours and hours is None:
            raise HTTPException(422, f"{schedule.name} runs on hours, so the hour-meter reading at the service is needed")
    return {"kind": body.kind, "performed_on": body.performed_on, "schedule_id": schedule.id if schedule else None, "schedule_name": schedule.name if schedule else None,
            "odometer_km": odometer, "engine_hours": hours, "description": (body.description or "").strip() or None, "technician": (body.technician or "").strip() or None,
            "cost": _money(body.cost), "checklist": [c.model_dump() for c in body.checklist]}


def _names(db: Session, rows: list[AssetServiceRecord]) -> dict[str, str]:
    ids = {r.created_by for r in rows if r.created_by}
    return {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}


@router.get("/{asset_id}/records")
def list_records(asset_id: str, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    rows = db.scalars(select(AssetServiceRecord).where(AssetServiceRecord.asset_id == asset.id, AssetServiceRecord.deleted_at.is_(None))
                      .order_by(AssetServiceRecord.performed_on.desc(), AssetServiceRecord.created_at.desc()).limit(500)).all()
    names = _names(db, list(rows))
    costs = [r.cost for r in rows if r.cost is not None]
    return {"items": [_record_out(r, names) for r in rows], "total_cost": f"{sum(costs, Decimal('0')):.2f}" if costs else None,
            "records_without_cost": sum(1 for r in rows if r.cost is None), "can_edit": caller.can("assets", "edit"), "can_delete": caller.can("assets", "delete")}


@router.post("/{asset_id}/records", status_code=201)
def create_record(asset_id: str, body: RecordIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Record work done. Linked to a schedule, it becomes that schedule's new starting point."""
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    today = today_for(_org(db, caller.org_id))
    row = AssetServiceRecord(org_id=caller.org_id, asset_id=asset.id, created_by=caller.user.id, **_record_values(db, asset, body, today))
    db.add(row)
    db.flush()
    # A reading taken at the service moves the asset's meter forward. An older record with a lower reading is history, not a regression.
    odometer = row.odometer_km if row.odometer_km is not None and (asset.odometer_km is None or row.odometer_km >= asset.odometer_km) else None
    hours = row.engine_hours if row.engine_hours is not None and (asset.engine_hours is None or row.engine_hours >= asset.engine_hours) else None
    if odometer is not None or hours is not None:
        fleet.apply_meter(db, asset, odometer_km=odometer, engine_hours=hours, source="service", reading_date=row.performed_on, recorded_by=caller.user.id, note=row.schedule_name or KIND_LABELS[row.kind])
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="service_record.created", target_type="asset", target_id=asset.id,
          detail={"kind": row.kind, "schedule": row.schedule_name, "performed_on": row.performed_on.isoformat()})
    db.commit()
    return _record_out(row, _names(db, [row]))


@router.get("/{asset_id}/records/{rid}")
def get_record(asset_id: str, rid: str, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _record(db, fleet.get_asset(db, caller.org_id, asset_id), rid)
    return _record_out(row, _names(db, [row]))


@router.put("/{asset_id}/records/{rid}")
def update_record(asset_id: str, rid: str, body: RecordIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Correct a record. The asset's current meter is not changed by a correction; use Update meter for that."""
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    row = _record(db, asset, rid)
    values = _record_values(db, asset, body, today_for(_org(db, caller.org_id)))
    if values["schedule_id"] == row.schedule_id:
        values["schedule_name"] = row.schedule_name  # keeps the name the work was done under
    changed = [k for k, v in values.items() if getattr(row, k) != v]
    for key in changed:
        setattr(row, key, values[key])
    if changed:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="service_record.updated", target_type="asset", target_id=asset.id, detail={"record_id": row.id, "fields": changed})
    db.commit()
    return _record_out(row, _names(db, [row]))


@router.delete("/{asset_id}/records/{rid}", status_code=204)
def delete_record(asset_id: str, rid: str, request: Request, caller: Caller = Depends(require_permission("assets", "delete")), db: Session = Depends(get_db)) -> Response:
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    row = _record(db, asset, rid)
    row.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="service_record.deleted", target_type="asset", target_id=asset.id,
          detail={"record_id": row.id, "performed_on": row.performed_on.isoformat(), "schedule": row.schedule_name})
    db.commit()
    return Response(status_code=204)
