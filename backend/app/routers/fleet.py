"""The fleet and equipment register, its QR labels, meters and documents, and the Navixy sync (stage 6a)."""
import datetime as dt
import io
import re
from typing import Any, Literal

import segno
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import fleet, integrations, navixy
from ..db import get_db
from ..deps import Caller, get_caller, require_admin, require_permission
from ..files import file_out, file_response, save_upload
from ..models import Asset, AssetDocument, AssetMeterReading, Company, EquipmentCategory, ImportRun, Organisation, Site, StoredFile, User, Worker, utcnow
from ..pdf import asset_label_pdf
from ..people import today_for, worker_name
from ..services import audit, iso, portal_base_url

router = APIRouter(prefix="/api/assets", tags=["fleet"])
fleet_router = APIRouter(prefix="/api/fleet", tags=["fleet"])
equipment_router = APIRouter(prefix="/api/equipment", tags=["fleet"])

Kind = Literal["vehicle", "plant", "trailer", "equipment", "other"]
Status = Literal["active", "out_of_service", "archived"]


class AssetIn(BaseModel):
    kind: Kind = "vehicle"
    name: str = Field(min_length=1, max_length=200)
    sub_type: str | None = Field(default=None, max_length=80)
    category_id: str | None = None
    registration: str | None = Field(default=None, max_length=40)
    serial_number: str | None = Field(default=None, max_length=80)
    make: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    year: int | None = Field(default=None, ge=1950, le=2100)
    company_id: str | None = None
    site_id: str | None = None
    assigned_worker_id: str | None = None
    location_note: str | None = Field(default=None, max_length=200)
    status: Status = "active"
    fuel_type: str | None = Field(default=None, max_length=20)
    tank_capacity_l: float | None = None
    odometer_km: float | None = None
    engine_hours: float | None = None
    last_inspection_date: dt.date | None = None
    next_inspection_date: dt.date | None = None
    notes: str | None = Field(default=None, max_length=4000)


class AssetPatch(AssetIn):
    kind: Kind | None = None  # type: ignore[assignment]
    name: str | None = Field(default=None, min_length=1, max_length=200)  # type: ignore[assignment]
    status: Status | None = None  # type: ignore[assignment]


class MeterIn(BaseModel):
    odometer_km: float | None = None
    engine_hours: float | None = None
    reading_date: dt.date | None = None
    note: str | None = Field(default=None, max_length=400)


class MeterResetIn(MeterIn):
    reason: str = Field(min_length=5, max_length=400)


class SyncIn(BaseModel):
    dry_run: bool = True


class TagsIn(BaseModel):
    tag_ids: list[str] = Field(default_factory=list, max_length=200)


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class LabelsIn(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=200)


def _org(db: Session, org_id: str) -> Organisation:
    return db.get(Organisation, org_id)


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _check_refs(db: Session, org_id: str, data: dict[str, Any]) -> None:
    for key, model, label in (("company_id", Company, "Company"), ("site_id", Site, "Site"), ("assigned_worker_id", Worker, "Worker"), ("category_id", EquipmentCategory, "Category")):
        if data.get(key):
            row = db.get(model, data[key])
            if row is None or row.org_id != org_id or getattr(row, "deleted_at", None) is not None:
                raise HTTPException(422, f"{label} not found")


def _names(db: Session, assets: list[Asset]) -> dict[str, dict[str, str]]:
    def load(model, ids, label):
        return {r.id: label(r) for r in db.scalars(select(model).where(model.id.in_(ids))).all()} if ids else {}
    return {"company": load(Company, {a.company_id for a in assets if a.company_id}, lambda r: r.name),
            "site": load(Site, {a.site_id for a in assets if a.site_id}, lambda r: r.name),
            "worker": load(Worker, {a.assigned_worker_id for a in assets if a.assigned_worker_id}, worker_name),
            "category": load(EquipmentCategory, {a.category_id for a in assets if a.category_id}, lambda r: r.name)}


