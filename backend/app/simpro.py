"""Importing workers, sites and suppliers from Simpro (stage 5b).

Rules (integration document, section VI):
  * identity is company + Simpro id. A name, email or ABN is never used to decide that an incoming
    record IS an existing one; at most it raises a question for a person to answer (the review queue);
  * a field changed by hand in the portal is kept on re-import;
  * one company failing does not hide the others: each company's result is reported;
  * "last synced" moves only when an import was actually committed;
  * importing a worker never creates a login and never changes anyone's role or payroll access.

The simulator returns a small invented dataset. Records it creates are marked as coming from the
simulator (source "simulator", not "simpro"), so they can never be mistaken for, or collide with,
real Simpro records.
"""
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import integrations
from .crypto import decrypt_json
from .integrations import ProviderError
from .models import ImportReviewItem, ImportRun, IntegrationConfig, Site, Supplier, Worker, utcnow

RESOURCES = ("workers", "sites", "suppliers")
PAGE_SIZE = 250
MAX_PAGES = 200
sleep = time.sleep  # replaced in tests

_PATHS = {
    "workers": ("employees", "ID,Name,Position,PrimaryContact,Archived"),
    "sites": ("sites", "ID,Name,Address,Archived"),
    "suppliers": ("vendors", "ID,Name,ABN,Phone,Email,Address,Archived"),
}
FIELDS = {
    "workers": ("first_name", "last_name", "email", "phone", "mobile", "position", "active"),
    "sites": ("name", "address", "suburb", "state", "postcode", "active"),
    "suppliers": ("name", "abn", "contact_email", "contact_phone", "address", "suburb", "state", "postcode", "active"),
}
MODELS: dict[str, Any] = {"workers": Worker, "sites": Site, "suppliers": Supplier}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _address(raw: Any) -> dict[str, str | None]:
    a = raw if isinstance(raw, dict) else {}
    return {"address": _text(a.get("Address")), "suburb": _text(a.get("City")), "state": _text(a.get("State")), "postcode": _text(a.get("PostalCode"))}


