"""Fuel: the weekly diesel rate, importing fills, attribution and anomaly rules (stage 6c).

Weekly diesel rate (business rule B04), in the organisation's timezone:
  * a week runs Monday to Sunday;
  * the rate for a week comes from the seven daily diesel (code DL) observations of the week
    before: rate = round_half_up(sum of the seven cents-per-litre figures / 700, 4 places);
  * all seven days must be present. If any is missing the week has no rate, its fills stay
    "pending", and totals say how many litres are unpriced. Nothing is estimated;
  * a rate is written once. Later data from the source never changes it, and there is no way to
    edit or delete it;
  * a fill's reporting cost = round_half_up(litres x rate, 2 places), written once.
Only diesel is priced. A fill with no fuel label is treated as diesel (this business uses diesel
only); a fill explicitly labelled as another fuel is not priced and is flagged for review.

The supplier's own price or invoice amount on a fill is kept as evidence and never replaces the
reporting cost.
"""
import csv
import hashlib
import io
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import Asset, FuelAnomaly, FuelBatch, FuelCard, FuelTransaction, FuelWeeklyPrice, Organisation, utcnow

FUELCHECK_URL = "https://www.fuelcheck.tas.gov.au/fuel/api/v1/fuel/prices/trends"
FUELCHECK_PAGE = "https://www.fuelcheck.tas.gov.au/app/FuelPrice/Trend?fuelTypeCode=DL"
FUELCHECK_BODY = {"fueltypes": [{"code": "DL"}], "location": {"latitude": -42.8666632, "longitude": 147.3166654}}
CENT, RATE = Decimal("0.01"), Decimal("0.0001")
MAX_LITRES = Decimal("5000")
ROLLING_WINDOW, ROLLING_MIN_PRIOR = 20, 5

RULES: dict[str, tuple[str, str]] = {  # rule -> (severity, plain label)
    "capacity_exceed": ("high", "More fuel than the tank holds"),
    "unlinked": ("medium", "Not matched to a vehicle"),
    "stat_spike": ("medium", "Much more fuel than usual"),
    "reading_regress": ("medium", "A meter reading went backwards"),
    "other_fuel": ("low", "Not diesel"),
    "missing_odometer": ("low", "No odometer reading"),
    "unusual_hour": ("low", "Filled at an unusual time"),
}
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# Column headings seen in fuel exports, reduced to lower-case letters and digits.
ALIASES: dict[str, list[str]] = {
    "transaction_id": ["transactionid", "transid", "transactionnumber", "txnid"], "date": ["date"], "time": ["time"], "datetime": ["datetime", "timestamp"],
    "card_number": ["cardnumber", "card", "keycode", "keyorcode", "key", "code", "fobcode"], "description": ["description", "vehicle", "name"],
    "registration": ["registration", "rego", "regoserial"], "location": ["from", "site", "location", "depot", "fromsite"],
    "litres": ["litres", "liters", "quantity", "qty", "volume"], "fuel_type": ["fueltype", "fuel"], "odometer_km": ["odometer", "odometerkm", "odo", "km"],
    "engine_hours": ["enginehours", "hours"], "driver": ["driver", "drivername", "driverauthorisation", "drivernamefull"],
    "total_price": ["totalprice", "price", "cost", "total", "amount", "value"], "unit_price": ["unitprice", "priceperlitre", "pricelitre", "priceperliter", "dollarsperlitre"],
}
_LOOKUP = {alias: canonical for canonical, aliases in ALIASES.items() for alias in aliases}


# ---------- numbers and weeks ----------

def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def source_week(week_start: date) -> list[date]:
    """The seven dates, Monday to Sunday, whose observations set the rate for the week starting `week_start`."""
    return [week_start - timedelta(days=7 - i) for i in range(7)]


def compute_rate(cents: list[Decimal]) -> Decimal:
    if len(cents) != 7:
        raise ValueError("a weekly rate needs exactly seven daily observations")
    return (sum(cents, Decimal("0")) / Decimal("700")).quantize(RATE, rounding=ROUND_HALF_UP)


