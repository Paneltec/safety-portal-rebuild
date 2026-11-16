"""The pay workbench routes (stage 7c). Test only: nothing is paid, lodged or sent.

Every route needs payroll access (the payroll owner, or a named grant to an administrator):
`view` to read, `edit` to change. A worker reaches only their own payslips, and only once a
finalised run's payslips have been released.
"""
import csv
import datetime as dt
import io
import re
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import payroll, payroll_engine as engine
from ..crypto import decrypt_json, encrypt_json, key_status
from ..db import get_db
from ..deps import Caller, get_caller, require_permission
from ..models import LeaveRequest, Organisation, PayProfile, PayRun, PaySettings, PaySettingsHistory, User, Worker, utcnow
from ..pdf import payslip_pdf
from ..people import today_for, worker_for_user, worker_name
from ..services import audit, iso
from .suppliers import clean_abn

router = APIRouter(prefix="/api/payroll", tags=["payroll"])
me_router = APIRouter(prefix="/api/me/payroll", tags=["payroll"])
VIEW, EDIT = require_permission("payroll", "view"), require_permission("payroll", "edit")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _org(db: Session, org_id: str) -> Organisation:
    return db.get(Organisation, org_id)


# ---------- what this workbench will not do ----------

BLOCKED = {
    "bank_file": "Bank payment files are not produced by this test workbench. Nothing is paid from here.",
    "stp": "Single Touch Payroll lodgement is not connected. Nothing is sent to the Tax Office from here.",
    "super": "Super contributions are not submitted from here. No fund or clearing house is connected.",
    "deliver": "Payslips are not emailed or texted. Workers see their own payslip in the portal once it is released.",
    "reset": "There is no reset. Pay runs, settings history and evidence are kept.",
}


def _refuse(db: Session, request: Request, caller: Caller, what: str, target: str | None) -> None:
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="payroll.blocked", target_type="pay_run", target_id=target, detail={"what": what})
    db.commit()
    raise HTTPException(409, BLOCKED[what])


@router.get("/workbench/status")
def status(caller: Caller = Depends(VIEW), db: Session = Depends(get_db)) -> dict[str, Any]:
    """States plainly what this workbench is and is not."""
    return {"mode": "test", "statement": payroll.TEST_ONLY, "not_available": BLOCKED, "rule_version": engine.RULE_VERSION,
            "tax_table": {"source": engine.TAX_SOURCE, "paydays_from": engine.TAX_FROM.isoformat(), "paydays_before": engine.TAX_UNTIL.isoformat(), "verified": False,
                          "note": "This is the table the earlier software used. It has not been checked against the Tax Office's published schedule."},
            "encryption": key_status(), "can_edit": caller.can("payroll", "edit")}


@router.post("/banking/batch/{run_id}/download")
def bank_file(run_id: str, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> None:
    _refuse(db, request, caller, "bank_file", run_id)


@router.post("/runs/{run_id}/submit/{kind}")
def submit_to_provider(run_id: str, kind: Literal["stp", "super"], request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> None:
    _refuse(db, request, caller, kind, run_id)


@router.post("/runs/{run_id}/payslips/deliver")
def deliver_payslips(run_id: str, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> None:
    _refuse(db, request, caller, "deliver", run_id)


@router.post("/reset")
def reset(request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> None:
    _refuse(db, request, caller, "reset", None)


# ---------- settings ----------

class EarningRule(BaseModel):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)
    basis: Literal["fixed", "base_rate"] = "fixed"
    rate: float | None = Field(default=None, ge=0, le=10000)
    multiplier: float | None = Field(default=None, ge=0, le=10)
    taxable: bool | None = None
    superable: bool | None = None


class RulesIn(BaseModel):
    ot1_multiplier: float = Field(ge=1, le=5)
    ot2_multiplier: float = Field(ge=1, le=5)
    night_multiplier: float = Field(ge=1, le=5)
    holiday_work_multiplier: float = Field(ge=1, le=5)
    saturday_multiplier: float | None = Field(default=None, ge=1, le=5)
    sunday_multiplier: float | None = Field(default=None, ge=1, le=5)
    super_percent: float = Field(ge=0, le=30)
    meal_allowance: float = Field(ge=0, le=500)
    meal_tax_treatment: Literal["taxable", "exempt"] | None = None
    daily_ordinary_hours: float = Field(gt=0, le=24)
    ot1_hours: float = Field(ge=0, le=12)
    night_start: str
    night_end: str
    earning_rules: list[EarningRule] = Field(default_factory=list, max_length=50)


class SettingsIn(BaseModel):
    expected_revision: int
    rules: RulesIn
    employer_name: str | None = Field(default=None, max_length=200)
    employer_abn: str | None = Field(default=None, max_length=20)
    week_start_day: int = Field(default=0, ge=0, le=6)


def _settings_out(db: Session, org: Organisation, caller: Caller) -> dict[str, Any]:
    row = payroll.get_settings(db, org.id)
    history = db.scalars(select(PaySettingsHistory).where(PaySettingsHistory.org_id == org.id).order_by(PaySettingsHistory.revision.desc()).limit(10)).all()
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_({h.changed_by for h in history if h.changed_by}))).all()} if history else {}
    day = ((org.details or {}).get("payroll") or {}).get("week_start_day", 0)
    return {"revision": row.revision if row else 0, "rules": payroll.rules_for(db, org.id), "using_defaults": row is None, "employer_name": row.employer_name if row else None,
            "employer_abn": row.employer_abn if row else None, "week_start_day": day, "rule_version": engine.RULE_VERSION, "can_edit": caller.can("payroll", "edit"),
            "defaults_note": "The starting figures are the ones the earlier software used. They are not a ruling on any award. Check each one.",
            "history": [{"revision": h.revision, "changed_by": names.get(h.changed_by or ""), "changed_at": iso(h.changed_at)} for h in history]}