def _inspection(asset: Asset, today: dt.date) -> str | None:
    if asset.next_inspection_date is None:
        return None
    if asset.next_inspection_date < today:
        return "overdue"
    return "due_soon" if (asset.next_inspection_date - today).days <= 30 else "ok"


def asset_out(asset: Asset, names: dict[str, dict[str, str]], today: dt.date, *, full: bool = False) -> dict[str, Any]:
    out = {**fleet.asset_brief(asset), "sub_type": asset.sub_type, "category_id": asset.category_id, "category": names["category"].get(asset.category_id or ""),
           "serial_number": asset.serial_number, "make": asset.make, "model": asset.model, "year": asset.year,
           "company_id": asset.company_id, "company": names["company"].get(asset.company_id or ""), "site_id": asset.site_id, "site": names["site"].get(asset.site_id or ""),
           "assigned_worker_id": asset.assigned_worker_id, "assigned_worker": names["worker"].get(asset.assigned_worker_id or ""), "location_note": asset.location_note,
           "status": asset.status, "status_label": fleet.STATUSES.get(asset.status, asset.status), "fuel_type": asset.fuel_type, "tank_capacity_l": asset.tank_capacity_l,
           "odometer_km": asset.odometer_km, "engine_hours": asset.engine_hours, "meter_at": iso(asset.meter_at), "meter_review": asset.meter_review,
           "last_inspection_date": asset.last_inspection_date.isoformat() if asset.last_inspection_date else None,
           "next_inspection_date": asset.next_inspection_date.isoformat() if asset.next_inspection_date else None, "inspection_status": _inspection(asset, today),
           "tags": list(asset.source_groups or []), "not_listed_by_provider": asset.source_missing, "tracker": fleet.tracker_out(asset.tracker_state),
           "updated_at": iso(asset.updated_at)}
    if full:
        out.update({"notes": asset.notes, "locally_edited": list(asset.locally_edited or []), "created_at": iso(asset.created_at)})
    return out


def _doc_out(db: Session, doc: AssetDocument, today: dt.date) -> dict[str, Any]:
    stored = db.get(StoredFile, doc.file_id)
    state = None
    if doc.expiry_date is not None:
        state = "expired" if doc.expiry_date < today else "expiring" if (doc.expiry_date - today).days <= 30 else "valid"
    return {"id": doc.id, "kind": doc.kind, "title": doc.title, "issued_date": doc.issued_date.isoformat() if doc.issued_date else None,
            "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None, "expiry_status": state, "notes": doc.notes,
            "file": file_out(stored) if stored else None, "created_at": iso(doc.created_at)}


# ---------- register ----------

@router.get("")
def list_assets(
    kind: str | None = None, status: str | None = None, q: str | None = None, source: str | None = None, site_id: str | None = None,
    category_id: str | None = None, tag: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """The register, filtered and paged on the server. Archived assets are left out unless asked for."""
    query = select(Asset).where(Asset.org_id == caller.org_id, Asset.deleted_at.is_(None))
    if kind == "fleet":
        query = query.where(Asset.kind != "equipment")
    elif kind:
        query = query.where(Asset.kind == kind)
    query = query.where(Asset.status == status) if status else query.where(Asset.status != "archived")
    if source:
        query = query.where(Asset.source_system.is_(None) if source == "manual" else Asset.source_system == source)
    if site_id:
        query = query.where(Asset.site_id == site_id)
    if category_id:
        query = query.where(Asset.category_id == category_id)
    if tag:
        query = query.where(Asset.source_groups.contains([tag]))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(Asset.name).like(like), func.lower(func.coalesce(Asset.registration, "")).like(like),
                                func.lower(func.coalesce(Asset.serial_number, "")).like(like), func.lower(func.coalesce(Asset.make, "")).like(like),
                                func.lower(func.coalesce(Asset.model, "")).like(like)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(func.lower(Asset.name), Asset.id).offset((page - 1) * page_size).limit(page_size)).all()
    today = today_for(_org(db, caller.org_id))
    names = _names(db, list(rows))
    return {"items": [asset_out(a, names, today) for a in rows], "total": total, "page": page, "page_size": page_size,
            "can_edit": caller.can("assets", "edit"), "can_delete": caller.can("assets", "delete"), "kinds": fleet.KINDS, "statuses": fleet.STATUSES}


