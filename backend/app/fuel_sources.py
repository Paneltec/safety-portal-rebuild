"""Where fuel data comes from: SmartFill (FMT Data) transactions and FuelCheck Tasmania prices (stage 6c).

Neither has been run against the real service from this build: both are tested against a stand-in.
What is known comes from the integration document (sections IV and V):

SmartFill: POST to the API address with {"jsonrpc": "2.0", "method": "Transactions:Read",
"parameters": {...}, "id": 1}. Note "parameters", not the usual "params". The answer may be a
list of records or a table (column names plus rows). Observed limits are 6 calls a minute, 60 an
hour and 600 a day; they are enforced here from a table in the database so every process shares
them. Paging is guarded: a page that brings nothing new ends the run, and the number of pages
is capped, in case the service ignores the paging or date arguments.

FuelCheck: POST the fixed request in business rule B04 and read the AveragePrices entries with
Code "DL" and Period "Month". Each has a Captured date and a Price in cents per litre. The
address is fixed in code; it is never taken from user input.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import fuel, integrations
from .crypto import decrypt_json
from .integrations import ProviderError
from .models import IntegrationConfig, ProviderCall, utcnow

SMARTFILL_LIMITS = ((60, 6, "minute"), (3600, 60, "hour"), (86400, 600, "day"))
PAGE_LENGTH = 100
MAX_PAGES = 30


def reserve_call(db: Session, org_id: str, provider: str = "smartfill") -> None:
    """Count one call against the provider's limits, or refuse it. The caller must commit."""
    now = utcnow()
    for seconds, limit, name in SMARTFILL_LIMITS:
        used = db.scalar(select(func.count()).select_from(ProviderCall).where(ProviderCall.org_id == org_id, ProviderCall.provider == provider,
                                                                              ProviderCall.called_at > now - timedelta(seconds=seconds))) or 0
        if used >= limit:
            raise ProviderError(f"SmartFill may be called {limit} times a {name} and that limit has been reached. Try again later.")
    db.add(ProviderCall(org_id=org_id, provider=provider, called_at=now))
    db.flush()


def calls_left(db: Session, org_id: str, provider: str = "smartfill") -> dict[str, int]:
    now = utcnow()
    return {name: max(0, limit - (db.scalar(select(func.count()).select_from(ProviderCall).where(
        ProviderCall.org_id == org_id, ProviderCall.provider == provider, ProviderCall.called_at > now - timedelta(seconds=seconds))) or 0))
        for seconds, limit, name in SMARTFILL_LIMITS}


def _table(result: Any) -> list[dict[str, Any]]:
    """Records from either shape SmartFill may answer in."""
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if not isinstance(result, dict):
        raise ProviderError("SmartFill answered, but not with transactions in a form this portal understands.")
    columns = result.get("columns") or result.get("fields")
    rows = next((result[k] for k in ("rows", "values", "data") if isinstance(result.get(k), list)), None)
    if isinstance(columns, list) and rows is not None:
        names = [str(c.get("name") if isinstance(c, dict) else c) for c in columns]
        return [dict(zip(names, row)) for row in rows if isinstance(row, (list, tuple))]
    for key in ("data", "result", "transactions"):
        if key in result and result[key] is not result:
            return _table(result[key])
    raise ProviderError("SmartFill answered, but not with transactions in a form this portal understands.")