@router.get("/settings")
def get_settings(caller: Caller = Depends(VIEW), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _settings_out(db, _org(db, caller.org_id), caller)


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Save the pay rules and employer details. The previous version is kept; a save made from an out-of-date screen is refused."""
    org = _org(db, caller.org_id)
    row = payroll.get_settings(db, org.id)
    if body.expected_revision != (row.revision if row else 0):
        raise HTTPException(409, "These settings changed in another window. Reload before saving.")
    rules = body.rules.model_dump()
    if not _CLOCK.match(rules["night_start"]) or not _CLOCK.match(rules["night_end"]):
        raise HTTPException(422, "The night window needs times like 18:00")
    if rules["night_start"] == rules["night_end"]:
        raise HTTPException(422, "The night window cannot start and end at the same time")
    codes = [r["code"].upper() for r in rules["earning_rules"]]
    if len(set(codes)) != len(codes):
        raise HTTPException(422, "Each earning or allowance code can be used once")
    for r in rules["earning_rules"]:
        if r["basis"] == "fixed" and r["rate"] is None:
            raise HTTPException(422, f"{r['name']}: enter the rate")
        if r["basis"] == "base_rate" and r["multiplier"] is None:
            raise HTTPException(422, f"{r['name']}: enter the multiplier of the base rate")
    abn = clean_abn(body.employer_abn)
    if row is None:
        row = PaySettings(org_id=org.id, revision=0)
        db.add(row)
    else:
        db.add(PaySettingsHistory(org_id=org.id, revision=row.revision, changed_by=row.updated_by, changed_at=row.updated_at,
                                  data={"rules": row.rules, "employer_name": row.employer_name, "employer_abn": row.employer_abn}))
    row.rules, row.employer_name, row.employer_abn = rules, (body.employer_name or "").strip() or None, abn
    row.revision, row.updated_by, row.updated_at = row.revision + 1, caller.user.id, utcnow()
    org.details = {**(org.details or {}), "payroll": {**((org.details or {}).get("payroll") or {}), "week_start_day": body.week_start_day}}
    audit(db, request, org_id=org.id, actor_id=caller.user.id, action="payroll.settings_changed", target_type="organisation", target_id=org.id, detail={"revision": row.revision})
    db.commit()
    return _settings_out(db, org, caller)


# ---------- employee pay profiles ----------

class CasualRates(BaseModel):
    base_rate: float = Field(gt=0, le=1000)
    loading_percent: float = Field(ge=0, le=100)
    ot1: float | None = Field(default=None, gt=0, le=2000)
    ot2: float | None = Field(default=None, gt=0, le=2000)
    night: float | None = Field(default=None, gt=0, le=2000)
    holiday_work: float | None = Field(default=None, gt=0, le=2000)


class ProfileIn(BaseModel):
    expected_revision: int
    hourly_rate: float | None = Field(default=None, ge=0, le=1000)
    annual_salary: float | None = Field(default=None, ge=0, le=2_000_000)
    employment_type: Literal["full_time", "part_time", "casual", "contractor"] | None = None
    pay_basis: Literal["hourly", "salary"] = "hourly"
    ordinary_weekly_hours: float | None = Field(default=None, ge=0, le=80)
    conditions_reviewed: bool = False
    tax_mode: Literal["resident_threshold", "resident_no_threshold", "manual"] | None = None
    tax_declaration_reviewed: bool = False
    annual_weeks: float = Field(default=4, ge=0, le=10)
    personal_weeks: float = Field(default=2, ge=0, le=10)
    leave_loading_percent: float = Field(default=0, ge=0, le=100)
    casual_rates: CasualRates | None = None
    opening_annual: float = Field(default=0, ge=-500, le=5000)
    opening_personal: float = Field(default=0, ge=-500, le=5000)


class BankIn(BaseModel):
    account_name: str = Field(min_length=2, max_length=60)
    bsb: str = Field(pattern=r"^\d{3}-?\d{3}$")
    account_number: str = Field(pattern=r"^\d{4,9}$")


def _worker(db: Session, org_id: str, worker_id: str) -> Worker:
    worker = db.get(Worker, worker_id)
    if worker is None or worker.org_id != org_id or worker.deleted_at is not None:
        raise HTTPException(404, "Worker not found")
    return worker


def _profile_out(worker: Worker, profile: PayProfile | None, caller: Caller) -> dict[str, Any]:
    data = (profile.data if profile else None) or {}
    try:
        rates, _ = engine.applied_rates(data, engine.DEFAULT_RULES)
    except engine.EngineError:
        rates = {}
    return {"worker_id": worker.id, "name": worker_name(worker), "position": worker.position, "has_profile": profile is not None, "revision": profile.revision if profile else 0,
            **{k: data.get(k) for k in payroll.PROFILE_KEYS}, "base_rate": float(rates["ordinary"]) if rates else None,
            "bank": {"is_set": bool(profile and profile.bank), "hint": f"Account ending {profile.bank_hint}" if profile and profile.bank_hint else None},
            "can_edit": caller.can("payroll", "edit")}


@router.get("/profiles")
def profiles(caller: Caller = Depends(VIEW), db: Session = Depends(get_db)) -> dict[str, Any]:
    workers = db.scalars(select(Worker).where(Worker.org_id == caller.org_id, Worker.deleted_at.is_(None), Worker.archived_at.is_(None), Worker.active.is_(True))).all()
    found = {p.worker_id: p for p in db.scalars(select(PayProfile).where(PayProfile.org_id == caller.org_id)).all()}
    return {"items": sorted((_profile_out(w, found.get(w.id), caller) for w in workers), key=lambda p: p["name"].lower()), "can_edit": caller.can("payroll", "edit")}


@router.get("/profiles/{worker_id}")
def profile(worker_id: str, caller: Caller = Depends(VIEW), db: Session = Depends(get_db)) -> dict[str, Any]:
    worker = _worker(db, caller.org_id, worker_id)
    return _profile_out(worker, db.get(PayProfile, worker.id), caller)


@router.put("/profiles/{worker_id}")
def put_profile(worker_id: str, body: ProfileIn, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> dict[str, Any]:
    worker = _worker(db, caller.org_id, worker_id)
    row = db.get(PayProfile, worker.id)
    if body.expected_revision != (row.revision if row else 0):
        raise HTTPException(409, "This profile changed in another window. Reload before saving.")
    data = body.model_dump(exclude={"expected_revision"})
    if data["employment_type"] != "casual":
        data["casual_rates"] = None
    if row is None:
        row = PayProfile(worker_id=worker.id, org_id=caller.org_id, revision=0)
        db.add(row)
    changed = sorted(k for k, v in data.items() if (row.data or {}).get(k) != v)
    row.data, row.revision, row.updated_by, row.updated_at = data, row.revision + 1, caller.user.id, utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="payroll.profile_changed", target_type="worker", target_id=worker.id, detail={"fields": changed})  # names of fields only, never the figures
    db.commit()
    return _profile_out(worker, row, caller)


@router.put("/profiles/{worker_id}/bank")
def put_bank(worker_id: str, body: BankIn, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Store bank details encrypted. They are never returned by a list or a profile, only by the audited reveal."""
    worker = _worker(db, caller.org_id, worker_id)
    if not key_status()["configured"]:
        raise HTTPException(503, "Bank details cannot be stored because the server's encryption key is not set.")
    row = db.get(PayProfile, worker.id)
    if row is None:
        row = PayProfile(worker_id=worker.id, org_id=caller.org_id, data={}, revision=1)
        db.add(row)
    row.bank = encrypt_json({"account_name": body.account_name.strip(), "bsb": body.bsb.replace("-", ""), "account_number": body.account_number})
    row.bank_hint, row.updated_by, row.updated_at = body.account_number[-3:], caller.user.id, utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="payroll.bank_changed", target_type="worker", target_id=worker.id)
    db.commit()
    return _profile_out(worker, row, caller)["bank"]