@router.get("/categories")
def categories(caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Counts for the filter: by kind, then by type within the kind."""
    rows = db.execute(select(Asset.kind, Asset.sub_type, func.count()).where(Asset.org_id == caller.org_id, Asset.deleted_at.is_(None), Asset.status != "archived")
                      .group_by(Asset.kind, Asset.sub_type)).all()
    out: dict[str, dict[str, Any]] = {}
    for kind, sub_type, count in rows:
        entry = out.setdefault(kind, {"kind": kind, "label": fleet.KINDS.get(kind, kind), "count": 0, "types": []})
        entry["count"] += count
        if sub_type:
            entry["types"].append({"sub_type": sub_type, "count": count})
    tags = sorted({t for (groups,) in db.execute(select(Asset.source_groups).where(Asset.org_id == caller.org_id, Asset.deleted_at.is_(None))).all() for t in (groups or [])})
    return {"items": [out[k] for k in fleet.KINDS if k in out], "tags": tags}


@router.get("/scan/{scan_token}")
def scan(scan_token: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """What a scanned label points at. Signed-in people of the same organisation only; a retired label is gone."""
    asset = db.scalars(select(Asset).where(Asset.scan_token == scan_token)).first() if scan_token else None
    if asset is None or asset.org_id != caller.org_id or asset.deleted_at is not None:
        raise HTTPException(404, "This label is not recognised. It may have been replaced.")
    if asset.status == "archived":
        raise HTTPException(410, "This asset has been archived.")
    return {**fleet.asset_brief(asset), "status": asset.status, "status_label": fleet.STATUSES.get(asset.status, asset.status), "scan_token": asset.scan_token,
            "can_open": caller.can("assets", "view")}


@router.post("", status_code=201)
def create_asset(body: AssetIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    data = body.model_dump()
    _check_refs(db, caller.org_id, data)
    odometer = fleet.finite(data.pop("odometer_km"), "Odometer", maximum=5_000_000)
    hours = fleet.finite(data.pop("engine_hours"), "Hour meter", maximum=500_000)
    data["tank_capacity_l"] = fleet.finite(data["tank_capacity_l"], "Tank size", maximum=100_000)
    for key in ("name", "sub_type", "registration", "serial_number", "make", "model", "location_note", "fuel_type", "notes"):
        data[key] = _clean(data[key])
    if not data["name"]:
        raise HTTPException(422, "Name is required")
    if data["registration"]:
        data["registration"] = data["registration"].upper()
    asset = Asset(org_id=caller.org_id, scan_token=fleet.new_scan_token(), created_by=caller.user.id, locally_edited=[], source_data={}, source_groups=[], **data)
    db.add(asset)
    db.flush()
    today = today_for(_org(db, caller.org_id))
    if odometer is not None or hours is not None:
        fleet.apply_meter(db, asset, odometer_km=odometer, engine_hours=hours, source="manual", reading_date=today, recorded_by=caller.user.id, note="Entered when the asset was added")
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="asset.created", target_type="asset", target_id=asset.id, detail={"name": asset.name, "kind": asset.kind})
    db.commit()
    return asset_out(asset, _names(db, [asset]), today, full=True)


def _detail(db: Session, caller: Caller, asset: Asset) -> dict[str, Any]:
    today = today_for(_org(db, caller.org_id))
    docs = db.scalars(select(AssetDocument).where(AssetDocument.asset_id == asset.id, AssetDocument.deleted_at.is_(None)).order_by(AssetDocument.created_at.desc())).all()
    out = asset_out(asset, _names(db, [asset]), today, full=True)
    out.update({"documents": [_doc_out(db, d, today) for d in docs], "can_edit": caller.can("assets", "edit"), "can_delete": caller.can("assets", "delete"),
                "can_reset_meter": caller.is_admin, "scan_url": f"/app/scan/asset/{asset.scan_token}" if caller.can("assets", "edit") else None})
    return out


@router.get("/{asset_id}")
def get_one(asset_id: str, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _detail(db, caller, fleet.get_asset(db, caller.org_id, asset_id))


@router.put("/{asset_id}")
def update_asset(asset_id: str, body: AssetPatch, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    data = body.model_dump(exclude_unset=True)
    if "odometer_km" in data or "engine_hours" in data:
        raise HTTPException(422, "Meter readings are recorded with 'Update meter', so the history is kept")
    _check_refs(db, caller.org_id, data)
    for key in ("name", "kind", "status"):
        if key in data and data[key] is None:
            raise HTTPException(422, f"{key.capitalize()} is required")
    if "tank_capacity_l" in data:
        data["tank_capacity_l"] = fleet.finite(data["tank_capacity_l"], "Tank size", maximum=100_000)
    changed = []
    for key, value in data.items():
        if isinstance(value, str):
            value = _clean(value)
            if key == "registration" and value:
                value = value.upper()
            if key == "name" and not value:
                raise HTTPException(422, "Name is required")
        if getattr(asset, key) != value:
            setattr(asset, key, value)
            changed.append(key)
    if asset.source_system and any(k in fleet.PROVIDER_FIELDS for k in changed):
        asset.locally_edited = sorted(set(asset.locally_edited or []) | {k for k in changed if k in fleet.PROVIDER_FIELDS})
    if changed:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="asset.updated", target_type="asset", target_id=asset.id, detail={"fields": changed})
    db.commit()
    return _detail(db, caller, asset)


@router.delete("/{asset_id}", status_code=204)
def delete_asset(asset_id: str, request: Request, caller: Caller = Depends(require_permission("assets", "delete")), db: Session = Depends(get_db)) -> Response:
    """Removes the asset from the register. Its history is kept, and a provider sync will not bring it back."""
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    asset.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="asset.deleted", target_type="asset", target_id=asset.id, detail={"name": asset.name})
    db.commit()
    return Response(status_code=204)


# ---------- labels ----------

def _scan_link(request: Request, asset: Asset) -> str:
    return f"{portal_base_url(request)}/app/scan/asset/{asset.scan_token}"


@router.post("/{asset_id}/rotate-qr")
def rotate_qr(asset_id: str, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Retire every label printed so far for this asset."""
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    asset.scan_token = fleet.new_scan_token()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="asset.qr_rotated", target_type="asset", target_id=asset.id)
    db.commit()
    return {"ok": True}


