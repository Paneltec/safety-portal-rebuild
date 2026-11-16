"""Fleet and equipment: shared rules for the asset register (stage 6a).

  * distance (odometer, km) and running time (hour meter) are separate and never mixed;
  * a reading lower than the one on file is not applied. It is kept as evidence and shown for a
    person to look at; only an admin "meter reset" with a reason can wind a meter back;
  * nothing here deletes an asset because a provider stopped listing it.
"""
import math
import secrets
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Asset, AssetMeterReading, utcnow
from .services import iso

KINDS = {"vehicle": "Vehicle", "plant": "Plant", "trailer": "Trailer", "equipment": "Equipment", "other": "Other"}
STATUSES = {"active": "In use", "out_of_service": "Out of service", "archived": "Archived"}
SOURCES = {None: "Added by hand", "navixy": "Navixy", "simulator": "Simulator (not real)"}
PICKER_KINDS = ("vehicle", "plant", "trailer")
# Fields a provider may fill in. A hand edit to one of these is remembered and kept on the next sync.
PROVIDER_FIELDS = ("name",)
DEFAULT_CATEGORIES = ["Ladders and access", "Lifting and rigging", "Electrical test equipment", "Survey and measuring", "Traffic control devices", "Small plant and tools"]
PROVIDER_SOURCES = ("navixy", "simulator")


def new_scan_token() -> str:
    return secrets.token_urlsafe(24)


def finite(value: Any, label: str, *, maximum: float) -> float | None:
    """A known, sensible number or None. Never NaN, infinite or negative."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{label} must be a number") from None
    if not math.isfinite(number) or number < 0 or number > maximum:
        raise HTTPException(422, f"{label} must be between 0 and {maximum:,.0f}")
    return number


def get_asset(db: Session, org_id: str, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None or asset.org_id != org_id or asset.deleted_at is not None:
        raise HTTPException(404, "Asset not found")
    return asset


def asset_label(asset: Asset) -> str:
    return f"{asset.name} ({asset.registration})" if asset.registration and asset.registration.lower() not in asset.name.lower() else asset.name


def asset_brief(asset: Asset) -> dict[str, Any]:
    return {"id": asset.id, "name": asset.name, "label": asset_label(asset), "registration": asset.registration, "kind": asset.kind,
            "kind_label": KINDS.get(asset.kind, asset.kind), "source": asset.source_system or "manual", "source_label": SOURCES.get(asset.source_system, asset.source_system),
            "simulated": asset.source_system == "simulator", "tracker_id": asset.source_id if asset.source_system in PROVIDER_SOURCES else None}


def record_reading(db: Session, asset: Asset, *, odometer_km: float | None, engine_hours: float | None, source: str, reading_date: date,
                   recorded_by: str | None, note: str | None = None, applied: bool = True) -> AssetMeterReading:
    """Keep one provider reading per asset, day and source; every hand-entered reading is its own row."""
    row = None
    if source in PROVIDER_SOURCES:
        row = db.scalars(select(AssetMeterReading).where(AssetMeterReading.asset_id == asset.id, AssetMeterReading.reading_date == reading_date,
                                                         AssetMeterReading.source == source, AssetMeterReading.applied == applied)).first()
    if row is None:
        row = AssetMeterReading(org_id=asset.org_id, asset_id=asset.id, reading_date=reading_date, source=source, applied=applied)
        db.add(row)
    row.odometer_km, row.engine_hours, row.note, row.recorded_by = odometer_km, engine_hours, note, recorded_by
    return row


def apply_meter(db: Session, asset: Asset, *, odometer_km: float | None, engine_hours: float | None, source: str, reading_date: date,
                recorded_by: str | None, note: str | None = None, strict: bool = False) -> dict[str, Any]:
    """Take a new reading. Returns {"applied": bool, "lower": [..]}.

    A value lower than the one on file is never applied. With `strict` (a person typing it in) that
    is an error they can read; otherwise (a provider) it is kept as evidence and flagged for review.
    """
    lower = []
    if odometer_km is not None and asset.odometer_km is not None and odometer_km < asset.odometer_km - 0.5:
        lower.append({"meter": "odometer_km", "on_file": asset.odometer_km, "incoming": odometer_km})
    if engine_hours is not None and asset.engine_hours is not None and engine_hours < asset.engine_hours - 0.05:
        lower.append({"meter": "engine_hours", "on_file": asset.engine_hours, "incoming": engine_hours})
    if lower:
        if strict:
            what = " and ".join(f"the {'odometer' if x['meter'] == 'odometer_km' else 'hour meter'} on file is {x['on_file']:,.1f}" for x in lower)
            raise HTTPException(409, f"This reading is lower than the last one: {what}. If the meter was replaced or the last reading was wrong, an admin can reset it with a reason.")
        asset.meter_review = {"readings": lower, "source": source, "reading_date": reading_date.isoformat(), "noticed_at": iso(utcnow())}
        record_reading(db, asset, odometer_km=odometer_km, engine_hours=engine_hours, source=source, reading_date=reading_date, recorded_by=recorded_by,
                       note="Lower than the reading on file, so it was not used.", applied=False)
        return {"applied": False, "lower": lower}
    changed = False
    if odometer_km is not None and odometer_km != asset.odometer_km:
        asset.odometer_km, changed = odometer_km, True
    if engine_hours is not None and engine_hours != asset.engine_hours:
        asset.engine_hours, changed = engine_hours, True
    if changed or source not in PROVIDER_SOURCES:
        asset.meter_at = utcnow()
        record_reading(db, asset, odometer_km=odometer_km, engine_hours=engine_hours, source=source, reading_date=reading_date, recorded_by=recorded_by, note=note)
    return {"applied": True, "lower": []}


def tracker_out(state: dict[str, Any] | None, now: datetime | None = None) -> dict[str, Any] | None:
    """Location as last reported. A missing value stays missing; it is never shown as zero."""
    if not state:
        return None
    out = {k: state.get(k) for k in ("lat", "lng", "speed_kmh", "last_seen_at", "connection")}
    stale = None
    if state.get("last_seen_at"):
        try:
            seen = datetime.fromisoformat(str(state["last_seen_at"]).replace("Z", "+00:00"))
            stale = ((now or utcnow()) - seen).total_seconds() > 24 * 3600
        except ValueError:
            stale = None
    out["stale"] = stale
    return out