@router.post("/profiles/{worker_id}/bank/reveal")
def reveal_bank(worker_id: str, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> dict[str, Any]:
    worker = _worker(db, caller.org_id, worker_id)
    row = db.get(PayProfile, worker.id)
    if row is None or not row.bank:
        raise HTTPException(404, "No bank details are stored for this worker")
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="payroll.bank_revealed", target_type="worker", target_id=worker.id)
    db.commit()
    return decrypt_json(row.bank)


@router.delete("/profiles/{worker_id}/bank", status_code=204)
def clear_bank(worker_id: str, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> Response:
    worker = _worker(db, caller.org_id, worker_id)
    row = db.get(PayProfile, worker.id)
    if row is not None and row.bank:
        row.bank, row.bank_hint = None, None
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="payroll.bank_cleared", target_type="worker", target_id=worker.id)
        db.commit()
    return Response(status_code=204)


# ---------- pay runs ----------

class RunCreate(BaseModel):
    week_start: dt.date
    extra: bool = False


class RowIn(BaseModel):
    manual: dict[str, float] = Field(default_factory=dict)
    adjust: dict[str, Any] = Field(default_factory=dict)
    hours_reviewed: bool = False
    super_reviewed: bool = False


class RunSave(BaseModel):
    expected_revision: int
    payday: dt.date | None = None
    rows: dict[str, RowIn | None] = Field(default_factory=dict)