def canonical(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for heading, value in record.items():
        name = fuel._LOOKUP.get(fuel._key(str(heading)))
        if name is not None and name not in out and value not in (None, ""):
            out[name] = value
    return out


def _simulated_fills(today: date) -> list[dict[str, Any]]:
    rows = []
    plan = [(9, "07:15", "SIM-CARD-1", "Sim Ute 01", 52.4, 48320, "Diesel"), (8, "06:50", "SIM-CARD-2", "Sim Tipper 02", 180.0, 131950, "Diesel"),
            (6, "07:05", "SIM-CARD-1", "Sim Ute 01", 49.8, 48890, "Diesel"), (5, "22:40", "SIM-CARD-2", "Sim Tipper 02", 175.5, 132400, "Diesel"),
            (3, "07:20", "SIM-CARD-9", "Sim spare card", 61.0, None, "Diesel"), (2, "12:10", "SIM-CARD-1", "Sim Ute 01", 12.5, None, "ULP 91"),
            (1, "07:00", "SIM-CARD-1", "Sim Ute 01", 51.1, 49410, "Diesel")]
    for i, (days_ago, clock, card, description, litres, odometer, kind) in enumerate(plan, start=1):
        day = today - timedelta(days=days_ago)
        rows.append({"transaction_id": f"SIM-{day.isoformat()}-{i}", "date": day.strftime("%d/%m/%Y"), "time": clock, "card_number": card, "description": description,
                     "location": "Sim Depot Tank", "litres": litres, "fuel_type": kind, "odometer_km": odometer})
    return rows


def smartfill_fetch(db: Session, org_id: str, config: IntegrationConfig, since: datetime, until: datetime, today: date) -> tuple[list[dict[str, Any]], list[str]]:
    """Fills between two instants, as canonical rows, plus anything the reader should be told."""
    if config.mode == "simulator":
        return _simulated_fills(today), []
    settings = integrations.effective_settings(integrations.CATALOGUE["smartfill"], config)
    secret = decrypt_json(config.secrets)["api_secret"]
    url = settings.get("api_url") or "https://fmtdata.com/API/api.php"
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    warnings: list[str] = []
    for page in range(MAX_PAGES):
        reserve_call(db, org_id)
        body = {"jsonrpc": "2.0", "method": "Transactions:Read", "id": 1,
                "parameters": {"clientReference": settings["api_key"], "clientSecret": secret, "range": {"offset": page * PAGE_LENGTH, "length": PAGE_LENGTH},
                               "From Timestamp": since.strftime("%Y-%m-%d %H:%M:%S"), "To Timestamp": until.strftime("%Y-%m-%d %H:%M:%S")}}
        data = integrations._json(integrations.call("POST", url, provider="SmartFill", secrets=[secret], json=body), "SmartFill")
        if not isinstance(data, dict) or data.get("error"):
            raise ProviderError("SmartFill refused the request for transactions. Check the client reference and secret, and that this account may read transactions.")
        records = [canonical(r) for r in _table(data.get("result", data))]
        fresh = 0
        for record in records:
            key = str(record.get("transaction_id") or f"{record.get('date')}|{record.get('time')}|{record.get('datetime')}|{record.get('card_number')}|{record.get('litres')}")
            if key not in seen:
                seen.add(key)
                out.append(record)
                fresh += 1
        if len(records) < PAGE_LENGTH:
            return out, warnings
        if fresh == 0:
            warnings.append("SmartFill kept returning the same page, so reading stopped there. Check the result against SmartFill's own report.")
            return out, warnings
    warnings.append(f"SmartFill returned more than {MAX_PAGES * PAGE_LENGTH} transactions for this period. Only the first {MAX_PAGES * PAGE_LENGTH} were read; import a shorter period.")
    return out, warnings


def fuelcheck_fetch(config: IntegrationConfig | None, today: date) -> tuple[dict[date, Decimal], list[str]]:
    """Daily diesel (DL) observations by date, in cents per litre."""
    if config is None or config.mode == "off":
        raise ProviderError("FuelCheck is switched off. Switch it on under Settings, Connections, or enter the week's seven daily prices by hand.")
    if config.mode == "simulator":
        return {today - timedelta(days=n): Decimal(195 + ((today - timedelta(days=n)).toordinal() % 9)) + Decimal("0.5") for n in range(1, 43)}, []
    data = integrations._json(integrations.call("POST", fuel.FUELCHECK_URL, provider="FuelCheck", secrets=[], json=fuel.FUELCHECK_BODY), "FuelCheck")
    entries = data.get("AveragePrices") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ProviderError("FuelCheck answered, but not in the expected format, so no prices were read.")
    found: dict[date, Decimal] = {}
    clashes: set[date] = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("Code") != "DL" or entry.get("Period") != "Month":
            continue
        try:
            day = date.fromisoformat(str(entry.get("Captured"))[:10])
            price = Decimal(str(entry.get("Price")))
        except (ValueError, InvalidOperation):
            continue
        if not price.is_finite() or price < 50 or price > 500:
            continue  # not a believable cents-per-litre figure
        if day in found and found[day] != price:
            clashes.add(day)
        found[day] = price
    for day in clashes:
        del found[day]
    warnings = [f"FuelCheck gave two different prices for {d.strftime('%d/%m/%Y')}, so that day was left out." for d in sorted(clashes)]
    return found, warnings