def is_diesel(label: str | None) -> bool:
    text = re.sub(r"[^a-z]", " ", (label or "").lower()).split()
    return not text or any(word in ("diesel", "distillate", "dl", "ulsd", "adf") for word in text)


def org_tz(org: Organisation) -> ZoneInfo:
    try:
        return ZoneInfo(org.timezone or "Australia/Hobart")
    except Exception:
        return ZoneInfo("Australia/Hobart")


def settings(org: Organisation) -> dict[str, Any]:
    raw = (org.details or {}).get("fuel") or {}
    start = None
    try:
        start = date.fromisoformat(raw["weekly_start"]) if raw.get("weekly_start") else None
    except ValueError:
        start = None
    return {"weekly_start": start}


def decimal(value: Any, label: str, *, low: Decimal, high: Decimal, allow_zero: bool = False) -> Decimal | None:
    """A finite number in range, or None when blank. Raises ValueError with words a person can act on."""
    if value is None or str(value).strip() == "":
        return None
    try:
        number = Decimal(str(value).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        raise ValueError(f"{label} is not a number") from None
    if not number.is_finite():
        raise ValueError(f"{label} is not a usable number")
    if number < low or number > high or (number == 0 and not allow_zero and low == 0):
        raise ValueError(f"{label} must be more than {low} and at most {high:,}" if not allow_zero else f"{label} must be between {low} and {high:,}")
    return number


# ---------- the weekly rate ----------

def get_price(db: Session, org_id: str, week_start: date, simulated: bool) -> FuelWeeklyPrice | None:
    return db.scalars(select(FuelWeeklyPrice).where(FuelWeeklyPrice.org_id == org_id, FuelWeeklyPrice.week_start == week_start, FuelWeeklyPrice.simulated == simulated)).first()


def lock_week(db: Session, org: Organisation, week_start: date, observations: dict[date, Decimal], *, source: str, source_url: str | None, source_note: str | None,
              fetched_at: datetime | None, actor_id: str | None) -> tuple[FuelWeeklyPrice | None, str]:
    """Fix the rate for one week if all seven source days are present and it is not fixed already.

    Returns (row, outcome): "locked", "already_locked" (the existing row, untouched) or "missing_days".
    The insert is atomic, so two requests at once cannot produce two rates for the same week.
    """
    simulated = source == "simulator"
    existing = get_price(db, org.id, week_start, simulated)
    if existing is not None:
        return existing, "already_locked"
    days = source_week(week_start)
    if any(d not in observations for d in days):
        return None, "missing_days"
    cents = [observations[d] for d in days]
    values = {"org_id": org.id, "week_start": week_start, "week_end": week_start + timedelta(days=6), "source_week_start": days[0], "source_week_end": days[6],
              "daily_cents": [{"date": d.isoformat(), "price": format(observations[d].normalize(), "f")} for d in days], "rate": compute_rate(cents), "fuel_code": "DL", "currency": "AUD",
              "timezone": org.timezone or "Australia/Hobart", "source": source, "source_url": source_url, "source_note": source_note, "simulated": simulated,
              "fetched_at": fetched_at, "locked_at": utcnow(), "locked_by": actor_id}
    from .models import new_id
    result = db.execute(pg_insert(FuelWeeklyPrice).values(id=new_id(), **values).on_conflict_do_nothing(constraint="uq_fuel_weekly_prices"))
    row = get_price(db, org.id, week_start, simulated)
    return row, "locked" if result.rowcount else "already_locked"


def price_fill(txn: FuelTransaction, price: FuelWeeklyPrice) -> bool:
    """Write the reporting rate and cost on a fill, once. Never overwrites."""
    if txn.report_rate is not None or not txn.is_diesel:
        return False
    txn.weekly_price_id, txn.report_rate, txn.report_cost = price.id, price.rate, money(txn.litres * price.rate)
    return True


def pricing_state(txn: FuelTransaction, weekly_start: date | None) -> str:
    if txn.report_rate is not None:
        return "priced"
    if not txn.is_diesel:
        return "not_diesel"
    if weekly_start is not None and txn.local_date < weekly_start:
        return "before_weekly_pricing"
    return "pending"


def price_pending(db: Session, org: Organisation, *, week_start: date | None = None) -> int:
    """Give every waiting diesel fill its week's rate where that week has one."""
    weekly_start = settings(org)["weekly_start"]
    query = select(FuelTransaction).where(FuelTransaction.org_id == org.id, FuelTransaction.is_diesel.is_(True), FuelTransaction.report_rate.is_(None))
    if week_start is not None:
        query = query.where(FuelTransaction.local_date >= week_start, FuelTransaction.local_date <= week_start + timedelta(days=6))
    if weekly_start is not None:
        query = query.where(FuelTransaction.local_date >= weekly_start)
    prices: dict[tuple[date, bool], FuelWeeklyPrice | None] = {}
    done = 0
    for txn in db.scalars(query).all():
        key = (monday(txn.local_date), txn.source_system == "simulator")
        if key not in prices:
            prices[key] = get_price(db, org.id, *key)
        if prices[key] is not None and price_fill(txn, prices[key]):
            done += 1
    return done


# ---------- reading a spreadsheet export ----------

def _key(heading: str) -> str:
    return re.sub(r"[^a-z0-9]", "", heading.lower())


def map_columns(headings: list[str]) -> tuple[dict[str, str], list[str]]:
    """canonical name -> heading as written, and the headings that were not recognised."""
    mapping: dict[str, str] = {}
    unknown = []
    for heading in headings:
        canonical = _LOOKUP.get(_key(heading))
        if canonical is None:
            unknown.append(heading)
        elif canonical not in mapping:
            mapping[canonical] = heading
    return mapping, unknown


def read_csv(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headings = [h.strip() for h in (reader.fieldnames or []) if h and h.strip()]
    rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items() if k} for row in reader]
    return headings, [r for r in rows if any(r.values())]