class ReasonIn(BaseModel):
    expected_revision: int
    reason: str | None = Field(default=None, max_length=600)


class EvidenceIn(BaseModel):
    reference: str = Field(min_length=3, max_length=200)
    completed_on: dt.date
    note: str | None = Field(default=None, max_length=600)
    confirm: bool = False
    correction_reason: str | None = Field(default=None, max_length=600)


class ReleaseIn(BaseModel):
    release: bool


def _run(db: Session, org_id: str, run_id: str) -> PayRun:
    run = db.get(PayRun, run_id)
    if run is None or run.org_id != org_id:
        raise HTTPException(404, "Pay run not found")
    return run


def _check(run: PayRun, expected: int) -> None:
    if run.revision != expected:
        raise HTTPException(409, "This pay run changed in another window. Reload before saving.")


def _run_brief(run: PayRun) -> dict[str, Any]:
    totals = (run.frozen or {}).get("totals") if run.status == "finalised" else None
    return {"id": run.id, "week_start": run.week_start.isoformat(), "week_end": (run.week_start + dt.timedelta(days=6)).isoformat(), "sequence": run.sequence,
            "label": "Weekly run" if run.sequence == 0 else f"Extra run {run.sequence}", "status": run.status, "status_label": "Finalised" if run.status == "finalised" else "Draft",
            "payday": run.payday.isoformat() if run.payday else None, "revision": run.revision, "totals": totals, "payslips_released": run.payslips_released,
            "evidence_kinds": sorted({e["kind"] for e in run.evidence or []}), "finalised_at": iso(run.finalised_at)}