def normalise(resource: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    """Turn one Simpro record into the portal's field names. Returns None when it has no id or name."""
    source_id, name = _text(raw.get("ID")), _text(raw.get("Name"))
    if source_id is None or name is None:
        return None
    active = not bool(raw.get("Archived"))
    if resource == "workers":
        contact = raw.get("PrimaryContact") if isinstance(raw.get("PrimaryContact"), dict) else {}
        parts = name.split()
        email = _text(contact.get("Email"))
        return {"source_id": source_id, "first_name": " ".join(parts[:-1]) or parts[0], "last_name": parts[-1] if len(parts) > 1 else "",
                "email": email.lower() if email else None, "phone": _text(contact.get("WorkPhone")), "mobile": _text(contact.get("CellPhone")),
                "position": _text(raw.get("Position")), "active": active}
    if resource == "sites":
        return {"source_id": source_id, "name": name, **_address(raw.get("Address")), "active": active}
    abn = _text(raw.get("ABN"))
    email = _text(raw.get("Email"))
    return {"source_id": source_id, "name": name, "abn": "".join(ch for ch in abn if ch.isdigit()) or None if abn else None,
            "contact_email": email.lower() if email else None, "contact_phone": _text(raw.get("Phone")), **_address(raw.get("Address")), "active": active}


# ---------- fetching ----------

def _simulated(resource: str, company_id: str) -> list[dict[str, Any]]:
    """Invented records, the same every time, so a second run shows "nothing changed"."""
    if resource == "workers":
        people = [("Sim Avery Stone", "Excavator operator", "0400 555 001"), ("Sim Blake Harrow", "Traffic controller", "0400 555 002"),
                  ("Sim Casey Lindqvist", "Leading hand", "0400 555 003"), ("Sim Devon Marsh", "Labourer", None)]
        return [{"ID": f"{company_id}-E{i}", "Name": name, "Position": position, "Archived": i == 4,
                 "PrimaryContact": {"Email": f"sim.worker{i}@simulator.invalid", "CellPhone": mobile}} for i, (name, position, mobile) in enumerate(people, 1)]
    if resource == "sites":
        sites = [("Sim Depot", "1 Example Street", "Launceston"), ("Sim Highway Upgrade Stage 2", "Example Highway", "Perth"), ("Sim Water Main Renewal", "Sample Road", "Longford")]
        return [{"ID": f"{company_id}-S{i}", "Name": name, "Archived": False, "Address": {"Address": street, "City": city, "State": "TAS", "PostalCode": "7250"}}
                for i, (name, street, city) in enumerate(sites, 1)]
    vendors = [("Sim Quarry Supplies", "0400 555 101"), ("Sim Concrete Pumping", "0400 555 102"), ("Sim Plant Hire", None)]
    return [{"ID": f"{company_id}-V{i}", "Name": name, "Phone": phone, "Email": f"sim.vendor{i}@simulator.invalid", "Archived": False,
             "Address": {"Address": f"{i} Example Lane", "City": "Launceston", "State": "TAS", "PostalCode": "7250"}} for i, (name, phone) in enumerate(vendors, 1)]


def _live_page(settings: dict[str, Any], token: str, resource: str, company_id: str, page: int) -> tuple[list[Any], int | None]:
    path, columns = _PATHS[resource]
    url = f"{settings['api_base_url']}/api/v1.0/companies/{company_id}/{path}/"
    for attempt in range(3):
        try:
            response = integrations.call("GET", url, provider="Simpro", secrets=[token],
                                         headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                                         params={"page": page, "pageSize": PAGE_SIZE, "columns": columns})
        except ProviderError as exc:
            # Simpro limits how fast it can be called. Wait briefly and try again, a bounded number of times.
            if "HTTP 429" in str(exc) and attempt < 2:
                sleep(2 * (attempt + 1))
                continue
            raise
        data = integrations._json(response, "Simpro")
        if not isinstance(data, list):
            raise ProviderError("Simpro answered, but not with a list.")
        pages = response.headers.get("Result-Pages")
        return data, int(pages) if pages and pages.isdigit() else None
    raise ProviderError("Simpro says it is being called too often (HTTP 429). Wait a few minutes and try again.")


def fetch(config: IntegrationConfig, resource: str, company_id: str) -> list[dict[str, Any]]:
    """Every record of one kind for one company, following Simpro's pages to the end."""
    if config.mode == "simulator":
        return _simulated(resource, company_id)
    kind = integrations.CATALOGUE["simpro"]
    settings = integrations.effective_settings(kind, config)
    token = decrypt_json(config.secrets)["api_token"]
    out: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        rows, pages = _live_page(settings, token, resource, company_id, page)
        out.extend(r for r in rows if isinstance(r, dict))
        if (pages is not None and page >= pages) or (pages is None and len(rows) < PAGE_SIZE):
            return out
    raise ProviderError(f"Simpro returned more than {MAX_PAGES} pages for company {company_id}. The import stopped rather than guess.")


# ---------- deciding what an incoming record means ----------

@dataclass
class Plan:
    created: list[dict[str, Any]] = field(default_factory=list)
    updated: list[dict[str, Any]] = field(default_factory=list)
    unchanged: int = 0
    kept_local: int = 0  # incoming changes not applied because the field was edited here
    review: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {"created": len(self.created), "updated": len(self.updated), "unchanged": self.unchanged, "kept_local": self.kept_local,
                "needs_review": len(self.review), "skipped": len(self.skipped)}


def _label(resource: str, values: dict[str, Any]) -> str:
    return " ".join(x for x in (values.get("first_name"), values.get("last_name")) if x) if resource == "workers" else str(values.get("name") or "")


def _find_lookalike(db: Session, org_id: str, resource: str, incoming: dict[str, Any]) -> tuple[Any, str] | None:
    """A record added by hand that could be the same thing. Only ever used to ask a person."""
    model = MODELS[resource]
    base = select(model).where(model.org_id == org_id, model.deleted_at.is_(None), model.source_system.is_(None))
    if resource == "workers":
        if incoming.get("email"):
            hit = db.scalars(base.where(func.lower(Worker.email) == incoming["email"])).first()
            if hit is not None:
                return hit, "email"
        hit = db.scalars(base.where(func.lower(Worker.first_name + " " + Worker.last_name) == _label(resource, incoming).lower())).first()
        return (hit, "name") if hit is not None else None
    if resource == "suppliers" and incoming.get("abn"):
        hit = db.scalars(base.where(Supplier.abn == incoming["abn"])).first()
        if hit is not None:
            return hit, "abn"
    hit = db.scalars(base.where(func.lower(model.name) == str(incoming["name"]).lower())).first()
    return (hit, "name") if hit is not None else None


def _apply(row: Any, resource: str, incoming: dict[str, Any], *, write: bool) -> tuple[list[str], int]:
    """Bring a linked record up to date. Returns (fields changed, fields kept because edited here)."""
    protected = set(row.locally_edited or [])
    changed, kept = [], 0
    for name in FIELDS[resource]:
        value = incoming.get(name)
        if name in ("first_name", "last_name", "name") and not value and name != "last_name":
            continue
        if name == "last_name":
            value = value or ""
        if getattr(row, name) == value:
            continue
        if name in protected:
            kept += 1
            continue
        changed.append(name)
        if write:
            setattr(row, name, value)
    if write:
        row.source_data = {k: v for k, v in incoming.items() if k != "source_id"}
    return changed, kept


def _create(db: Session, org_id: str, resource: str, system: str, company_id: str, incoming: dict[str, Any], user_id: str | None) -> Any:
    values = {name: incoming.get(name) for name in FIELDS[resource]}
    if resource == "workers":
        values["last_name"] = values["last_name"] or ""
    extra: dict[str, Any] = {}
    if resource == "sites":
        import secrets as _secrets

        extra["scan_token"] = _secrets.token_urlsafe(24)
    row = MODELS[resource](org_id=org_id, source_system=system, source_company=company_id, source_id=incoming["source_id"],
                           source_data={k: v for k, v in incoming.items() if k != "source_id"}, created_by=user_id, **values, **extra)
    db.add(row)
    return row


def run(db: Session, org_id: str, config: IntegrationConfig, resource: str, *, dry_run: bool, user_id: str | None) -> tuple[ImportRun, dict[str, Any]]:
    """Fetch one kind of record for every configured company and (unless it is a dry run) apply it."""
    kind = integrations.CATALOGUE["simpro"]
    settings = integrations.effective_settings(kind, config)
    system = "simulator" if config.mode == "simulator" else "simpro"
    companies = [str(c) for c in (settings.get("company_ids") or [])] or ["0"]
    model = MODELS[resource]
    record = ImportRun(org_id=org_id, provider="simpro", resource=resource, mode=config.mode, dry_run=dry_run, status="failed", started_by=user_id)
    db.add(record)
    db.flush()
    plan = Plan()
    per_company: list[dict[str, Any]] = []
    for company_id in companies:
        try:
            raw = fetch(config, resource, company_id)
        except ProviderError as exc:
            per_company.append({"company_id": company_id, "ok": False, "fetched": 0, "error": str(exc)})
            continue
        per_company.append({"company_id": company_id, "ok": True, "fetched": len(raw), "error": None})
        seen: set[str] = set()
        for item in raw:
            incoming = normalise(resource, item)
            if incoming is None:
                plan.skipped.append({"company_id": company_id, "reason": "no id or name"})
                continue
            if incoming["source_id"] in seen:
                plan.skipped.append({"company_id": company_id, "source_id": incoming["source_id"], "reason": "listed twice by Simpro"})
                continue
            seen.add(incoming["source_id"])
            label = _label(resource, incoming)
            existing = db.scalars(select(model).where(model.org_id == org_id, model.source_system == system,
                                                      model.source_company == company_id, model.source_id == incoming["source_id"])).first()
            if existing is not None:
                if existing.deleted_at is not None:
                    plan.skipped.append({"company_id": company_id, "source_id": incoming["source_id"], "name": label, "reason": "deleted in the portal"})
                    continue
                changed, kept = _apply(existing, resource, incoming, write=not dry_run)
                plan.kept_local += kept
                if changed:
                    plan.updated.append({"id": existing.id, "name": label, "fields": changed})
                else:
                    plan.unchanged += 1
                continue
            item_key = dict(org_id=org_id, resource=resource, source_system=system, source_company=company_id, source_id=incoming["source_id"])
            review = db.scalars(select(ImportReviewItem).filter_by(**item_key)).first()
            if review is not None and review.status == "ignored":
                plan.skipped.append({"company_id": company_id, "source_id": incoming["source_id"], "name": label, "reason": "ignored in the review queue"})
                continue
            lookalike = None if (review is not None and review.status == "created") else _find_lookalike(db, org_id, resource, incoming)
            if lookalike is not None:
                candidate, match_on = lookalike
                plan.review.append({"name": label, "match_on": match_on, "candidate_id": candidate.id})
                if not dry_run:
                    if review is None:
                        db.add(ImportReviewItem(**item_key, run_id=record.id, incoming=incoming, candidate_id=candidate.id, match_on=match_on))
                    elif review.status == "open":
                        review.incoming, review.candidate_id, review.match_on, review.run_id = incoming, candidate.id, match_on, record.id
                continue
            plan.created.append({"name": label, "company_id": company_id})
            if not dry_run:
                _create(db, org_id, resource, system, company_id, incoming, user_id)
    ok = [c for c in per_company if c["ok"]]
    record.companies = per_company
    record.counts = plan.counts()
    record.finished_at = utcnow()
    if not ok:
        record.status = "failed"
        record.error = "; ".join(f"company {c['company_id']}: {c['error']}" for c in per_company)[:400]
    elif dry_run:
        record.status = "previewed"
    else:
        record.status = "committed" if len(ok) == len(per_company) else "partial"
        config.last_sync_at = record.finished_at  # only a committed import moves "last synced"
    return record, {"created": plan.created[:200], "updated": plan.updated[:200], "review": plan.review[:200], "skipped": plan.skipped[:200]}


def link(db: Session, item: ImportReviewItem, candidate: Any) -> None:
    """A person has said the incoming record IS this existing one. Attach the Simpro identity and
    keep every value that currently differs, as if it had been edited here."""
    incoming = dict(item.incoming or {})
    candidate.source_system, candidate.source_company, candidate.source_id = item.source_system, item.source_company, item.source_id
    differing = [name for name in FIELDS[item.resource] if name != "active" and getattr(candidate, name) not in (None, "") and getattr(candidate, name) != incoming.get(name)]
    candidate.locally_edited = sorted(set(candidate.locally_edited or []) | set(differing))
    _apply(candidate, item.resource, incoming, write=True)


def create_from_review(db: Session, item: ImportReviewItem, user_id: str | None) -> Any:
    return _create(db, item.org_id, item.resource, item.source_system, item.source_company, dict(item.incoming or {}), user_id)
