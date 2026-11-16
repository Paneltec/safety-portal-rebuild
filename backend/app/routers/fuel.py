"""Fuel: imports, cards, the weekly diesel rate, reports and the anomaly inbox (stage 6c)."""
import csv
import datetime as dt
import hashlib
import io
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from .. import comms, fleet, fuel, fuel_sources, integrations
from ..db import get_db
from ..deps import Caller, require_admin, require_permission
from ..integrations import ProviderError
from ..models import Asset, FuelAnomaly, FuelBatch, FuelCard, FuelTransaction, FuelWeeklyPrice, Organisation, User, Worker, utcnow
from ..people import today_for, worker_name
from ..services import audit, iso

router = APIRouter(prefix="/api/fleet/fuel", tags=["fuel"])

ATTRIBUTION = {"direct": "Matched by registration", "card": "From the fuel card's assignment", "manual": "Matched by a person", "unlinked": "Not matched"}
PRICING = {"priced": "Priced", "pending": "Waiting for the week's rate", "not_diesel": "Not diesel: not priced", "before_weekly_pricing": "Before weekly pricing began"}
Group = Literal["vehicle", "day", "driver", "card"]
MAX_CSV_BYTES, MAX_CSV_ROWS = 5 * 1024 * 1024, 20000


def _org(db: Session, org_id: str) -> Organisation:
    return db.get(Organisation, org_id)


def _import_caller(caller: Caller = Depends(require_admin)) -> Caller:
    if not caller.can("assets", "edit"):
        raise HTTPException(403, "You do not have permission to do this")
    return caller


def _dataset(db: Session, org_id: str) -> bool:
    """True when totals should be built from simulator fills (the SmartFill connection is in simulator mode)."""
    config = integrations.get_config(db, org_id, "smartfill")
    return bool(config and config.mode == "simulator")


def _labels(db: Session, txns: list[FuelTransaction]) -> tuple[dict[str, Asset], dict[str, str]]:
    asset_ids = {t.asset_id for t in txns if t.asset_id}
    worker_ids = {t.worker_id for t in txns if t.worker_id}
    assets = {a.id: a for a in db.scalars(select(Asset).where(Asset.id.in_(asset_ids))).all()} if asset_ids else {}
    workers = {w.id: worker_name(w) for w in db.scalars(select(Worker).where(Worker.id.in_(worker_ids))).all()} if worker_ids else {}
    return assets, workers


def txn_out(t: FuelTransaction, assets: dict[str, Asset], workers: dict[str, str], weekly_start: dt.date | None) -> dict[str, Any]:
    asset = assets.get(t.asset_id or "")
    state = fuel.pricing_state(t, weekly_start)
    return {"id": t.id, "source_id": t.source_id, "simulated": t.source_system == "simulator", "occurred_at": iso(t.occurred_at), "local_date": t.local_date.isoformat(),
            "local_time": t.local_time, "litres": f"{t.litres:.3f}".rstrip("0").rstrip("."), "fuel_type": t.fuel_type, "is_diesel": t.is_diesel,
            "odometer_km": t.odometer_km, "engine_hours": t.engine_hours, "card_number": t.card_number, "driver_name": t.driver_name, "location": t.location,
            "description": t.description, "registration": t.registration, "asset_id": t.asset_id, "asset": fleet.asset_label(asset) if asset else None,
            "worker_id": t.worker_id, "worker": workers.get(t.worker_id or ""), "attribution": t.attribution, "attribution_label": ATTRIBUTION[t.attribution],
            "attribution_note": t.attribution_note, "pricing": state, "pricing_label": PRICING[state],
            "report_rate": f"{t.report_rate:.4f}" if t.report_rate is not None else None, "report_cost": f"{t.report_cost:.2f}" if t.report_cost is not None else None,
            "source_unit_price": str(t.source_unit_price) if t.source_unit_price is not None else None,
            "source_total": f"{t.source_total:.2f}" if t.source_total is not None else None}


def _get_txn(db: Session, org_id: str, txn_id: str) -> FuelTransaction:
    txn = db.get(FuelTransaction, txn_id)
    if txn is None or txn.org_id != org_id:
        raise HTTPException(404, "Fill not found")
    return txn


def _period(date_from: dt.date | None, date_to: dt.date | None, today: dt.date) -> tuple[dt.date, dt.date]:
    end = date_to or today
    start = date_from or (end - dt.timedelta(days=27))
    if start > end:
        raise HTTPException(422, "The start date is after the end date")
    if (end - start).days > 400:
        raise HTTPException(422, "Choose a period of 400 days or less")
    return start, end


# ---------- importing ----------

def _batch_out(b: FuelBatch, names: dict[str, str] | None = None) -> dict[str, Any]:
    return {"id": b.id, "via": b.via, "via_label": {"csv": "Spreadsheet file", "api": "SmartFill", "simulator": "Simulator (not real)"}.get(b.via, b.via), "filename": b.filename,
            "timezone": b.timezone, "dry_run": b.dry_run, "status": b.status, "counts": b.counts or {}, "errors": b.errors or [], "warnings": b.warnings or [],
            "created_by": (names or {}).get(b.created_by or ""), "created_at": iso(b.created_at)}