def _run_out(db: Session, caller: Caller, run: PayRun) -> dict[str, Any]:
    out = _run_brief(run)
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_({e.get("by") for e in (run.history or []) + (run.evidence or []) if e.get("by")}))).all()}
    out.update({"can_edit": caller.can("payroll", "edit"), "statement": payroll.TEST_ONLY, "rule_version": engine.RULE_VERSION,
                "history": [{**h, "by": names.get(h.get("by") or "")} for h in run.history or []],
                "evidence": [{**e, "by": names.get(e.get("by") or "")} for e in run.evidence or []],
                "evidence_note": "Recorded by hand. The portal did not pay, lodge or submit anything."})
    if run.status == "finalised":
        frozen = run.frozen or {}
        corrections = db.scalars(select(LeaveRequest).where(LeaveRequest.pay_run_id == run.id, LeaveRequest.needs_payroll_correction.is_(True))).all()
        workers = {w.id: worker_name(w) for w in db.scalars(select(Worker).where(Worker.id.in_({c.worker_id for c in corrections}))).all()} if corrections else {}
        out.update({"rows": frozen.get("rows", []), "totals": frozen.get("totals"), "hash": run.frozen_hash, "employer": frozen.get("employer"), "stale": [], "ready": True,
                    "corrections": [{"leave_id": c.id, "worker": workers.get(c.worker_id, ""), "hours": f"{c.hours:.2f}", "leave_type": c.leave_type, "reason": c.cancel_reason,
                                     "text": "This leave was cancelled after the run was finalised. The pay has not been changed; correct it in an extra run."} for c in corrections]})
    else:
        computed = payroll.compute(db, caller.org_id, run)
        stale = payroll.stale_workers(db, caller.org_id, run)
        out.update({"rows": computed["rows"], "totals": computed["totals"], "ready": computed["ready"] and not stale, "stale": stale, "corrections": [],
                    "stale_note": "Approved hours or leave have changed since they were brought in. Bring them in again before finalising." if stale else None})
    return out


@router.get("/runs")
def runs(caller: Caller = Depends(VIEW), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(PayRun).where(PayRun.org_id == caller.org_id).order_by(PayRun.week_start.desc(), PayRun.sequence.desc()).limit(104)).all()
    return {"items": [_run_brief(r) for r in rows], "can_edit": caller.can("payroll", "edit"), "statement": payroll.TEST_ONLY}