@router.get("/{asset_id}/qr.png")
def qr_png(asset_id: str, request: Request, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> Response:
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    buffer = io.BytesIO()
    segno.make(_scan_link(request, asset), error="m").save(buffer, kind="png", scale=8, border=3)
    return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


def _labels(db: Session, request: Request, caller: Caller, assets: list[Asset]) -> Response:
    org = _org(db, caller.org_id)
    data = asset_label_pdf(org_name=(org.details or {}).get("trading_name") or org.name,
                           labels=[{"url": _scan_link(request, a), "name": a.name, "registration": a.registration or "", "kind": fleet.KINDS.get(a.kind, a.kind)} for a in assets])
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="asset-labels.pdf"', "Cache-Control": "no-store"})


@router.get("/{asset_id}/label.pdf")
def label_pdf(asset_id: str, request: Request, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> Response:
    return _labels(db, request, caller, [fleet.get_asset(db, caller.org_id, asset_id)])


@router.post("/labels/bulk")
def labels_bulk(body: LabelsIn, request: Request, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> Response:
    """One PDF with a label per chosen asset. Every asset is checked; an unknown one fails the request."""
    return _labels(db, request, caller, [fleet.get_asset(db, caller.org_id, i) for i in dict.fromkeys(body.asset_ids)])


# ---------- meters ----------

@router.post("/{asset_id}/meter")
def update_meter(asset_id: str, body: MeterIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    odometer = fleet.finite(body.odometer_km, "Odometer", maximum=5_000_000)
    hours = fleet.finite(body.engine_hours, "Hour meter", maximum=500_000)
    if odometer is None and hours is None:
        raise HTTPException(422, "Enter an odometer reading, an hour-meter reading, or both")
    today = today_for(_org(db, caller.org_id))
    if body.reading_date and body.reading_date > today:
        raise HTTPException(422, "The reading date cannot be in the future")
    fleet.apply_meter(db, asset, odometer_km=odometer, engine_hours=hours, source="manual", reading_date=body.reading_date or today, recorded_by=caller.user.id,
                      note=_clean(body.note), strict=True)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="asset.meter_updated", target_type="asset", target_id=asset.id,
          detail={"odometer_km": odometer, "engine_hours": hours})
    db.commit()
    return _detail(db, caller, asset)


@router.post("/{asset_id}/meter/reset")
def reset_meter(asset_id: str, body: MeterResetIn, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Admin only: set a meter to a lower figure (a replaced meter, a wrong earlier reading). The reason is kept."""
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    odometer = fleet.finite(body.odometer_km, "Odometer", maximum=5_000_000)
    hours = fleet.finite(body.engine_hours, "Hour meter", maximum=500_000)
    if odometer is None and hours is None:
        raise HTTPException(422, "Enter the correct odometer reading, hour-meter reading, or both")
    before = {"odometer_km": asset.odometer_km, "engine_hours": asset.engine_hours}
    if odometer is not None:
        asset.odometer_km = odometer
    if hours is not None:
        asset.engine_hours = hours
    asset.meter_at, asset.meter_review = utcnow(), None
    today = today_for(_org(db, caller.org_id))
    fleet.record_reading(db, asset, odometer_km=odometer, engine_hours=hours, source="reset", reading_date=body.reading_date or today, recorded_by=caller.user.id, note=body.reason.strip())
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="asset.meter_reset", target_type="asset", target_id=asset.id,
          detail={"before": before, "after": {"odometer_km": asset.odometer_km, "engine_hours": asset.engine_hours}, "reason": body.reason.strip()})
    db.commit()
    return _detail(db, caller, asset)


@router.post("/{asset_id}/meter/dismiss-review")
def dismiss_meter_review(asset_id: str, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Someone has looked at the lower provider reading and the figure on file stands."""
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    if asset.meter_review:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="asset.meter_review_dismissed", target_type="asset", target_id=asset.id, detail=asset.meter_review)
        asset.meter_review = None
        db.commit()
    return _detail(db, caller, asset)


@router.get("/{asset_id}/meter-history")
def meter_history(asset_id: str, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    rows = db.scalars(select(AssetMeterReading).where(AssetMeterReading.asset_id == asset.id).order_by(AssetMeterReading.reading_date.desc(), AssetMeterReading.created_at.desc()).limit(200)).all()
    ids = {r.recorded_by for r in rows if r.recorded_by}
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}
    labels = {"manual": "Entered by hand", "navixy": "Navixy", "simulator": "Simulator (not real)", "service": "Service record", "fuel": "Fuel fill", "reset": "Reset by an admin"}
    return {"items": [{"id": r.id, "reading_date": r.reading_date.isoformat(), "odometer_km": r.odometer_km, "engine_hours": r.engine_hours, "source": r.source,
                       "source_label": labels.get(r.source, r.source), "applied": r.applied, "note": r.note, "recorded_by": names.get(r.recorded_by or "")} for r in rows]}


# ---------- documents ----------

@router.post("/{asset_id}/documents", status_code=201)
def add_document(
    asset_id: str, request: Request, file: UploadFile = File(...), title: str = Form(""), kind: str = Form("document"),
    issued_date: str = Form(""), expiry_date: str = Form(""), notes: str = Form(""),
    caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db),
) -> dict[str, Any]:
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    if kind not in ("document", "certificate", "photo"):
        raise HTTPException(422, "Kind must be document, certificate or photo")
    dates: dict[str, dt.date | None] = {}
    for key, raw in (("issued_date", issued_date), ("expiry_date", expiry_date)):
        try:
            dates[key] = dt.date.fromisoformat(raw) if raw.strip() else None
        except ValueError:
            raise HTTPException(422, "Dates must look like 2026-11-30") from None
    if dates["issued_date"] and dates["expiry_date"] and dates["expiry_date"] < dates["issued_date"]:
        raise HTTPException(422, "The expiry date is before the issue date")
    stored = save_upload(db, org_id=caller.org_id, upload=file, purpose="asset_document", created_by=caller.user.id,
                         allowed_mimes={"image/jpeg", "image/png", "image/webp"} if kind == "photo" else None)
    doc = AssetDocument(org_id=caller.org_id, asset_id=asset.id, kind=kind, title=(title.strip() or stored.original_name)[:200], file_id=stored.id,
                        notes=notes.strip() or None, uploaded_by=caller.user.id, **dates)
    db.add(doc)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="asset.document_added", target_type="asset", target_id=asset.id, detail={"title": doc.title, "kind": kind})
    db.commit()
    return _doc_out(db, doc, today_for(_org(db, caller.org_id)))


def _get_doc(db: Session, asset: Asset, doc_id: str) -> AssetDocument:
    doc = db.get(AssetDocument, doc_id)
    if doc is None or doc.asset_id != asset.id or doc.deleted_at is not None:
        raise HTTPException(404, "Document not found")
    return doc


@router.get("/{asset_id}/documents/{doc_id}/download")
def download_document(asset_id: str, doc_id: str, inline: bool = False, caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)):
    doc = _get_doc(db, fleet.get_asset(db, caller.org_id, asset_id), doc_id)
    stored = db.get(StoredFile, doc.file_id)
    if stored is None:
        raise HTTPException(404, "The file for this document is missing")
    return file_response(stored, inline=inline)