@router.post("/import-csv")
def import_csv(
    request: Request, file: UploadFile = File(...), timezone: str = Form(""), dry_run: bool = Form(True),
    caller: Caller = Depends(_import_caller), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Read a fuel export. Preview by default: nothing is saved until it is run with dry_run=false.

    `timezone` says how times in the file are to be read (they carry no offset). It is required to
    be a real zone name and is recorded with the import; it defaults to the organisation's own.
    """
    org = _org(db, caller.org_id)
    zone = timezone.strip() or org.timezone or "Australia/Hobart"
    try:
        ZoneInfo(zone)
    except Exception:
        raise HTTPException(422, "Unknown timezone. Use a name like Australia/Hobart") from None
    data = file.file.read(MAX_CSV_BYTES + 1)
    if len(data) > MAX_CSV_BYTES:
        raise HTTPException(413, "The file is larger than 5 MB. Export a shorter period.")
    headings, records = fuel.read_csv(data)
    mapping, unknown = fuel.map_columns(headings)
    missing = [label for key, label in (("litres", "Litres"),) if key not in mapping]
    if "datetime" not in mapping and not ("date" in mapping and "time" in mapping):
        missing.append("Date and Time")
    if missing:
        raise HTTPException(422, "This file cannot be used: it has no column for " + " or ".join(missing) + ". Columns found: " + (", ".join(headings) or "none") + ".")
    if len(records) > MAX_CSV_ROWS:
        raise HTTPException(413, f"The file has more than {MAX_CSV_ROWS:,} rows. Export a shorter period.")
    warnings = []
    if "transaction_id" not in mapping:
        warnings.append("The file has no transaction number column, so each fill is identified by its time, card, litres and site. Importing a corrected copy of the same fill may add it twice.")
    if unknown:
        warnings.append("Columns not used: " + ", ".join(unknown[:12]) + ".")
    batch = FuelBatch(org_id=caller.org_id, via="csv", filename=(file.filename or "upload.csv")[:300], sha256=hashlib.sha256(data).hexdigest(), timezone=zone, dry_run=dry_run,
                      status="previewed" if dry_run else "committed", created_by=caller.user.id, warnings=warnings)
    db.add(batch)
    db.flush()
    rows = [(i, {canonical: record.get(heading, "") for canonical, heading in mapping.items()}) for i, record in enumerate(records, start=2)]
    fuel.import_rows(db, org, rows, source_system="smartfill", batch=batch, dry_run=dry_run)
    if not dry_run:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="fuel.imported", target_type="fuel_batch", target_id=batch.id,
              detail={"via": "csv", "filename": batch.filename, "counts": batch.counts})
    db.commit()
    return {**_batch_out(batch), "columns": {k: v for k, v in mapping.items()}}


def _smartfill_state(db: Session, org_id: str) -> tuple[Any, bool, str]:
    config = integrations.get_config(db, org_id, "smartfill")
    if config is None or config.mode == "off":
        return config, False, "SmartFill is not set up. An admin can set it up under Settings, Connections. Fuel can still be imported from a spreadsheet file."
    if config.mode == "simulator":
        return config, True, "The SmartFill simulator is on. Fills it provides are invented and labelled as simulator."
    missing = integrations.missing_required(integrations.CATALOGUE["smartfill"], config)
    if missing:
        return config, False, "SmartFill details are missing: " + ", ".join(missing) + "."
    if config.last_test_ok is not True or config.last_test_mode != "live":
        return config, False, "Test the SmartFill connection successfully under Settings, Connections, before importing."
    return config, True, "SmartFill is connected."


class SmartfillIn(BaseModel):
    dry_run: bool = True
    days: int = Field(default=7, ge=1, le=62)


@router.get("/smartfill-status")
def smartfill_status(caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    config, ready, message = _smartfill_state(db, caller.org_id)
    last = db.scalars(select(FuelBatch).where(FuelBatch.org_id == caller.org_id, FuelBatch.via.in_(("api", "simulator")), FuelBatch.status == "committed")
                      .order_by(FuelBatch.created_at.desc())).first()
    return {"ready": ready, "message": message, "simulated": bool(config and config.mode == "simulator"), "last_synced_at": iso(last.created_at) if last else None,
            "last_counts": last.counts if last else None, "calls_left": fuel_sources.calls_left(db, caller.org_id),
            "schedule": "Manual only. Nothing imports by itself.", "can_sync": caller.is_admin and caller.can("assets", "edit")}


@router.post("/sync-smartfill")
def sync_smartfill(body: SmartfillIn, request: Request, caller: Caller = Depends(_import_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Preview (the default) or import the last `days` days of fills from SmartFill."""
    config, ready, message = _smartfill_state(db, caller.org_id)
    if not ready:
        raise HTTPException(409, message)
    org = _org(db, caller.org_id)
    simulated = config.mode == "simulator"
    batch = FuelBatch(org_id=caller.org_id, via="simulator" if simulated else "api", timezone=org.timezone or "Australia/Hobart", dry_run=body.dry_run, status="failed", created_by=caller.user.id)
    db.add(batch)
    db.flush()
    now = utcnow()
    try:
        records, warnings = fuel_sources.smartfill_fetch(db, caller.org_id, config, now - dt.timedelta(days=body.days), now, today_for(org))
    except ProviderError as exc:
        batch.errors = [{"row": None, "error": str(exc)}]
        db.commit()  # keeps the calls counted against the limit
        return {**_batch_out(batch), "error": str(exc)}
    batch.warnings = warnings
    fuel.import_rows(db, org, list(enumerate(records, start=1)), source_system="simulator" if simulated else "smartfill", batch=batch, dry_run=body.dry_run)
    batch.status = "previewed" if body.dry_run else "committed"
    if not body.dry_run:
        config.last_sync_at = now
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="fuel.imported", target_type="fuel_batch", target_id=batch.id,
              detail={"via": batch.via, "days": body.days, "counts": batch.counts})
    db.commit()
    return {**_batch_out(batch), "error": None}


@router.get("/batches")
def batches(caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(FuelBatch).where(FuelBatch.org_id == caller.org_id, FuelBatch.status != "previewed").order_by(FuelBatch.created_at.desc()).limit(50)).all()
    ids = {b.created_by for b in rows if b.created_by}
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}
    return {"items": [_batch_out(b, names) for b in rows]}