@router.post("/runs", status_code=201)
def create_run(body: RunCreate, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Start the weekly run for a seven-day week, or an extra run for a week that already has one."""
    org = _org(db, caller.org_id)
    if body.week_start > today_for(org) + dt.timedelta(days=7):
        raise HTTPException(422, "That week is too far ahead")
    existing = db.scalars(select(PayRun).where(PayRun.org_id == org.id, PayRun.week_start == body.week_start).order_by(PayRun.sequence.desc())).all()
    if existing and not body.extra:
        raise HTTPException(409, "This week already has a pay run. Open it, or start an extra run.")
    if body.extra and not existing:
        raise HTTPException(422, "An extra run is for a week that already has its weekly run")
    run = PayRun(org_id=org.id, week_start=body.week_start, sequence=(existing[0].sequence + 1) if existing else 0, payday=payroll.default_payday(body.week_start), created_by=caller.user.id, rows={})
    db.add(run)
    db.flush()
    if run.sequence == 0:
        _import(db, org.id, run)
    audit(db, request, org_id=org.id, actor_id=caller.user.id, action="payroll.run_created", target_type="pay_run", target_id=run.id, detail={"week_start": body.week_start.isoformat(), "sequence": run.sequence})
    db.commit()
    return _run_out(db, caller, run)


def _import(db: Session, org_id: str, run: PayRun) -> dict[str, int]:
    """Copy approved hours and leave into the worksheet. Adjustments are kept; a worker whose hours changed must be reviewed again."""
    sources = payroll.collect_sources(db, org_id, run)
    rows = dict(run.rows or {})
    counts = {"workers": len(sources), "changed": 0, "removed": 0}
    for worker_id, src in sources.items():
        row = dict(rows.get(worker_id) or {"manual": {}, "adjust": {}, "hours_reviewed": False, "super_reviewed": False})
        if (row.get("sources") or {}).get("fingerprint") != src["fingerprint"]:
            row["hours_reviewed"] = False
            counts["changed"] += 1
        row["hours"], row["meal_count"] = src["hours"], src["meal_count"]
        row["sources"] = {k: src[k] for k in ("timesheets", "leave", "unpaid_leave", "fingerprint")}
        rows[worker_id] = row
    for worker_id, row in list(rows.items()):
        if worker_id not in sources and (row.get("sources") or {}).get("fingerprint"):
            row = {**row, "hours": {}, "meal_count": 0, "sources": {}, "hours_reviewed": False}
            rows[worker_id] = row
            counts["removed"] += 1
    run.rows = rows
    return counts


@router.get("/runs/{run_id}")
def get_run(run_id: str, caller: Caller = Depends(VIEW), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _run_out(db, caller, _run(db, caller.org_id, run_id))


@router.post("/runs/{run_id}/import")
def import_sources(run_id: str, body: ReasonIn, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> dict[str, Any]:
    run = _run(db, caller.org_id, run_id)
    if run.status != "draft":
        raise HTTPException(409, "This run is finalised. Reopen it to bring hours in again.")
    if run.sequence != 0:
        raise HTTPException(409, "An extra run does not take hours from timesheets. Enter its figures by hand.")
    _check(run, body.expected_revision)
    counts = _import(db, caller.org_id, run)
    run.revision += 1
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="payroll.sources_imported", target_type="pay_run", target_id=run.id, detail=counts)
    db.commit()
    return {**_run_out(db, caller, run), "imported": counts}


def _clean_row(db: Session, org_id: str, worker_id: str, incoming: RowIn, existing: dict[str, Any] | None) -> dict[str, Any]:
    _worker(db, org_id, worker_id)
    manual = {}
    for bucket, value in incoming.manual.items():
        if bucket not in payroll.MANUAL_BUCKETS:
            raise HTTPException(422, f"'{bucket}' is not a kind of hours")
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > 168:
            raise HTTPException(422, "Hours must be between 0 and 168")
        if number:
            manual[bucket] = float(number)
    adjust = {k: v for k, v in incoming.adjust.items() if k in payroll.ADJUST_KEYS and v not in (None, "", [])}
    base = existing or {"hours": {}, "meal_count": 0, "sources": {}}
    return {**base, "manual": manual, "adjust": adjust, "hours_reviewed": incoming.hours_reviewed, "super_reviewed": incoming.super_reviewed}


@router.put("/runs/{run_id}")
def save_run(run_id: str, body: RunSave, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Save the worksheet: payday, hand-entered hours, adjustments and the reviewed ticks. Hours that came
    from timesheets and leave are not changed here. A row with no timesheet or leave behind it can be removed (null)."""
    run = _run(db, caller.org_id, run_id)
    if run.status != "draft":
        raise HTTPException(409, "This run is finalised. Reopen it to change it.")
    _check(run, body.expected_revision)
    if body.payday is not None:
        if body.payday < run.week_start:
            raise HTTPException(422, "The payday cannot be before the week it pays")
        run.payday = body.payday
    rows = dict(run.rows or {})
    for worker_id, incoming in body.rows.items():
        if incoming is None:
            if (rows.get(worker_id, {}).get("sources") or {}).get("fingerprint"):
                raise HTTPException(409, "This worker has approved hours or leave in the week, so the row cannot be removed.")
            rows.pop(worker_id, None)
        else:
            rows[worker_id] = _clean_row(db, caller.org_id, worker_id, incoming, rows.get(worker_id))
    run.rows, run.revision = rows, run.revision + 1
    db.flush()
    computed = payroll.compute(db, caller.org_id, run)
    for row in computed["rows"]:
        if row["error"] and row["error"] != "Set the payday first":
            raise HTTPException(422, f"{row['name']}: {row['error']}")  # nothing unusable is saved
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="payroll.run_saved", target_type="pay_run", target_id=run.id, detail={"revision": run.revision})
    db.commit()
    return _run_out(db, caller, run)