_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%d %b %Y")
_TIME_FORMATS = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p")


def parse_when(date_text: str, time_text: str, stamp_text: str, tz: ZoneInfo) -> datetime:
    """An instant from the export's date and time. A time with no offset is read in `tz`, which
    the import records; a value carrying its own offset keeps it."""
    if stamp_text:
        try:
            stamp = datetime.fromisoformat(stamp_text.replace("Z", "+00:00"))
            return stamp if stamp.tzinfo else stamp.replace(tzinfo=tz)
        except ValueError:
            parts = stamp_text.split(None, 1)
            date_text, time_text = parts[0], parts[1] if len(parts) > 1 else ""
    day = None
    for fmt in _DATE_FORMATS:
        try:
            day = datetime.strptime(date_text, fmt).date()
            break
        except ValueError:
            continue
    if day is None:
        raise ValueError(f"the date '{date_text}' is not understood (use 31/12/2026 or 2026-12-31)")
    clock = None
    for fmt in _TIME_FORMATS:
        try:
            clock = datetime.strptime(time_text.upper(), fmt).time()
            break
        except ValueError:
            continue
    if clock is None:
        if time_text:
            raise ValueError(f"the time '{time_text}' is not understood")
        raise ValueError("there is no time of day, which is needed to tell fills apart")
    return datetime.combine(day, clock, tzinfo=tz)