@router.get("/batches/{batch_id}")
def batch_detail(batch_id: str, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    batch = db.get(FuelBatch, batch_id)
    if batch is None or batch.org_id != caller.org_id:
        raise HTTPException(404, "Import not found")
    return _batch_out(batch)


# ---------- fills ----------

@router.get("/transactions")
def transactions(
    date_from: dt.date | None = None, date_to: dt.date | None = None, asset_id: str | None = None, card_number: str | None = None, worker_id: str | None = None,
    attribution: str | None = None, pricing: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    org = _org(db, caller.org_id)
    weekly_start = fuel.settings(org)["weekly_start"]
    query = select(FuelTransaction).where(FuelTransaction.org_id == caller.org_id)
    if date_from:
        query = query.where(FuelTransaction.local_date >= date_from)
    if date_to:
        query = query.where(FuelTransaction.local_date <= date_to)
    if asset_id:
        query = query.where(FuelTransaction.asset_id.is_(None) if asset_id == "none" else FuelTransaction.asset_id == asset_id)
    if card_number:
        query = query.where(FuelTransaction.card_number == card_number)
    if worker_id:
        query = query.where(FuelTransaction.worker_id == worker_id)
    if attribution:
        query = query.where(FuelTransaction.attribution == attribution)
    if pricing == "pending":
        query = query.where(FuelTransaction.is_diesel.is_(True), FuelTransaction.report_rate.is_(None))
        if weekly_start:
            query = query.where(FuelTransaction.local_date >= weekly_start)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(FuelTransaction.occurred_at.desc(), FuelTransaction.id).offset((page - 1) * page_size).limit(page_size)).all()
    assets, workers = _labels(db, list(rows))
    return {"items": [txn_out(t, assets, workers, weekly_start) for t in rows], "total": total, "page": page, "page_size": page_size, "can_edit": caller.can("assets", "edit")}


def _reasons(rows: list[FuelAnomaly], names: dict[str, str]) -> list[dict[str, Any]]:
    rows = sorted(rows, key=lambda a: (fuel.SEVERITY_RANK[a.severity], a.rule))
    return [{"rule": a.rule, "label": fuel.RULES[a.rule][1], "severity": a.severity, "status": a.status, "evidence": [e.get("text") for e in (a.evidence or []) if e.get("text")],
             "note": a.note, "acted_by": names.get(a.acted_by or "") or ("the portal" if a.status != "open" and a.acted_at and not a.acted_by else None), "acted_at": iso(a.acted_at)} for a in rows]


@router.get("/transactions/{txn_id}")
def transaction(txn_id: str, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    txn = _get_txn(db, caller.org_id, txn_id)
    assets, workers = _labels(db, [txn])
    flags = db.scalars(select(FuelAnomaly).where(FuelAnomaly.transaction_id == txn.id)).all()
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_({a.acted_by for a in flags if a.acted_by} | ({txn.attributed_by} if txn.attributed_by else set())))).all()}
    price = db.get(FuelWeeklyPrice, txn.weekly_price_id) if txn.weekly_price_id else None
    return {**txn_out(txn, assets, workers, fuel.settings(_org(db, caller.org_id))["weekly_start"]), "reasons": _reasons(list(flags), names),
            "attributed_by": names.get(txn.attributed_by or ""), "attributed_at": iso(txn.attributed_at),
            "week": {"week_start": price.week_start.isoformat(), "rate": f"{price.rate:.4f}", "source": price.source} if price else None, "original": txn.raw if caller.can("assets", "edit") else None}


class MatchIn(BaseModel):
    asset_id: str | None = None
    worker_id: str | None = None
    reason: str = Field(min_length=3, max_length=400)


def _attribute(db: Session, request: Request, caller: Caller, txn: FuelTransaction, asset: Asset | None, worker: Worker | None, reason: str) -> bool:
    """Set who and what a fill belongs to by hand. Litres and prices are never touched. Returns False when nothing changed."""
    new_asset, new_worker = (asset.id if asset else None), (worker.id if worker else None)
    if txn.attribution == "manual" and txn.asset_id == new_asset and txn.worker_id == new_worker:
        return False
    before = {"asset_id": txn.asset_id, "worker_id": txn.worker_id, "attribution": txn.attribution}
    txn.asset_id, txn.worker_id, txn.attribution = new_asset, new_worker, "manual" if (new_asset or new_worker) else "unlinked"
    txn.attribution_note, txn.attributed_by, txn.attributed_at = reason, caller.user.id, utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="fuel.attributed", target_type="fuel_transaction", target_id=txn.id,
          detail={"before": before, "after": {"asset_id": new_asset, "worker_id": new_worker}, "reason": reason})
    return True


def _refs(db: Session, org_id: str, asset_id: str | None, worker_id: str | None) -> tuple[Asset | None, Worker | None]:
    asset = fleet.get_asset(db, org_id, asset_id) if asset_id else None
    worker = None
    if worker_id:
        worker = db.get(Worker, worker_id)
        if worker is None or worker.org_id != org_id or worker.deleted_at is not None:
            raise HTTPException(404, "Worker not found")
    return asset, worker