@router.post("/runs/{run_id}/finalise")
def finalise(run_id: str, body: ReasonIn, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Freeze the run. Refused unless every row is free of review issues, the hours are current, and the
    employer details are set. This records figures; it does not pay anyone."""
    run = _run(db, caller.org_id, run_id)
    if run.status != "draft":
        raise HTTPException(409, "This run is already finalised.")
    _check(run, body.expected_revision)
    settings = payroll.get_settings(db, caller.org_id)
    if settings is None or not settings.employer_name or not settings.employer_abn:
        raise HTTPException(422, "Set the legal employer name and a valid ABN in the pay settings first")
    if run.payday is None:
        raise HTTPException(422, "Set the payday first")
    if payroll.stale_workers(db, caller.org_id, run):
        raise HTTPException(422, "Approved hours or leave have changed since they were brought in. Bring them in again and review before finalising.")
    computed = payroll.compute(db, caller.org_id, run)
    if not computed["rows"]:
        raise HTTPException(422, "There is nobody on this pay run")
    waiting = [r["name"] for r in computed["rows"] if r["result"] is None or not r["result"]["review_ready"]]
    if waiting:
        raise HTTPException(422, "Resolve every review item before finalising. Still to do: " + ", ".join(waiting[:10]) + ".")
    run.frozen, run.frozen_hash = payroll.freeze(run, computed, settings)
    run.status, run.finalised_by, run.finalised_at, run.revision = "finalised", caller.user.id, utcnow(), run.revision + 1
    run.history = [*(run.history or []), {"event": "finalised", "by": caller.user.id, "at": iso(utcnow()), "hash": run.frozen_hash, "revision": run.revision}]
    payroll.lock_sources(db, run, lock=True)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="payroll.run_finalised", target_type="pay_run", target_id=run.id,
          detail={"hash": run.frozen_hash, "rows": len(computed["rows"]), "rule_version": engine.RULE_VERSION})
    db.commit()
    return _run_out(db, caller, run)


@router.post("/runs/{run_id}/reopen")
def reopen(run_id: str, body: ReasonIn, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Undo a finalise, with a reason. The earlier frozen figures stay in the run's history by their hash.
    Not allowed once a payment has been recorded: correct that with an extra run."""
    run = _run(db, caller.org_id, run_id)
    if run.status != "finalised":
        raise HTTPException(409, "Only a finalised run can be reopened.")
    _check(run, body.expected_revision)
    reason = (body.reason or "").strip()
    if len(reason) < 5:
        raise HTTPException(422, "Say why the run is being reopened")
    if any(e["kind"] == "payment" for e in run.evidence or []):
        raise HTTPException(409, "A payment has been recorded for this run, so it cannot be reopened. Make the correction in an extra run.")
    payroll.lock_sources(db, run, lock=False)
    run.history = [*(run.history or []), {"event": "reopened", "by": caller.user.id, "at": iso(utcnow()), "reason": reason, "hash": run.frozen_hash, "revision": run.revision + 1}]
    run.status, run.frozen, run.frozen_hash, run.finalised_by, run.finalised_at, run.payslips_released, run.revision = "draft", None, None, None, None, False, run.revision + 1
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="payroll.run_reopened", target_type="pay_run", target_id=run.id, detail={"reason": reason})
    db.commit()
    return _run_out(db, caller, run)


def _finalised(db: Session, org_id: str, run_id: str) -> PayRun:
    run = _run(db, org_id, run_id)
    if run.status != "finalised":
        raise HTTPException(409, "This is only available once the run is finalised.")
    return run