@router.delete("/{asset_id}/documents/{doc_id}", status_code=204)
def delete_document(asset_id: str, doc_id: str, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> Response:
    asset = fleet.get_asset(db, caller.org_id, asset_id)
    doc = _get_doc(db, asset, doc_id)
    doc.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="asset.document_removed", target_type="asset", target_id=asset.id, detail={"title": doc.title})
    db.commit()
    return Response(status_code=204)


# ---------- Navixy ----------

def _navixy_state(db: Session, org_id: str) -> tuple[Any, dict[str, Any]]:
    config = integrations.get_config(db, org_id, "navixy")
    kind = integrations.CATALOGUE["navixy"]
    status = integrations.status_of(kind, config)
    ready, why = False, "Navixy is not set up. An admin can set it up under Settings, Connections."
    if config is not None and config.mode == "simulator":
        ready, why = True, "The Navixy simulator is on. Vehicles it lists are invented and are labelled as simulator."
    elif config is not None and config.mode == "live":
        missing = integrations.missing_required(kind, config)
        if missing:
            why = "Navixy details are missing: " + ", ".join(missing) + "."
        elif config.last_test_ok is not True or config.last_test_mode != "live":
            why = "Test the Navixy connection successfully under Settings, Connections, before syncing."
        else:
            ready, why = True, "Navixy is connected."
    return config, {"state": status["state"], "ready": ready, "message": why,
                    "simulated": bool(config and config.mode == "simulator")}