def normalise(raw: dict[str, Any], tz: ZoneInfo, local: ZoneInfo, now: datetime) -> dict[str, Any]:
    """One incoming row (canonical names) as a fill. Raises ValueError saying what is wrong with it."""
    litres = decimal(raw.get("litres"), "Litres", low=Decimal("0"), high=MAX_LITRES)
    if litres is None:
        raise ValueError("there are no litres")
    when = parse_when(str(raw.get("date") or ""), str(raw.get("time") or ""), str(raw.get("datetime") or ""), tz)
    if when > now + timedelta(hours=1):
        raise ValueError("the date is in the future")
    here = when.astimezone(local)
    card = str(raw.get("card_number") or "").strip() or None
    odometer = decimal(raw.get("odometer_km"), "Odometer", low=Decimal("0"), high=Decimal("5000000"), allow_zero=True)
    hours = decimal(raw.get("engine_hours"), "Engine hours", low=Decimal("0"), high=Decimal("500000"), allow_zero=True)
    total = decimal(raw.get("total_price"), "Total price", low=Decimal("0"), high=Decimal("1000000"), allow_zero=True)
    unit = decimal(raw.get("unit_price"), "Unit price", low=Decimal("0"), high=Decimal("100"), allow_zero=True)
    source_id = str(raw.get("transaction_id") or "").strip()
    derived = not source_id
    if derived:  # no provider id in the export: a stable key from what identifies the fill
        basis = "|".join([when.astimezone(timezone.utc).isoformat(), card or "", str(litres.normalize()), str(raw.get("location") or "").strip().lower()])
        source_id = "derived:" + hashlib.sha256(basis.encode()).hexdigest()[:32]
    fuel_type = str(raw.get("fuel_type") or "").strip() or None
    return {"source_id": source_id[:120], "derived_id": derived, "occurred_at": when.astimezone(timezone.utc), "local_date": here.date(), "local_time": here.strftime("%H:%M"),
            "litres": litres.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), "fuel_type": fuel_type[:60] if fuel_type else None, "is_diesel": is_diesel(fuel_type),
            "source_unit_price": unit, "source_total": money(total) if total is not None else None,
            "odometer_km": float(odometer) if odometer is not None else None, "engine_hours": float(hours) if hours is not None else None, "card_number": card[:80] if card else None,
            "driver_name": str(raw.get("driver") or "").strip()[:160] or None, "location": str(raw.get("location") or "").strip()[:200] or None,
            "description": str(raw.get("description") or "").strip()[:200] or None, "registration": str(raw.get("registration") or "").strip().upper()[:40] or None}


# ---------- attribution ----------