@router.get("/runs/{run_id}/report.csv")
def register_csv(run_id: str, caller: Caller = Depends(VIEW), db: Session = Depends(get_db)) -> Response:
    """The pay register for a finalised run. It contains no bank details."""
    run = _finalised(db, caller.org_id, run_id)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Worker", "Ordinary hours", "Gross", "Tax withheld", "Net", "Qualifying earnings", "Super", "Annual leave balance (hours)", "Personal leave balance (hours)"])
    for row in run.frozen["rows"]:
        r = row["result"]
        name = "'" + row["name"] if row["name"][:1] in "=+-@" else row["name"]
        writer.writerow([name, row["entry"].get("ordinary", 0), f"{r['gross']:.2f}", f"{r['payg']:.2f}", f"{r['net']:.2f}", f"{r['qualifying_earnings']:.2f}", f"{r['super']:.2f}",
                         f"{r['annual_closing']:.2f}", f"{r['personal_closing']:.2f}"])
    t = run.frozen["totals"]
    writer.writerow(["Total", "", t["gross"], t["payg"], t["net"], "", t["super"], "", ""])
    writer.writerow([f"TEST WORKBENCH. Week starting {run.week_start.isoformat()}. Run reference {run.frozen_hash[:16]}. Nothing has been paid or lodged by the portal."])
    return Response(out.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="pay-register-{run.week_start.isoformat()}-{run.sequence}.csv"', "Cache-Control": "no-store"})


def _slip(run: PayRun, worker_id: str) -> Response:
    row = next((r for r in run.frozen["rows"] if r["worker_id"] == worker_id), None)
    if row is None:
        raise HTTPException(404, "Payslip not found")
    data = payslip_pdf(employer=run.frozen["employer"], row=row, week_start=run.frozen["week_start"], payday=run.frozen["payday"], run_hash=run.frozen_hash)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="payslip-{run.week_start.isoformat()}.pdf"', "Cache-Control": "no-store"})


@router.get("/runs/{run_id}/payslips/{worker_id}/pdf")
def payslip(run_id: str, worker_id: str, caller: Caller = Depends(VIEW), db: Session = Depends(get_db)) -> Response:
    return _slip(_finalised(db, caller.org_id, run_id), worker_id)


@router.post("/runs/{run_id}/payslips/release")
def release(run_id: str, body: ReleaseIn, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Let each worker on the run see their own payslip in the portal, or hide them again. No message is sent."""
    run = _finalised(db, caller.org_id, run_id)
    if run.payslips_released != body.release:
        run.payslips_released = body.release
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="payroll.payslips_released" if body.release else "payroll.payslips_hidden", target_type="pay_run", target_id=run.id)
        db.commit()
    return {"payslips_released": run.payslips_released, "note": "No email or text was sent. Workers find their payslip under My payslips."}


@router.post("/runs/{run_id}/completion/{kind}")
def record_evidence(run_id: str, kind: Literal["payment", "stp", "super"], body: EvidenceIn, request: Request, caller: Caller = Depends(EDIT), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Record, by hand, that something was done outside the portal (the bank payment, the STP lodgement, the super
    payment), with its receipt or reference. This never pays or lodges anything."""
    run = _finalised(db, caller.org_id, run_id)
    if not body.confirm:
        raise HTTPException(422, "Confirm that this was completed outside the portal and give its receipt or reference")
    if body.completed_on < run.week_start or body.completed_on > today_for(_org(db, caller.org_id)):
        raise HTTPException(422, "The completion date must be within this pay week or later, and not in the future")
    earlier = [e for e in run.evidence or [] if e["kind"] == kind]
    if earlier and not (body.correction_reason and body.correction_reason.strip()):
        raise HTTPException(409, "Evidence is already recorded for this. To correct it, give the reason; the earlier record is kept.")
    entry = {"id": len(run.evidence or []) + 1, "kind": kind, "reference": body.reference.strip(), "completed_on": body.completed_on.isoformat(), "note": (body.note or "").strip() or None,
             "by": caller.user.id, "at": iso(utcnow()), "run_hash": run.frozen_hash, "corrects": earlier[-1]["id"] if earlier else None,
             "correction_reason": (body.correction_reason or "").strip() or None, "source": "recorded by hand"}
    run.evidence = [*(run.evidence or []), entry]
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="payroll.evidence_recorded", target_type="pay_run", target_id=run.id, detail={"kind": kind, "corrects": entry["corrects"]})
    db.commit()
    return _run_out(db, caller, run)


# ---------- a worker's own payslips ----------

@me_router.get("/payslips")
def my_payslips(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> dict[str, Any]:
    worker = worker_for_user(db, caller.user)
    if worker is None:
        return {"linked": False, "items": []}
    runs_ = db.scalars(select(PayRun).where(PayRun.org_id == caller.org_id, PayRun.status == "finalised", PayRun.payslips_released.is_(True)).order_by(PayRun.week_start.desc(), PayRun.sequence.desc()).limit(104)).all()
    items = []
    for run in runs_:
        row = next((r for r in run.frozen["rows"] if r["worker_id"] == worker.id), None)
        if row is not None:
            items.append({"run_id": run.id, "week_start": run.week_start.isoformat(), "payday": run.frozen["payday"], "label": "Weekly pay" if run.sequence == 0 else f"Extra pay {run.sequence}",
                          "gross": f"{row['result']['gross']:.2f}", "net": f"{row['result']['net']:.2f}"})
    return {"linked": True, "items": items, "note": "Test payslips from the pay workbench. They are not a record of payment."}


@me_router.get("/payslips/{run_id}/pdf")
def my_payslip_pdf(run_id: str, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> Response:
    """Your own payslip, once released. Anyone else's, or one not released, is simply not found."""
    worker = worker_for_user(db, caller.user)
    run = db.get(PayRun, run_id)
    if worker is None or run is None or run.org_id != caller.org_id or run.status != "finalised" or not run.payslips_released:
        raise HTTPException(404, "Payslip not found")
    return _slip(run, worker.id)