@router.post("/transactions/{txn_id}/match")
def match(txn_id: str, body: MatchIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    txn = _get_txn(db, caller.org_id, txn_id)
    asset, worker = _refs(db, caller.org_id, body.asset_id, body.worker_id)
    _attribute(db, request, caller, txn, asset, worker, body.reason.strip())
    db.flush()
    fuel.redetect(db, [txn])
    db.commit()
    return transaction(txn_id, caller, db)


# ---------- cards ----------

class CardIn(BaseModel):
    asset_id: str | None = None
    worker_id: str | None = None
    notes: str | None = Field(default=None, max_length=1000)
    apply_to_unmatched: bool = False


def _card(db: Session, org_id: str, number: str) -> FuelCard:
    card = db.scalars(select(FuelCard).where(FuelCard.org_id == org_id, FuelCard.card_number == number)).first()
    if card is None:
        raise HTTPException(404, "Card not found")
    return card


@router.get("/cards")
def cards(caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(FuelCard).where(FuelCard.org_id == caller.org_id).order_by(FuelCard.card_number)).all()
    stats = {n: (c, last, unmatched) for n, c, last, unmatched in db.execute(
        select(FuelTransaction.card_number, func.count(), func.max(FuelTransaction.occurred_at), func.count().filter(FuelTransaction.attribution == "unlinked"))
        .where(FuelTransaction.org_id == caller.org_id, FuelTransaction.card_number.is_not(None)).group_by(FuelTransaction.card_number)).all()}
    assets = {a.id: a for a in db.scalars(select(Asset).where(Asset.id.in_({c.asset_id for c in rows if c.asset_id}))).all()} if rows else {}
    workers = {w.id: worker_name(w) for w in db.scalars(select(Worker).where(Worker.id.in_({c.worker_id for c in rows if c.worker_id}))).all()} if rows else {}
    return {"items": [{"card_number": c.card_number, "label": c.label, "simulated": c.simulated, "asset_id": c.asset_id, "asset": fleet.asset_label(assets[c.asset_id]) if c.asset_id in assets else None,
                       "worker_id": c.worker_id, "worker": workers.get(c.worker_id or ""), "notes": c.notes, "fills": stats.get(c.card_number, (0, None, 0))[0],
                       "last_used_at": iso(stats.get(c.card_number, (0, None, 0))[1]), "unmatched_fills": stats.get(c.card_number, (0, None, 0))[2]} for c in rows],
            "can_edit": caller.can("assets", "edit")}


@router.patch("/cards/{card_number}")
def assign_card(card_number: str, body: CardIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Say which vehicle and/or person a card belongs to. Fills already imported are only re-matched
    when `apply_to_unmatched` is set, and then only the ones with no match yet; a match made by a
    person or by registration is left alone."""
    card = _card(db, caller.org_id, card_number)
    asset, worker = _refs(db, caller.org_id, body.asset_id, body.worker_id)
    before = {"asset_id": card.asset_id, "worker_id": card.worker_id}
    card.asset_id, card.worker_id, card.notes = (asset.id if asset else None), (worker.id if worker else None), (body.notes or "").strip() or None
    card.assigned_by, card.assigned_at = caller.user.id, utcnow()
    changed = 0
    if body.apply_to_unmatched and (card.asset_id or card.worker_id):
        waiting = db.scalars(select(FuelTransaction).where(FuelTransaction.org_id == caller.org_id, FuelTransaction.card_number == card.card_number,
                                                           FuelTransaction.attribution == "unlinked")).all()
        for txn in waiting:
            txn.asset_id, txn.worker_id, txn.attribution = card.asset_id, card.worker_id, "card"
        db.flush()
        fuel.redetect(db, list(waiting))
        changed = len(waiting)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="fuel.card_assigned", target_type="fuel_card", target_id=card.id,
          detail={"card_number": card.card_number, "before": before, "after": {"asset_id": card.asset_id, "worker_id": card.worker_id}, "fills_matched": changed})
    db.commit()
    return {"card_number": card.card_number, "asset_id": card.asset_id, "worker_id": card.worker_id, "fills_matched": changed}


# ---------- the weekly diesel rate ----------

def _price_out(p: FuelWeeklyPrice, names: dict[str, str] | None = None) -> dict[str, Any]:
    return {"id": p.id, "week_start": p.week_start.isoformat(), "week_end": p.week_end.isoformat(), "source_week_start": p.source_week_start.isoformat(),
            "source_week_end": p.source_week_end.isoformat(), "daily_cents": p.daily_cents, "rate": f"{p.rate:.4f}", "fuel_code": p.fuel_code, "timezone": p.timezone,
            "source": p.source, "source_label": {"fuelcheck": "FuelCheck Tasmania", "manual": "Entered by hand", "simulator": "Simulator (not real)"}.get(p.source, p.source),
            "source_url": p.source_url, "source_note": p.source_note, "simulated": p.simulated, "locked_at": iso(p.locked_at), "locked_by": (names or {}).get(p.locked_by or "")}


def _pending_weeks(db: Session, org: Organisation, simulated: bool) -> list[dict[str, Any]]:
    weekly_start = fuel.settings(org)["weekly_start"]
    query = select(FuelTransaction.local_date, FuelTransaction.litres).where(
        FuelTransaction.org_id == org.id, FuelTransaction.is_diesel.is_(True), FuelTransaction.report_rate.is_(None),
        (FuelTransaction.source_system == "simulator") if simulated else (FuelTransaction.source_system != "simulator"))
    if weekly_start:
        query = query.where(FuelTransaction.local_date >= weekly_start)
    weeks: dict[dt.date, dict[str, Any]] = {}
    for day, litres in db.execute(query).all():
        entry = weeks.setdefault(fuel.monday(day), {"fills": 0, "litres": Decimal("0")})
        entry["fills"] += 1
        entry["litres"] += litres
    return [{"week_start": w.isoformat(), "week_end": (w + dt.timedelta(days=6)).isoformat(), "fills": v["fills"], "litres": f"{v['litres']:.1f}",
             "source_week_start": fuel.source_week(w)[0].isoformat(), "source_week_end": fuel.source_week(w)[6].isoformat()} for w, v in sorted(weeks.items(), reverse=True)]


@router.get("/weekly-diesel")
def weekly_diesel(caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """This week's rate (or that it is waiting), the weeks with fills still waiting, and every rate fixed so far."""
    org = _org(db, caller.org_id)
    simulated = _dataset(db, org.id)
    today = today_for(org)
    this_week = fuel.monday(today)
    rows = db.scalars(select(FuelWeeklyPrice).where(FuelWeeklyPrice.org_id == org.id, FuelWeeklyPrice.simulated == simulated).order_by(FuelWeeklyPrice.week_start.desc()).limit(104)).all()
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_({p.locked_by for p in rows if p.locked_by}))).all()} if rows else {}
    current = next((p for p in rows if p.week_start == this_week), None)
    fuelcheck = integrations.get_config(db, org.id, "fuelcheck")
    return {"today": today.isoformat(), "this_week": {"week_start": this_week.isoformat(), "week_end": (this_week + dt.timedelta(days=6)).isoformat(),
                                                      "source_week_start": fuel.source_week(this_week)[0].isoformat(), "source_week_end": fuel.source_week(this_week)[6].isoformat(),
                                                      "price": _price_out(current, names) if current else None},
            "pending_weeks": _pending_weeks(db, org, simulated), "ledger": [_price_out(p, names) for p in rows], "simulated": simulated,
            "weekly_start": fuel.settings(org)["weekly_start"].isoformat() if fuel.settings(org)["weekly_start"] else None,
            "fuelcheck_mode": fuelcheck.mode if fuelcheck else "off", "source_page": fuel.FUELCHECK_PAGE,
            "rule": "Each week's rate is the average of the seven daily diesel prices of the week before, fixed once and never changed.",
            "can_refresh": caller.can("assets", "edit"), "can_enter": caller.is_admin}


