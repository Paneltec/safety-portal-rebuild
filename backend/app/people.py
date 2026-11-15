"""Workers and certifications: shared rules used by the register, the form pickers and the
certification gate.

Certificate states (spec section 9):
  valid     in date, or explicitly recorded as never expiring
  expiring  in date but inside the warning window
  expired   past its expiry date
  unknown   no expiry date recorded. This is NOT treated as valid.
  missing   the worker holds no current certificate of an expected type
"""
import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Certification, CertificationKind, FormTemplate, Organisation, User, Worker
from .permissions import slugify

DEFAULT_WINDOW_DAYS = 30
STATUS_LABELS = {
    "valid": "Valid", "expiring": "Expiring soon", "expired": "Expired",
    "unknown": "Expiry not recorded", "missing": "Missing",
}
OK_STATES = ("valid", "expiring")


@lru_cache
def _reference_kinds() -> list[dict[str, Any]]:
    return json.loads((Path(__file__).parent / "data" / "certification_kinds.json").read_text())


def today_for(org: Organisation) -> date:
    try:
        return datetime.now(ZoneInfo(org.timezone or "Australia/Hobart")).date()
    except Exception:  # an unknown zone name must not take the register down
        return datetime.now(ZoneInfo("Australia/Hobart")).date()


def cert_settings(org: Organisation) -> dict[str, Any]:
    raw = (org.details or {}).get("certifications") or {}
    return {
        "expiry_window_days": int(raw.get("expiry_window_days") or DEFAULT_WINDOW_DAYS),
        "expected_kinds": list(raw.get("expected_kinds") or []),
        # The organisation-wide half of the double switch. Off by default so nobody is locked out.
        "gate_enabled": bool(raw.get("gate_enabled", False)),
    }


def ensure_kinds(db: Session, org_id: str) -> list[CertificationKind]:
    """The organisation's certification types. The reference list is copied in on first use."""
    rows = db.scalars(select(CertificationKind).where(CertificationKind.org_id == org_id)).all()
    if not rows:
        for item in _reference_kinds():
            db.add(CertificationKind(org_id=org_id, slug=item["slug"], label=item["label"],
                                     aliases=list(item.get("aliases") or []), is_system=True,
                                     category="induction" if item["slug"].endswith("_induction") else "certificate"))
        db.flush()
        rows = db.scalars(select(CertificationKind).where(CertificationKind.org_id == org_id)).all()
    return sorted(rows, key=lambda k: k.label.lower())


def _norm(text: str) -> str:
    return " ".join(text.replace("_", " ").replace("-", " ").lower().split())


def resolve_kind(db: Session, org_id: str, value: str) -> CertificationKind | None:
    """Match a slug, a label or a known alias, ignoring case, spaces, hyphens and underscores."""
    wanted = _norm(value or "")
    if not wanted:
        return None
    for kind in ensure_kinds(db, org_id):
        names = [kind.slug, kind.label, *(kind.aliases or [])]
        if any(_norm(n) == wanted for n in names):
            return kind
    return None


def kind_labels(db: Session, org_id: str) -> dict[str, str]:
    return {k.slug: k.label for k in ensure_kinds(db, org_id)}


def new_kind_slug(db: Session, org_id: str, label: str) -> str:
    base = slugify(label).replace("-", "_") or "certificate"
    taken = {k.slug for k in ensure_kinds(db, org_id)}
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    return slug


def cert_status(cert: Certification, today: date, window_days: int) -> tuple[str, int | None]:
    """(state, days until expiry). Days are negative once expired, None when there is no date."""
    if cert.no_expiry:
        return "valid", None
    if cert.expiry_date is None:
        return "unknown", None
    days = (cert.expiry_date - today).days
    if days < 0:
        return "expired", days
    if days <= window_days:
        return "expiring", days
    return "valid", days


def worker_name(worker: Worker) -> str:
    return " ".join(part for part in (worker.first_name, worker.last_name) if part).strip()


def worker_for_user(db: Session, user: User) -> Worker | None:
    if not user.worker_id:
        return None
    worker = db.get(Worker, user.worker_id)
    if worker is None or worker.org_id != user.org_id or worker.deleted_at is not None:
        return None
    return worker


def current_certs(db: Session, worker_id: str) -> list[Certification]:
    """Certificates that count: not replaced by a renewal, not archived, not deleted."""
    return list(db.scalars(
        select(Certification).where(
            Certification.worker_id == worker_id, Certification.superseded_by_id.is_(None),
            Certification.archived_at.is_(None), Certification.deleted_at.is_(None),
        )
    ).all())


def missing_expected(db: Session, org: Organisation, worker: Worker) -> list[str]:
    held = {c.kind_slug for c in current_certs(db, worker.id)}
    return [slug for slug in cert_settings(org)["expected_kinds"] if slug not in held]


def gate_problems(db: Session, org: Organisation, template: FormTemplate, user: User) -> list[dict[str, str]]:
    """Why the certification gate stops this person filling in this form. Empty means allowed.

    Both switches must be on: the organisation-wide gate and the form's own gate.
    """
    settings = cert_settings(org)
    required = list(template.required_certifications or [])
    if not (settings["gate_enabled"] and template.certification_gate_enabled and required):
        return []
    labels = kind_labels(db, org.id)
    worker = worker_for_user(db, user)
    if worker is None:
        return [{"slug": "", "label": "Worker record", "problem": "no_worker",
                 "message": "Your login is not linked to a worker record, so your certificates cannot be checked."}]
    today = today_for(org)
    best: dict[str, str] = {}
    order = {"valid": 0, "expiring": 1, "unknown": 2, "expired": 3}
    for cert in current_certs(db, worker.id):
        state, _ = cert_status(cert, today, settings["expiry_window_days"])
        if cert.kind_slug not in best or order[state] < order[best[cert.kind_slug]]:
            best[cert.kind_slug] = state
    problems = []
    for slug in required:
        state = best.get(slug, "missing")
        if state in OK_STATES:
            continue
        label = labels.get(slug, slug)
        text = {"missing": f"{label}: no certificate on record",
                "expired": f"{label}: certificate has expired",
                "unknown": f"{label}: expiry date is not recorded"}[state]
        problems.append({"slug": slug, "label": label, "problem": state, "message": text})
    return problems


def enforce_gate(db: Session, org: Organisation, template: FormTemplate, user: User) -> None:
    problems = gate_problems(db, org, template, user)
    if problems:
        raise HTTPException(403, {
            "message": "This form needs current certificates before it can be filled in.",
            "certification_gate": problems,
        })