def _rego(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


class Matcher:
    """Looks up the vehicle for a fill: first an exact registration (a direct match), then the
    fuel card's assignment (an inference, and labelled as one)."""

    def __init__(self, db: Session, org_id: str):
        self.db, self.org_id = db, org_id
        assets = db.scalars(select(Asset).where(Asset.org_id == org_id, Asset.deleted_at.is_(None), Asset.registration.is_not(None))).all()
        by_rego: dict[str, list[Asset]] = {}
        for a in assets:
            by_rego.setdefault(_rego(a.registration), []).append(a)
        self.by_rego = {k: v[0] for k, v in by_rego.items() if k and len(v) == 1}  # two assets with one registration: not a match
        self.cards = {c.card_number: c for c in db.scalars(select(FuelCard).where(FuelCard.org_id == org_id)).all()}

    def card(self, number: str | None, label: str | None, simulated: bool, *, create: bool) -> FuelCard | None:
        if not number:
            return None
        card = self.cards.get(number)
        if card is None and create:
            card = FuelCard(org_id=self.org_id, card_number=number, label=label, simulated=simulated)
            self.db.add(card)
            self.cards[number] = card
        return card

    def apply(self, txn: FuelTransaction, *, create_cards: bool = True) -> None:
        card = self.card(txn.card_number, txn.description, txn.source_system == "simulator", create=create_cards)
        if txn.attribution == "manual":
            return
        direct = self.by_rego.get(_rego(txn.registration)) if txn.registration else None
        if direct is not None:
            txn.asset_id, txn.attribution = direct.id, "direct"
            txn.worker_id = card.worker_id if card else None
        elif card is not None and (card.asset_id or card.worker_id):
            txn.asset_id, txn.worker_id, txn.attribution = card.asset_id, card.worker_id, "card"
        else:
            txn.asset_id, txn.worker_id, txn.attribution = None, None, "unlinked"


# ---------- anomaly rules ----------

def detect(db: Session, txn: FuelTransaction, asset: Asset | None) -> dict[str, list[dict[str, Any]]]:
    """rule -> evidence items for one fill, judged against what was known at the time of the fill."""
    found: dict[str, list[dict[str, Any]]] = {}
    hour = int(txn.local_time[:2])
    if hour < 5 or hour >= 19:
        found["unusual_hour"] = [{"text": f"Filled at {txn.local_time}, outside 5 am to 7 pm", "local_time": txn.local_time}]
    if not txn.is_diesel:
        found["other_fuel"] = [{"text": f"Labelled '{txn.fuel_type}'. Only diesel is priced, so this fill has no reporting cost", "fuel_type": txn.fuel_type}]
    if asset is None or txn.asset_id is None:
        found["unlinked"] = [{"text": f"Card {txn.card_number} is not assigned to a vehicle" if txn.card_number else "No card or registration to match it by", "card_number": txn.card_number}]
        return found
    litres = float(txn.litres)
    if asset.tank_capacity_l and litres > 1.10 * asset.tank_capacity_l:
        found["capacity_exceed"] = [{"text": f"{litres:,.1f} litres into a {asset.tank_capacity_l:,.0f} litre tank", "litres": litres, "tank_capacity_l": asset.tank_capacity_l}]
    earlier = db.scalars(select(FuelTransaction).where(FuelTransaction.asset_id == asset.id, FuelTransaction.occurred_at < txn.occurred_at, FuelTransaction.id != txn.id)
                         .order_by(FuelTransaction.occurred_at.desc()).limit(ROLLING_WINDOW)).all()
    sample = [float(t.litres) for t in earlier]
    if len(sample) >= ROLLING_MIN_PRIOR:
        mean, spread = statistics.fmean(sample), statistics.stdev(sample)
        if spread > 0 and litres > mean + 2 * spread:
            found["stat_spike"] = [{"text": f"{litres:,.1f} litres; the last {len(sample)} fills averaged {mean:,.1f}", "litres": litres, "usual": round(mean, 1), "fills_compared": len(sample)}]
    regress = []
    for field, label, unit in (("odometer_km", "Odometer", "km"), ("engine_hours", "Hour meter", "hours")):
        now_value = getattr(txn, field)
        before = next((t for t in earlier if (getattr(t, field) or 0) > 0), None)
        if now_value and before is not None and now_value < getattr(before, field):
            regress.append({"text": f"{label} {now_value:,.0f} {unit}, but it read {getattr(before, field):,.0f} at the fill on {before.local_date.strftime('%d/%m/%Y')}",
                            "meter": field, "reading": now_value, "previous": getattr(before, field), "previous_date": before.local_date.isoformat()})
    if regress:
        found["reading_regress"] = regress
    if not txn.odometer_km and any((t.odometer_km or 0) > 0 for t in earlier):
        found["missing_odometer"] = [{"text": "No odometer reading was entered, although earlier fills for this vehicle have one"}]
    return found


def sync_anomalies(db: Session, txn: FuelTransaction, found: dict[str, list[dict[str, Any]]]) -> int:
    """Bring a fill's flags into line with the rules. A reason appears once per fill; running this
    again updates it. An open reason that no longer applies is closed with a note saying so. A
    reason a person already resolved or dismissed is left as they left it. Returns new flags."""
    existing = {a.rule: a for a in db.scalars(select(FuelAnomaly).where(FuelAnomaly.transaction_id == txn.id)).all()}
    created = 0
    for rule, evidence in found.items():
        row = existing.get(rule)
        if row is None:
            db.add(FuelAnomaly(org_id=txn.org_id, transaction_id=txn.id, rule=rule, severity=RULES[rule][0], evidence=evidence))
            created += 1
        elif row.status == "open":
            row.evidence = evidence
    for rule, row in existing.items():
        if rule not in found and row.status == "open":
            row.status, row.note, row.acted_at, row.acted_by = "resolved", "Corrected: this no longer applies to the fill.", utcnow(), None
    return created


# ---------- import ----------

UPDATABLE = ("fuel_type", "source_unit_price", "source_total", "odometer_km", "engine_hours", "card_number", "driver_name", "location", "description", "registration")


def import_rows(db: Session, org: Organisation, rows: list[tuple[int, dict[str, Any]]], *, source_system: str, batch: FuelBatch, dry_run: bool) -> dict[str, int]:
    """Upsert fills on the provider's transaction id. `rows` are (row number, canonical values)."""
    counts = {"rows": len(rows), "new": 0, "updated": 0, "unchanged": 0, "errors": 0, "conflicts": 0, "derived_ids": 0, "new_flags": 0, "priced": 0, "pending_price": 0, "not_diesel": 0}
    errors: list[dict[str, Any]] = []
    tz_in, local, now = ZoneInfo(batch.timezone) if batch.timezone else org_tz(org), org_tz(org), utcnow()
    matcher = Matcher(db, org.id)
    weekly_start = settings(org)["weekly_start"]
    seen: set[str] = set()
    touched: list[FuelTransaction] = []
    for number, raw in rows:
        try:
            values = normalise(raw, tz_in, local, now)
        except ValueError as exc:
            counts["errors"] += 1
            if len(errors) < 50:
                errors.append({"row": number, "error": str(exc)})
            continue
        if values["source_id"] in seen:
            counts["unchanged"] += 1  # the same fill twice in one file
            continue
        seen.add(values["source_id"])
        derived = values.pop("derived_id")
        counts["derived_ids"] += derived
        txn = db.scalars(select(FuelTransaction).where(FuelTransaction.org_id == org.id, FuelTransaction.source_system == source_system,
                                                       FuelTransaction.source_id == values["source_id"])).first()
        if txn is None:
            counts["new"] += 1
            if dry_run:
                continue
            txn = FuelTransaction(org_id=org.id, source_system=source_system, batch_id=batch.id, raw={k: str(v) for k, v in raw.items() if v not in (None, "")}, **values)
            db.add(txn)
            db.flush()
        else:
            # Fill gaps only. A value already on file is kept, and a different incoming one is counted, not applied.
            filled, clash = [], False
            for field in UPDATABLE:
                incoming, current = values[field], getattr(txn, field)
                if incoming is None or incoming == current:
                    continue
                if current is None:
                    filled.append(field)
                else:
                    clash = True
            counts["conflicts"] += clash
            counts["updated" if filled else "unchanged"] += 1
            if dry_run or not filled:
                continue
            for field in filled:
                setattr(txn, field, values[field])
            if "fuel_type" in filled and txn.report_rate is None:
                txn.is_diesel = values["is_diesel"]
        matcher.apply(txn)
        touched.append(txn)
    if not dry_run:
        db.flush()
        prices: dict[tuple[date, bool], FuelWeeklyPrice | None] = {}
        assets = {a.id: a for a in db.scalars(select(Asset).where(Asset.id.in_({t.asset_id for t in touched if t.asset_id}))).all()} if touched else {}
        for txn in sorted(touched, key=lambda t: t.occurred_at):
            if txn.is_diesel and txn.report_rate is None and not (weekly_start and txn.local_date < weekly_start):
                key = (monday(txn.local_date), source_system == "simulator")
                if key not in prices:
                    prices[key] = get_price(db, org.id, *key)
                if prices[key] is not None:
                    price_fill(txn, prices[key])
            state = pricing_state(txn, weekly_start)
            if state != "before_weekly_pricing":
                counts["priced" if state == "priced" else "not_diesel" if state == "not_diesel" else "pending_price"] += 1
            counts["new_flags"] += sync_anomalies(db, txn, detect(db, txn, assets.get(txn.asset_id or "")))
    batch.counts, batch.errors = counts, errors
    return counts


def redetect(db: Session, txns: list[FuelTransaction]) -> None:
    assets = {a.id: a for a in db.scalars(select(Asset).where(Asset.id.in_({t.asset_id for t in txns if t.asset_id}))).all()} if txns else {}
    for txn in txns:
        sync_anomalies(db, txn, detect(db, txn, assets.get(txn.asset_id or "")))


def open_flag_count(db: Session, org_id: str) -> int:
    return db.scalar(select(func.count(func.distinct(FuelAnomaly.transaction_id))).where(FuelAnomaly.org_id == org_id, FuelAnomaly.status == "open")) or 0