@fleet_router.get("/navixy/status")
def navixy_status(caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    config, state = _navixy_state(db, caller.org_id)
    last = db.scalars(select(ImportRun).where(ImportRun.org_id == caller.org_id, ImportRun.provider == "navixy", ImportRun.status == "committed")
                      .order_by(ImportRun.started_at.desc())).first()
    failed = db.scalars(select(ImportRun).where(ImportRun.org_id == caller.org_id, ImportRun.provider == "navixy").order_by(ImportRun.started_at.desc())).first()
    return {**state, "tags": ((config.capabilities or {}).get("tags") or []) if config else [], "selected_tags": navixy.selected_tags(config) if config else [],
            "grouping_note": "Vehicles are grouped by Navixy tags. Navixy's separate 'groups' are not used.",
            "last_synced_at": iso(last.finished_at) if last else None, "last_counts": last.counts if last else None,
            "last_error": failed.error if failed is not None and failed.status == "failed" else None,
            "can_sync": caller.can("integrations", "edit") and caller.can("assets", "edit")}


def _sync_caller(caller: Caller = Depends(get_caller)) -> Caller:
    if not (caller.can("integrations", "edit") and caller.can("assets", "edit")):
        raise HTTPException(403, "You do not have permission to do this")
    return caller


@fleet_router.post("/navixy/sync")
def navixy_sync(body: SyncIn, request: Request, caller: Caller = Depends(_sync_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Preview (the default) or bring the Navixy tracker list into the register."""
    config, state = _navixy_state(db, caller.org_id)
    if not state["ready"]:
        raise HTTPException(409, state["message"])
    run, detail = navixy.run(db, caller.org_id, config, actor_id=caller.user.id, dry_run=body.dry_run, today=today_for(_org(db, caller.org_id)))
    if not body.dry_run or run.status == "failed":
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="navixy.sync", target_type="integration", target_id="navixy",
              detail={"status": run.status, "counts": run.counts, "mode": run.mode})
    db.commit()
    return {"id": run.id, "status": run.status, "dry_run": run.dry_run, "simulated": run.mode == "simulator", "counts": run.counts or {}, "error": run.error, **detail}


@fleet_router.put("/navixy/tags")
def navixy_tags(body: TagsIn, request: Request, caller: Caller = Depends(_sync_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Choose which Navixy tags feed the register. Empty means every tracker."""
    config = integrations.get_config(db, caller.org_id, "navixy")
    if config is None:
        raise HTTPException(409, "Navixy is not set up.")
    known = {t["id"] for t in (config.capabilities or {}).get("tags") or []}
    unknown = [t for t in body.tag_ids if t not in known]
    if unknown:
        raise HTTPException(422, "Choose from the tags Navixy listed at the last sync")
    config.settings = {**(config.settings or {}), "selected_tags": list(dict.fromkeys(body.tag_ids))}
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="navixy.tags_selected", target_type="integration", target_id="navixy", detail={"count": len(body.tag_ids)})
    db.commit()
    return {"selected_tags": navixy.selected_tags(config)}


def vehicle_picker(db: Session, org_id: str) -> dict[str, Any]:
    """The list behind a form's vehicle question: the register, whatever its source. When there is
    nothing to choose from, the form still works by typing the vehicle in."""
    rows = db.scalars(select(Asset).where(Asset.org_id == org_id, Asset.deleted_at.is_(None), Asset.status == "active", Asset.kind.in_(fleet.PICKER_KINDS))
                      .order_by(func.lower(Asset.name)).limit(2000)).all()
    _, state = _navixy_state(db, org_id)
    message = None
    if not rows:
        message = "No vehicles are on the fleet register yet. Type the vehicle in instead." if state["ready"] else "The vehicle list is not connected. Type the vehicle in instead."
    return {"items": [fleet.asset_brief(a) for a in rows], "available": bool(rows), "connection": "simulator" if state["simulated"] else "connected" if state["ready"] else "not_connected",
            "message": message, "manual_entry_allowed": True}


# ---------- equipment ----------

def ensure_categories(db: Session, org_id: str) -> None:
    if db.scalars(select(EquipmentCategory.id).where(EquipmentCategory.org_id == org_id).limit(1)).first() is None:
        db.add_all(EquipmentCategory(org_id=org_id, name=name, seeded=True) for name in fleet.DEFAULT_CATEGORIES)
        db.flush()


def _category(db: Session, org_id: str, cid: str) -> EquipmentCategory:
    row = db.get(EquipmentCategory, cid)
    if row is None or row.org_id != org_id or row.deleted_at is not None:
        raise HTTPException(404, "Category not found")
    return row


def _category_name(db: Session, org_id: str, name: str, *, excluding: str | None = None) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(422, "Name is required")
    clash = db.scalars(select(EquipmentCategory).where(EquipmentCategory.org_id == org_id, EquipmentCategory.deleted_at.is_(None), func.lower(EquipmentCategory.name) == name.lower())).first()
    if clash is not None and clash.id != excluding:
        raise HTTPException(409, "There is already a category with this name")
    return name


@equipment_router.get("/categories")
def list_categories(caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    ensure_categories(db, caller.org_id)
    db.commit()
    rows = db.scalars(select(EquipmentCategory).where(EquipmentCategory.org_id == caller.org_id, EquipmentCategory.deleted_at.is_(None)).order_by(func.lower(EquipmentCategory.name))).all()
    counts = dict(db.execute(select(Asset.category_id, func.count()).where(Asset.org_id == caller.org_id, Asset.deleted_at.is_(None), Asset.category_id.is_not(None)).group_by(Asset.category_id)).all())
    return {"items": [{"id": r.id, "name": r.name, "count": counts.get(r.id, 0)} for r in rows], "can_edit": caller.can("assets", "edit")}


@equipment_router.post("/categories", status_code=201)
def create_category(body: CategoryIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    ensure_categories(db, caller.org_id)
    row = EquipmentCategory(org_id=caller.org_id, name=_category_name(db, caller.org_id, body.name))
    db.add(row)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="equipment_category.created", target_type="equipment_category", target_id=row.id, detail={"name": row.name})
    db.commit()
    return {"id": row.id, "name": row.name, "count": 0}


@equipment_router.patch("/categories/{cid}")
def rename_category(cid: str, body: CategoryIn, request: Request, caller: Caller = Depends(require_permission("assets", "edit")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _category(db, caller.org_id, cid)
    row.name = _category_name(db, caller.org_id, body.name, excluding=row.id)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="equipment_category.renamed", target_type="equipment_category", target_id=row.id, detail={"name": row.name})
    db.commit()
    return {"id": row.id, "name": row.name}


@equipment_router.delete("/categories/{cid}", status_code=204)
def delete_category(cid: str, request: Request, caller: Caller = Depends(require_permission("assets", "delete")), db: Session = Depends(get_db)) -> Response:
    """A category still holding equipment cannot be removed: move the equipment first."""
    row = _category(db, caller.org_id, cid)
    in_use = db.scalar(select(func.count()).select_from(Asset).where(Asset.category_id == row.id, Asset.deleted_at.is_(None))) or 0
    if in_use:
        raise HTTPException(409, f"{in_use} item{'s are' if in_use != 1 else ' is'} in this category. Move {'them' if in_use != 1 else 'it'} to another category first.")
    row.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="equipment_category.deleted", target_type="equipment_category", target_id=row.id, detail={"name": row.name})
    db.commit()
    return Response(status_code=204)


@equipment_router.get("/summary")
def equipment_summary(caller: Caller = Depends(require_permission("assets", "view")), db: Session = Depends(get_db)) -> dict[str, Any]:
    today = today_for(_org(db, caller.org_id))
    rows = db.scalars(select(Asset).where(Asset.org_id == caller.org_id, Asset.deleted_at.is_(None), Asset.kind == "equipment", Asset.status != "archived")).all()
    states = [_inspection(a, today) for a in rows]
    expiring = db.scalar(select(func.count()).select_from(AssetDocument).join(Asset, Asset.id == AssetDocument.asset_id).where(
        AssetDocument.org_id == caller.org_id, AssetDocument.deleted_at.is_(None), Asset.deleted_at.is_(None), Asset.kind == "equipment",
        AssetDocument.expiry_date.is_not(None), AssetDocument.expiry_date <= today + dt.timedelta(days=30))) or 0
    return {"total": len(rows), "out_of_service": sum(1 for a in rows if a.status == "out_of_service"), "inspection_overdue": states.count("overdue"),
            "inspection_due_soon": states.count("due_soon"), "no_inspection_date": states.count(None), "certificates_expiring_or_expired": expiring}