def _lock_candidates(db: Session, org: Organisation, simulated: bool, today: dt.date) -> list[dt.date]:
    """This week, plus earlier weeks that have fills waiting. A week is only fixed once it has begun."""
    weeks = {fuel.monday(today)} | {dt.date.fromisoformat(w["week_start"]) for w in _pending_weeks(db, org, simulated)}
    return sorted(w for w in weeks if w <= today)


@router.post("/weekly-diesel/refresh")
def refresh_weekly(request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Read the daily diesel prices and fix the rate for this week and for any earlier week with fills
    waiting, where all seven source days are available. A week that already has a rate is not touched."""
    org = _org(db, caller.org_id)
    today = today_for(org)
    config = integrations.get_config(db, org.id, "fuelcheck")
    try:
        observations, warnings = fuel_sources.fuelcheck_fetch(config, today)
    except ProviderError as exc:
        return {"ok": False, "error": str(exc), "results": [], "warnings": [], "priced": 0}
    simulated = bool(config and config.mode == "simulator")
    results, priced = [], 0
    for week in _lock_candidates(db, org, simulated, today):
        row, outcome = fuel.lock_week(db, org, week, observations, source="simulator" if simulated else "fuelcheck", source_url=fuel.FUELCHECK_URL, source_note=None,
                                      fetched_at=utcnow(), actor_id=caller.user.id)
        entry = {"week_start": week.isoformat(), "outcome": outcome, "rate": f"{row.rate:.4f}" if row else None}
        if outcome == "missing_days":
            entry["missing"] = [d.isoformat() for d in fuel.source_week(week) if d not in observations]
        if outcome == "locked":
            audit(db, request, org_id=org.id, actor_id=caller.user.id, action="fuel.week_locked", target_type="fuel_weekly_price", target_id=row.id,
                  detail={"week_start": week.isoformat(), "rate": str(row.rate), "source": row.source})
        if row is not None:
            priced += fuel.price_pending(db, org, week_start=week)
        results.append(entry)
    db.commit()
    return {"ok": True, "error": None, "results": results, "warnings": warnings, "priced": priced, "simulated": simulated}


class DayPrice(BaseModel):
    date: dt.date
    cents: float = Field(gt=50, lt=500)


class ManualWeekIn(BaseModel):
    week_start: dt.date
    days: list[DayPrice] = Field(min_length=7, max_length=7)
    note: str = Field(min_length=5, max_length=400)


@router.put("/weekly-diesel")
def enter_week(body: ManualWeekIn, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Admin only: fix a week's rate from seven daily prices typed in from the FuelCheck page, for when
    the price list cannot be read automatically. The same rule applies: all seven days, written once.
    A week that already has a rate cannot be entered again."""
    org = _org(db, caller.org_id)
    today = today_for(org)
    if body.week_start.weekday() != 0:
        raise HTTPException(422, "The week must start on a Monday")
    if body.week_start > today:
        raise HTTPException(422, "A week's rate can only be fixed once the week has begun")
    wanted = fuel.source_week(body.week_start)
    if sorted(d.date for d in body.days) != wanted:
        raise HTTPException(422, f"Enter one price for each day from {wanted[0].strftime('%d/%m/%Y')} to {wanted[6].strftime('%d/%m/%Y')}")
    if fuel.get_price(db, org.id, body.week_start, False) is not None:
        raise HTTPException(409, "This week already has a rate. A fixed rate cannot be changed.")
    row, outcome = fuel.lock_week(db, org, body.week_start, {d.date: Decimal(str(d.cents)) for d in body.days}, source="manual", source_url=fuel.FUELCHECK_PAGE,
                                  source_note=body.note.strip(), fetched_at=None, actor_id=caller.user.id)
    if outcome != "locked":
        raise HTTPException(409, "This week already has a rate. A fixed rate cannot be changed.")
    priced = fuel.price_pending(db, org, week_start=body.week_start)
    audit(db, request, org_id=org.id, actor_id=caller.user.id, action="fuel.week_locked", target_type="fuel_weekly_price", target_id=row.id,
          detail={"week_start": body.week_start.isoformat(), "rate": str(row.rate), "source": "manual", "note": body.note.strip()})
    db.commit()
    return {**_price_out(row), "priced": priced}


class PriceSettingsIn(BaseModel):
    weekly_start: dt.date | None = None


@router.get("/price-settings")
def price_settings(caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    start = fuel.settings(_org(db, caller.org_id))["weekly_start"]
    return {"mode": "weekly_diesel", "weekly_start": start.isoformat() if start else None, "currency": "AUD",
            "note": "Fills dated before the start date are not given a reporting cost. With no start date, every diesel fill uses its week's rate."}


@router.put("/price-settings")
def set_price_settings(body: PriceSettingsIn, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Set the date weekly pricing starts from. Costs already written on fills are never removed or recalculated."""
    org = _org(db, caller.org_id)
    if body.weekly_start and body.weekly_start.weekday() != 0:
        raise HTTPException(422, "The start date must be a Monday")
    before = fuel.settings(org)["weekly_start"]
    org.details = {**(org.details or {}), "fuel": {"weekly_start": body.weekly_start.isoformat() if body.weekly_start else None}}
    audit(db, request, org_id=org.id, actor_id=caller.user.id, action="fuel.price_settings_changed", target_type="organisation", target_id=org.id,
          detail={"before": before.isoformat() if before else None, "after": body.weekly_start.isoformat() if body.weekly_start else None})
    db.flush()
    priced = fuel.price_pending(db, org)
    db.commit()
    return {**price_settings(caller, db), "priced": priced}


# ---------- reports ----------

def _report(db: Session, org: Organisation, group: str, start: dt.date, end: dt.date, simulated: bool) -> dict[str, Any]:
    weekly_start = fuel.settings(org)["weekly_start"]
    T = FuelTransaction
    waiting = and_(T.is_diesel.is_(True), T.report_rate.is_(None), (T.local_date >= weekly_start) if weekly_start else True)
    key = {"vehicle": T.asset_id, "day": T.local_date, "driver": T.worker_id, "card": T.card_number}[group]
    diesel = case((T.is_diesel.is_(True), T.litres), else_=0)
    rows = db.execute(select(
        key, func.count(), func.sum(diesel), func.sum(T.report_cost), func.sum(case((waiting, T.litres), else_=0)), func.count().filter(waiting),
        func.sum(case((T.is_diesel.is_(False), T.litres), else_=0)), func.sum(T.source_total), func.count(T.source_total), func.max(T.local_date),
        func.count().filter(T.is_diesel.is_(True), T.report_rate.is_(None), ~waiting)).where(
        T.org_id == org.id, T.local_date >= start, T.local_date <= end, (T.source_system == "simulator") if simulated else (T.source_system != "simulator")).group_by(key)).all()
    labels: dict[Any, str] = {}
    keys = [r[0] for r in rows if r[0] is not None]
    if group == "vehicle" and keys:
        labels = {a.id: fleet.asset_label(a) for a in db.scalars(select(Asset).where(Asset.id.in_(keys))).all()}
    elif group == "driver" and keys:
        labels = {w.id: worker_name(w) for w in db.scalars(select(Worker).where(Worker.id.in_(keys))).all()}
    none_label = {"vehicle": "Not matched to a vehicle", "driver": "No driver recorded", "card": "No card recorded", "day": ""}[group]
    items = []
    total = {"fills": 0, "litres": Decimal("0"), "cost": Decimal("0"), "unpriced_litres": Decimal("0"), "unpriced_fills": 0, "other_fuel_litres": Decimal("0"),
             "supplier_total": Decimal("0"), "supplier_fills": 0, "before_weekly_fills": 0}
    for k, fills, litres, cost, unpriced_l, unpriced_n, other_l, supplier, supplier_n, last, before_n in rows:
        litres, cost, unpriced_l, other_l, supplier = litres or Decimal("0"), cost, unpriced_l or Decimal("0"), other_l or Decimal("0"), supplier
        label = k.strftime("%a %d/%m/%Y") if group == "day" and k else labels.get(k, str(k)) if k is not None else none_label
        items.append({"key": k.isoformat() if isinstance(k, dt.date) else k, "label": label, "fills": fills, "litres": f"{litres:.1f}", "cost": f"{cost:.2f}" if cost is not None else None,
                      "unpriced_litres": f"{unpriced_l:.1f}", "unpriced_fills": unpriced_n, "complete": unpriced_n == 0, "other_fuel_litres": f"{other_l:.1f}",
                      "supplier_total": f"{supplier:.2f}" if supplier is not None else None, "last_fill": last.isoformat() if last else None, "_sort": (k is None, str(k) if group == "day" else label.lower())})
        total["fills"] += fills
        total["litres"] += litres
        total["cost"] += cost or Decimal("0")
        total["unpriced_litres"] += unpriced_l
        total["unpriced_fills"] += unpriced_n
        total["other_fuel_litres"] += other_l
        total["supplier_total"] += supplier or Decimal("0")
        total["supplier_fills"] += supplier_n
        total["before_weekly_fills"] += before_n
    items.sort(key=lambda r: r["_sort"], reverse=group == "day")
    for item in items:
        del item["_sort"]
    priced_litres = total["litres"] - total["unpriced_litres"]
    notes = []
    if total["unpriced_fills"]:
        notes.append(f"{total['unpriced_fills']} fill{'s' if total['unpriced_fills'] != 1 else ''} ({total['unpriced_litres']:.1f} litres) {'are' if total['unpriced_fills'] != 1 else 'is'} waiting for a weekly rate, so the cost shown is not complete.")
    if total["other_fuel_litres"]:
        notes.append(f"{total['other_fuel_litres']:.1f} litres of fuel that is not diesel are listed but not priced.")
    if total["before_weekly_fills"]:
        notes.append(f"{total['before_weekly_fills']} fill{'s' if total['before_weekly_fills'] != 1 else ''} dated before weekly pricing began {'have' if total['before_weekly_fills'] != 1 else 'has'} no reporting cost.")
    return {"group": group, "date_from": start.isoformat(), "date_to": end.isoformat(), "simulated": simulated, "items": items,
            "group_meaning": {"vehicle": "Each vehicle's fills in the period", "day": "Fills on each day (the date of the fill, in local time)", "driver": "Fills by the person the card or fill is assigned to", "card": "Fills by fuel card"}[group],
            "totals": {"fills": total["fills"], "litres": f"{total['litres']:.1f}", "cost": f"{total['cost']:.2f}", "complete": total["unpriced_fills"] == 0,
                       "unpriced_litres": f"{total['unpriced_litres']:.1f}", "unpriced_fills": total["unpriced_fills"], "other_fuel_litres": f"{total['other_fuel_litres']:.1f}",
                       "average_rate": f"{(total['cost'] / priced_litres):.4f}" if priced_litres > 0 and total["cost"] > 0 else None,
                       "supplier_total": f"{total['supplier_total']:.2f}" if total["supplier_fills"] else None, "supplier_fills": total["supplier_fills"]},
            "notes": notes, "cost_meaning": "Cost uses the weekly diesel rate. It is a reporting figure, not what the supplier invoiced."}


@router.get("/reports")
def reports(group: Group = "vehicle", date_from: dt.date | None = None, date_to: dt.date | None = None,
            caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    org = _org(db, caller.org_id)
    start, end = _period(date_from, date_to, today_for(org))
    return {**_report(db, org, group, start, end, _dataset(db, org.id)), "can_email": caller.can("assets", "edit")}


def _safe_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text  # spreadsheets must not run a cell as a formula


@router.get("/reports/export")
def reports_export(group: Group = "vehicle", date_from: dt.date | None = None, date_to: dt.date | None = None,
                   caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> Response:
    org = _org(db, caller.org_id)
    start, end = _period(date_from, date_to, today_for(org))
    report = _report(db, org, group, start, end, _dataset(db, org.id))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([{"vehicle": "Vehicle", "day": "Day", "driver": "Driver", "card": "Card"}[group], "Fills", "Diesel litres", "Reporting cost (AUD)", "Litres waiting for a rate", "Fills waiting for a rate",
                     "Other fuel litres", "Supplier amount where given (AUD)", "Last fill"])
    for r in report["items"]:
        writer.writerow([_safe_cell(r["label"]), r["fills"], r["litres"], r["cost"] or "", r["unpriced_litres"], r["unpriced_fills"], r["other_fuel_litres"], r["supplier_total"] or "", r["last_fill"] or ""])
    t = report["totals"]
    writer.writerow(["Total" + ("" if t["complete"] else " (cost not complete)"), t["fills"], t["litres"], t["cost"], t["unpriced_litres"], t["unpriced_fills"], t["other_fuel_litres"], t["supplier_total"] or "", ""])
    for note in [report["cost_meaning"], *report["notes"]] + (["SIMULATOR DATA: these fills are invented."] if report["simulated"] else []):
        writer.writerow([note])
    return Response(out.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="fuel-by-{group}-{start.isoformat()}-to-{end.isoformat()}.csv"', "Cache-Control": "no-store"})


class EmailReportIn(BaseModel):
    to: list[EmailStr] = Field(min_length=1, max_length=10)
    group: Group = "vehicle"
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    note: str | None = Field(default=None, max_length=1000)


@router.post("/reports/email")
def email_report(body: EmailReportIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Send this report as an email to the people named, now, because someone asked. It goes through the
    same gate as every other message, so with safe mode on it is held in the outbox and not sent."""
    org = _org(db, caller.org_id)
    start, end = _period(body.date_from, body.date_to, today_for(org))
    report = _report(db, org, body.group, start, end, _dataset(db, org.id))
    t = report["totals"]
    lines = [f"Fuel report for {org.name}", f"{start.strftime('%d/%m/%Y')} to {end.strftime('%d/%m/%Y')}, {report['group_meaning'].lower()}", ""]
    if report["simulated"]:
        lines += ["SIMULATOR DATA: these fills are invented.", ""]
    if body.note and body.note.strip():
        lines += [body.note.strip(), ""]
    for r in report["items"][:200]:
        cost = f"${r['cost']}" if r["cost"] is not None else "no cost yet"
        lines.append(f"{r['label']}: {r['fills']} fill{'s' if r['fills'] != 1 else ''}, {r['litres']} L, {cost}" + ("" if r["complete"] else f" ({r['unpriced_litres']} L waiting for a rate)"))
    lines += ["", f"Total: {t['fills']} fills, {t['litres']} L diesel, ${t['cost']}" + ("" if t["complete"] else " (not complete)"), "", report["cost_meaning"], *report["notes"],
              "", f"Sent by {caller.user.name}"]
    messages = [comms.deliver(db, request, org_id=org.id, channel="email", purpose="fuel_report", recipient=str(address).lower(),
                              subject=f"Fuel report {start.strftime('%d/%m/%Y')} to {end.strftime('%d/%m/%Y')}", body="\n".join(lines), store_body=True,
                              related_type="fuel_report", related_id=None, created_by=caller.user.id) for address in dict.fromkeys(str(a).lower() for a in body.to)]
    audit(db, request, org_id=org.id, actor_id=caller.user.id, action="fuel.report_emailed", target_type="organisation", target_id=org.id,
          detail={"recipients": len(messages), "group": body.group, "date_from": start.isoformat(), "date_to": end.isoformat(), "statuses": [m.status for m in messages]})
    db.commit()
    return {"messages": [{"recipient": m.recipient, "status": m.status, "note": comms.note_for(m)} for m in messages]}


@router.get("/stats")
def stats(caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    org = _org(db, caller.org_id)
    simulated = _dataset(db, org.id)
    today = today_for(org)
    week = fuel.monday(today)
    this_week = _report(db, org, "day", week, week + dt.timedelta(days=6), simulated)["totals"]
    price = fuel.get_price(db, org.id, week, simulated)
    pending = _pending_weeks(db, org, simulated)
    return {"simulated": simulated, "this_week": this_week, "rate": f"{price.rate:.4f}" if price else None, "rate_pending": price is None,
            "fills_waiting_for_rate": sum(w["fills"] for w in pending), "open_anomalies": fuel.open_flag_count(db, org.id)}


# ---------- anomaly inbox ----------

class ActIn(BaseModel):
    note: str | None = Field(default=None, max_length=600)
    rules: list[str] | None = None


class BulkIn(ActIn):
    txn_ids: list[str] = Field(min_length=1, max_length=500)


class BulkAttributeIn(BaseModel):
    txn_ids: list[str] = Field(min_length=1, max_length=500)
    asset_id: str
    reason: str = Field(min_length=3, max_length=400)


def _flag_query(caller: Caller, status: str, rule: str | None, asset_id: str | None, date_from: dt.date | None, date_to: dt.date | None):
    """Ids of fills having at least one flag that fits the filters."""
    query = select(FuelTransaction.id).join(FuelAnomaly, FuelAnomaly.transaction_id == FuelTransaction.id).where(FuelTransaction.org_id == caller.org_id)
    if status != "all":
        query = query.where(FuelAnomaly.status == status)
    if rule:
        query = query.where(FuelAnomaly.rule == rule)
    if asset_id:
        query = query.where(FuelTransaction.asset_id.is_(None) if asset_id == "none" else FuelTransaction.asset_id == asset_id)
    if date_from:
        query = query.where(FuelTransaction.local_date >= date_from)
    if date_to:
        query = query.where(FuelTransaction.local_date <= date_to)
    return query.group_by(FuelTransaction.id, FuelTransaction.occurred_at).order_by(FuelTransaction.occurred_at.desc(), FuelTransaction.id)


@router.get("/anomalies")
def anomalies(
    status: Literal["open", "resolved", "dismissed", "all"] = "open", rule: str | None = None, asset_id: str | None = None, date_from: dt.date | None = None,
    date_to: dt.date | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """One row per fill, carrying every reason it was flagged. Opening this list sends nothing to anyone."""
    query = _flag_query(caller, status, rule, asset_id, date_from, date_to)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    ids = list(db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all())
    txns = {t.id: t for t in db.scalars(select(FuelTransaction).where(FuelTransaction.id.in_(ids))).all()} if ids else {}
    flags: dict[str, list[FuelAnomaly]] = {}
    for flag in (db.scalars(select(FuelAnomaly).where(FuelAnomaly.transaction_id.in_(ids))).all() if ids else []):
        flags.setdefault(flag.transaction_id, []).append(flag)
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_({f.acted_by for fs in flags.values() for f in fs if f.acted_by}))).all()} if flags else {}
    assets, workers = _labels(db, list(txns.values()))
    weekly_start = fuel.settings(_org(db, caller.org_id))["weekly_start"]
    return {"items": [{**txn_out(txns[i], assets, workers, weekly_start), "reasons": _reasons(flags.get(i, []), names)} for i in ids], "total": total, "page": page,
            "page_size": page_size, "rules": [{"rule": r, "label": label} for r, (_, label) in fuel.RULES.items()], "can_edit": caller.can("assets", "edit")}


@router.get("/anomalies/matching-ids")
def matching_ids(
    status: Literal["open", "resolved", "dismissed", "all"] = "open", rule: str | None = None, asset_id: str | None = None, date_from: dt.date | None = None,
    date_to: dt.date | None = None, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Every fill matching the filters (up to 500), for 'select all that match'."""
    ids = list(db.scalars(_flag_query(caller, status, rule, asset_id, date_from, date_to).limit(501)).all())
    return {"ids": ids[:500], "truncated": len(ids) > 500}


def _act(db: Session, request: Request, caller: Caller, txn_ids: list[str], action: str, note: str | None, rules: list[str] | None) -> dict[str, Any]:
    """Resolve, dismiss or reopen flags on the chosen fills. Each fill is checked against the caller's
    organisation here; anything not found is counted, not acted on. Measurements are never changed."""
    if action == "dismiss" and not (note and note.strip()):
        raise HTTPException(422, "Say why these are being dismissed")
    wanted = set(rules) if rules else None
    if wanted and not wanted <= set(fuel.RULES):
        raise HTTPException(422, "Unknown reason")
    target = {"resolve": "resolved", "dismiss": "dismissed", "reopen": "open"}[action]
    counts = {"changed": 0, "unchanged": 0, "not_found": 0}
    for txn_id in dict.fromkeys(txn_ids):
        txn = db.get(FuelTransaction, txn_id)
        if txn is None or txn.org_id != caller.org_id:
            counts["not_found"] += 1
            continue
        flags = [f for f in db.scalars(select(FuelAnomaly).where(FuelAnomaly.transaction_id == txn.id)).all() if not wanted or f.rule in wanted]
        movable = [f for f in flags if (f.status != "open" if action == "reopen" else f.status == "open")]
        if not movable:
            counts["unchanged"] += 1
            continue
        for flag in movable:
            flag.status, flag.note, flag.acted_by, flag.acted_at = target, (note or "").strip() or None, caller.user.id, utcnow()
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action=f"fuel.anomaly_{target}", target_type="fuel_transaction", target_id=txn.id,
              detail={"rules": [f.rule for f in movable], "note": (note or "").strip() or None})
        counts["changed"] += 1
    db.commit()
    return counts


@router.post("/anomalies/bulk-resolve")
def bulk_resolve(body: BulkIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _act(db, request, caller, body.txn_ids, "resolve", body.note, body.rules)


@router.post("/anomalies/bulk-dismiss")
def bulk_dismiss(body: BulkIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _act(db, request, caller, body.txn_ids, "dismiss", body.note, body.rules)


@router.post("/anomalies/bulk-reopen")
def bulk_reopen(body: BulkIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _act(db, request, caller, body.txn_ids, "reopen", body.note, body.rules)


@router.post("/anomalies/bulk-attribute")
def bulk_attribute(body: BulkAttributeIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Match every chosen fill to one vehicle. A fill already matched to it by a person is left as it is."""
    asset = fleet.get_asset(db, caller.org_id, body.asset_id)
    counts = {"changed": 0, "unchanged": 0, "not_found": 0}
    changed: list[FuelTransaction] = []
    for txn_id in dict.fromkeys(body.txn_ids):
        txn = db.get(FuelTransaction, txn_id)
        if txn is None or txn.org_id != caller.org_id:
            counts["not_found"] += 1
        elif _attribute(db, request, caller, txn, asset, db.get(Worker, txn.worker_id) if txn.worker_id else None, body.reason.strip()):
            counts["changed"] += 1
            changed.append(txn)
        else:
            counts["unchanged"] += 1
    db.flush()
    fuel.redetect(db, changed)
    db.commit()
    return counts


@router.post("/anomalies/{txn_id}/resolve")
def resolve(txn_id: str, body: ActIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    _get_txn(db, caller.org_id, txn_id)
    return _act(db, request, caller, [txn_id], "resolve", body.note, body.rules)


@router.post("/anomalies/{txn_id}/dismiss")
def dismiss(txn_id: str, body: ActIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    _get_txn(db, caller.org_id, txn_id)
    return _act(db, request, caller, [txn_id], "dismiss", body.note, body.rules)


@router.post("/anomalies/{txn_id}/reopen")
def reopen(txn_id: str, body: ActIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    _get_txn(db, caller.org_id, txn_id)
    return _act(db, request, caller, [txn_id], "reopen", body.note, body.rules)
