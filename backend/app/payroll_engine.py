"""The payroll calculation rules, as pure functions with no database (business rules B08 and B09).

These reproduce the behaviour described in the reconstruction pack and its worked examples. They
are the business's own configured rules as observed, not a ruling on what any award, tax table
or super law requires. Nothing here pays anyone or lodges anything.

Part 1 (this file, stage 7b): turning clock times into hours by category.

  For each paid minute, the first of these that applies decides its category. They never stack:
    1. the shift is marked as work on a public holiday      -> holiday_work
    2. the minute falls in the night window on a weekday,
       or continues a shift that started on a weekday       -> night
    3. the minute is on a Sunday                            -> sunday
    4. the minute is on a Saturday                          -> saturday
    5. otherwise by how many paid minutes the day already
       has: up to the daily ordinary hours                  -> ordinary
       the next band                                        -> ot1
       beyond that                                          -> ot2
  Paid minutes belong to the date the shift started on. With `separate_weekends` off, Saturday
  counts as ot1 and Sunday as ot2.

  `penalty_ordinary` is a second measure, not extra paid time: holiday_work minutes inside the
  daily ordinary band, and night minutes inside it when the shift is marked as replacing a day
  shift. It is used later for leave accrual and super.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

RULE_VERSION = "AU-weekly-2026-07-01-v4"
DEFAULT_RULES: dict[str, Any] = {"ot1_multiplier": 1.5, "ot2_multiplier": 2, "night_multiplier": 2, "holiday_work_multiplier": 2.5, "super_percent": 12, "meal_allowance": 20,
                                 "meal_tax_treatment": "taxable", "daily_ordinary_hours": 7.6, "ot1_hours": 2, "night_start": "18:00", "night_end": "06:00", "earning_rules": []}
BUCKETS = ("ordinary", "ot1", "ot2", "night", "holiday_work", "saturday", "sunday")
BREAK_ZONE_ERROR = "Enter the unpaid break start so day/night rates can be allocated correctly"
OVERLAP_ERROR = "Shifts overlap; correct the time entries"


class EngineError(ValueError):
    """Something in the input a person has to fix. The message is safe to show."""


def _clock(value: Any, label: str) -> int:
    try:
        parsed = datetime.strptime(str(value), "%H:%M")
    except ValueError:
        raise EngineError(f"{label} must be a time like 07:30") from None
    return parsed.hour * 60 + parsed.minute


def _day(value: Any) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise EngineError("Each shift needs a date like 2026-10-19") from None


@dataclass
class _Shift:
    day: date
    start: int       # minutes from midnight of `day`
    finish: int      # may exceed 1440 for an overnight shift
    break_minutes: int
    break_start: int | None
    public_holiday: bool
    replacement_day_shift: bool


def _parse(raw: dict[str, Any]) -> _Shift:
    day = _day(raw.get("date"))
    start, finish = _clock(raw.get("start"), "Start"), _clock(raw.get("finish"), "Finish")
    next_day = bool(raw.get("next_day"))
    if next_day:
        finish += 1440
    elif finish <= start:
        raise EngineError("Finish must be after start. For a shift that ends after midnight, mark it as finishing the next day")
    duration = finish - start
    if duration <= 0 or duration > 1440:
        raise EngineError("A shift must be longer than nothing and no longer than 24 hours")
    try:
        unpaid = int(raw.get("break_minutes") or 0)
    except (TypeError, ValueError):
        raise EngineError("The unpaid break must be a whole number of minutes") from None
    if unpaid < 0 or unpaid >= duration:
        raise EngineError("The unpaid break must be shorter than the shift")
    break_start = None
    if raw.get("break_start") not in (None, ""):
        break_start = _clock(raw.get("break_start"), "Break start")
        if break_start < start:
            break_start += 1440  # a break after midnight on an overnight shift
        if unpaid and (break_start < start or break_start + unpaid > finish):
            raise EngineError("The unpaid break must fall inside the shift")
    return _Shift(day, start, finish, unpaid, break_start, bool(raw.get("public_holiday")), bool(raw.get("replacement_day_shift")))


def _night(rules: dict[str, Any]) -> tuple[int, int]:
    start, end = _clock(rules.get("night_start", "18:00"), "Night start"), _clock(rules.get("night_end", "06:00"), "Night end")
    if start == end:
        raise EngineError("The night window cannot start and end at the same time")
    return start, end


def _zone(shift: _Shift, minute: int, night: tuple[int, int]) -> str:
    """The rate zone of one minute of a shift (before ordinary/overtime banding)."""
    if shift.public_holiday:
        return "holiday_work"
    actual = shift.day + timedelta(days=minute // 1440)
    clock = minute % 1440
    start, end = night
    in_night = (start <= clock or clock < end) if start > end else (start <= clock < end)
    if in_night and (actual.weekday() < 5 or shift.day.weekday() < 5):
        return "night"
    if actual.weekday() == 6:
        return "sunday"
    if actual.weekday() == 5:
        return "saturday"
    return "day"


def allocate_shifts(shifts: list[dict[str, Any]], rules: dict[str, Any] | None = None, *, separate_weekends: bool = True) -> dict[str, Any]:
    """Hours by category for a set of shifts. Raises EngineError when the times cannot be used."""
    rules = {**DEFAULT_RULES, **(rules or {})}
    night = _night(rules)
    ordinary_cap = int(round(float(rules["daily_ordinary_hours"]) * 60))
    ot1_cap = ordinary_cap + int(round(float(rules["ot1_hours"]) * 60))
    parsed = sorted((_parse(s) for s in shifts), key=lambda s: (s.day, s.start))
    # No minute may be covered twice, counting the whole span of each shift, breaks included.
    spans = sorted((datetime.combine(s.day, datetime.min.time()) + timedelta(minutes=s.start), datetime.combine(s.day, datetime.min.time()) + timedelta(minutes=s.finish)) for s in parsed)
    for (_, earlier_end), (later_start, _) in zip(spans, spans[1:]):
        if later_start < earlier_end:
            raise EngineError(OVERLAP_ERROR)
    minutes = {b: 0 for b in BUCKETS}
    penalty_ordinary = 0
    by_day: dict[date, int] = {}
    for shift in parsed:
        unpaid: set[int] = set()
        if shift.break_minutes:
            if shift.break_start is not None:
                unpaid = set(range(shift.break_start, shift.break_start + shift.break_minutes))
            else:
                if len({_zone(shift, m, night) for m in range(shift.start, shift.finish)}) > 1:
                    raise EngineError(BREAK_ZONE_ERROR)
                unpaid = set(range(shift.finish - shift.break_minutes, shift.finish))  # one zone: the break is taken off the end
        for minute in range(shift.start, shift.finish):
            if minute in unpaid:
                continue
            index = by_day.get(shift.day, 0)
            by_day[shift.day] = index + 1
            zone = _zone(shift, minute, night)
            if zone == "day":
                zone = "ordinary" if index < ordinary_cap else "ot1" if index < ot1_cap else "ot2"
            elif zone == "saturday" and not separate_weekends:
                zone = "ot1"
            elif zone == "sunday" and not separate_weekends:
                zone = "ot2"
            minutes[zone] += 1
            if index < ordinary_cap and (zone == "holiday_work" or (zone == "night" and shift.replacement_day_shift)):
                penalty_ordinary += 1
    hours = lambda m: float((Decimal(m) / Decimal(60)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))  # noqa: E731
    out: dict[str, Any] = {b: hours(minutes[b]) for b in ("ordinary", "ot1", "ot2", "night", "holiday_work")}
    out["penalty_ordinary"] = hours(penalty_ordinary)
    out["saturday"], out["sunday"] = hours(minutes["saturday"]), hours(minutes["sunday"])
    out["meal_count"] = sum((1 if paid >= 600 else 0) + (1 if paid >= 840 else 0) for paid in by_day.values())
    return out


def paid_hours(buckets: dict[str, Any]) -> Decimal:
    return sum((Decimal(str(buckets.get(b, 0))) for b in BUCKETS), Decimal("0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------------------------
# Part 2 (stage 7c): hours into money (business rule B09).
#
#   * Decimal throughout; every pay line is rounded half-up to cents, then the lines are added.
#   * Gross is earnings only. Reimbursements and after-tax deductions are separate.
#   * Withholding is worked out only when the tax declaration has been reviewed and the payday is
#     inside the dates the built-in table covers. Otherwise it is unknown (None), with a reason:
#     it is never guessed, and net pay is then unknown too.
#   * Super is worked out on qualifying earnings, which is not the same as gross: overtime is left
#     out. Where it cannot be decided without a person (LAFHA not classified; a mix of ordinary
#     and overtime penalty hours) it is unknown, with a reason.
#   * Any reason listed in `issues` means the row is not ready to finalise.
#
# The table below is what the baseline software used for paydays from 1 July 2026 to 30 June 2027.
# It has not been checked against the Tax Office's published schedule in this build and must be
# verified before any real pay is calculated with it.
# ---------------------------------------------------------------------------------------------

TAX_SOURCE = "https://www.legislation.gov.au/F2026L00716/asmade/text"
TAX_FROM, TAX_UNTIL = date(2026, 7, 1), date(2027, 7, 1)  # payday on or after, and before
SCALES: dict[str, list[tuple[int | None, str, str]]] = {
    "resident_threshold": [(362, "0", "0"), (538, ".1500", "54.3462"), (673, ".2500", "108.2135"), (721, ".1700", "54.3473"), (865, ".1790", "60.8377"), (1282, ".3227", "185.1935"),
                           (2596, ".3200", "181.7319"), (3653, ".3900", "363.4627"), (None, ".4700", "655.7704")],
    "resident_no_threshold": [(188, ".1500", ".1500"), (371, ".2084", "11.0185"), (515, ".1790", ".1066"), (932, ".3227", "74.1674"), (2246, ".3200", "71.6508"), (3303, ".3900", "228.8816"),
                              (None, ".4700", "493.1893")],
}
TAX_YEAR_ISSUE = "PAYG table supports paydays from 1 July 2026 to 30 June 2027; review the table before using another year"
TAX_REVIEW_ISSUE = "Confirm tax declaration; HELP, variations and special payments use manual PAYG"
LAFHA_TAX_ISSUE, LAFHA_SUPER_ISSUE = "Confirm LAFHA tax treatment", "Confirm LAFHA super treatment"
MIXED_PENALTY_ISSUE = "Confirm qualifying earnings for mixed ordinary and overtime penalty hours"
HOUR_FIELDS = ("ordinary", "ot1", "ot2", "saturday", "sunday", "annual", "personal", "public_holiday", "night", "holiday_work")
_CENT, _SIX = Decimal("0.01"), Decimal("0.000001")


def _dec(value: Any, label: str, *, allow_negative: bool = False) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        number = Decimal(str(value))
    except Exception:
        raise EngineError(f"{label} is not a number") from None
    if not number.is_finite():
        raise EngineError(f"{label} is not a usable number")
    if number < 0 and not allow_negative:
        raise EngineError(f"{label} cannot be negative")
    return number


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def weekly_withholding(taxable_gross: Decimal, scale: str) -> Decimal:
    """Withholding for one week's taxable earnings from the built-in table. Whole dollars."""
    if taxable_gross <= 0:
        return Decimal("0")
    x = Decimal(int(taxable_gross)) + Decimal("0.99")
    for limit, a, b in SCALES[scale]:
        if limit is None or x < limit:
            return max(Decimal("0"), (Decimal(a) * x - Decimal(b)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    raise EngineError("The tax table has no final bracket")  # cannot happen with the tables above


def applied_rates(profile: dict[str, Any], rules: dict[str, Any]) -> tuple[dict[str, Decimal], Decimal | None]:
    """Hourly rates by category, and the base rate they came from (None when no rate is set)."""
    hourly = _dec(profile.get("hourly_rate"), "Hourly rate")
    salary, weekly_hours = _dec(profile.get("annual_salary"), "Annual salary"), _dec(profile.get("ordinary_weekly_hours"), "Ordinary weekly hours")
    base = salary / Decimal(52) / weekly_hours if salary > 0 and weekly_hours > 0 else hourly
    casual = profile.get("casual_rates") if profile.get("employment_type") == "casual" and isinstance(profile.get("casual_rates"), dict) else None
    if casual:
        base = _dec(casual.get("base_rate"), "Casual base rate") * (Decimal(1) + _dec(casual.get("loading_percent"), "Casual loading") / Decimal(100))
    if base <= 0:
        return {}, None
    rates = {"ordinary": base}
    for key, multiplier in (("ot1", "ot1_multiplier"), ("ot2", "ot2_multiplier"), ("night", "night_multiplier"), ("holiday_work", "holiday_work_multiplier")):
        explicit = casual.get(key) if casual else None
        # An explicit casual rate replaces multiplying the loaded rate, so the loading is never applied twice.
        rates[key] = _dec(explicit, f"Casual {key} rate") if explicit not in (None, "") else base * _dec(rules.get(multiplier), multiplier)
    rates["saturday"] = base * _dec(rules["saturday_multiplier"], "Saturday multiplier") if rules.get("saturday_multiplier") not in (None, "") else rates["ot1"]
    rates["sunday"] = base * _dec(rules["sunday_multiplier"], "Sunday multiplier") if rules.get("sunday_multiplier") not in (None, "") else rates["ot2"]
    return rates, base


def calculate_row(profile: dict[str, Any], entry: dict[str, Any], rules: dict[str, Any] | None, payday: date | str) -> dict[str, Any]:
    """One employee's week: pay lines, gross, withholding, net, super, leave balances and the reasons
    (if any) it is not ready to finalise. Raises EngineError for values that cannot be used at all."""
    rules = {**DEFAULT_RULES, **(rules or {})}
    payday = _day(payday)
    hours = {f: _dec(entry.get(f), f.replace("_", " ").capitalize() + " hours") for f in HOUR_FIELDS}
    if sum(hours.values()) > 168:
        raise EngineError("A week cannot hold more than 168 hours")
    penalty_ordinary = _dec(entry.get("penalty_ordinary"), "Penalty ordinary hours")
    penalty_hours = hours["night"] + hours["holiday_work"]
    if penalty_ordinary > penalty_hours:
        raise EngineError("Penalty ordinary hours cannot be more than the night and public holiday hours worked")
    rates, base = applied_rates(profile, rules)
    kind = profile.get("employment_type")
    issues: list[str] = []
    if base is None:
        issues.append("Enter an hourly rate or an annual salary with ordinary weekly hours")
        rates, base = {k: Decimal("0") for k in ("ordinary", "ot1", "ot2", "night", "holiday_work", "saturday", "sunday")}, Decimal("0")
    if kind not in ("full_time", "part_time", "casual", "contractor"):
        issues.append("Set the employment type")
    if not profile.get("conditions_reviewed"):
        issues.append("Confirm this employee's pay conditions have been reviewed")
    if not entry.get("hours_reviewed"):
        issues.append("Confirm the hours have been reviewed")
    paid_ordinary = hours["ordinary"] + hours["annual"] + hours["personal"] + hours["public_holiday"] + penalty_ordinary
    weekly = _dec(profile.get("ordinary_weekly_hours"), "Ordinary weekly hours")
    if weekly > 0 and paid_ordinary > weekly:
        issues.append(f"Paid ordinary hours ({paid_ordinary.normalize():f}) are more than this employee's ordinary weekly hours ({weekly.normalize():f})")
    if kind in ("casual", "contractor") and (hours["annual"] > 0 or hours["personal"] > 0):
        issues.append("Casual and contractor profiles do not normally take paid annual or personal leave")

    pay = {f: _money(hours[f] * rates[f]) for f in ("ordinary", "ot1", "ot2", "saturday", "sunday", "night", "holiday_work")}
    for leave in ("annual", "personal", "public_holiday"):
        pay[leave] = _money(hours[leave] * rates["ordinary"])
    leave_loading = _money(hours["annual"] * base * _dec(profile.get("leave_loading_percent"), "Leave loading") / Decimal(100))
    lafha = _money(_dec(entry.get("lafha"), "LAFHA"))
    taxable_allowances = _money(_dec(entry.get("taxable_allowances"), "Taxable allowances"))
    meal_count = _dec(entry.get("meal_count"), "Meal count")
    meal_pay = _money(meal_count * _dec(rules.get("meal_allowance"), "Meal allowance"))

    # Configured earnings and allowances: every code must be defined, with its tax and super treatment stated.
    definitions = {str(r.get("code")): r for r in rules.get("earning_rules") or [] if isinstance(r, dict) and r.get("code")}
    lines: dict[str, list[dict[str, Any]]] = {"allowances": [], "earnings": []}
    totals = {"allowances": Decimal("0"), "earnings": Decimal("0")}
    non_taxable = superable_extra = Decimal("0")
    for group in ("allowances", "earnings"):
        for item in entry.get(group) or []:
            units = _dec(item.get("units"), "Units")
            if units == 0:
                continue
            rule = definitions.get(str(item.get("code")))
            if rule is None:
                raise EngineError(f"'{item.get('code')}' is not a configured earning or allowance")
            if not isinstance(rule.get("taxable"), bool) or not isinstance(rule.get("superable"), bool):
                raise EngineError(f"Confirm the tax and super treatment of '{rule.get('name') or rule['code']}' in the pay settings")
            rate = base * _dec(rule.get("multiplier", 1), "Multiplier") if rule.get("basis") == "base_rate" else _dec(rule.get("rate"), "Rate")
            amount = _money(units * rate)
            lines[group].append({"code": rule["code"], "name": rule.get("name") or rule["code"], "units": float(units), "rate": float(rate), "amount": float(amount),
                                 "taxable": rule["taxable"], "superable": rule["superable"]})
            totals[group] += amount
            non_taxable += Decimal("0") if rule["taxable"] else amount
            superable_extra += amount if rule["superable"] else Decimal("0")

    gross = sum(pay.values(), Decimal("0")) + leave_loading + lafha + taxable_allowances + meal_pay + totals["allowances"] + totals["earnings"]
    reimbursements = _money(_dec(entry.get("reimbursements"), "Reimbursements"))
    deductions = _money(_dec(entry.get("deductions"), "Deductions"))

    # Withholding
    tax: Decimal | None = None
    tax_issues: list[str] = []
    taxable = gross - non_taxable
    if lafha > 0 and not isinstance(entry.get("lafha_taxable"), bool):
        tax_issues.append(LAFHA_TAX_ISSUE)
    elif lafha > 0 and entry.get("lafha_taxable") is False:
        taxable -= lafha
    if meal_pay > 0 and rules.get("meal_tax_treatment") not in ("taxable", "exempt"):
        tax_issues.append("Confirm the meal allowance tax treatment in the pay settings")
    elif meal_pay > 0 and rules.get("meal_tax_treatment") == "exempt":
        taxable -= meal_pay
    mode = profile.get("tax_mode")
    if not tax_issues:
        if not profile.get("tax_declaration_reviewed"):
            tax_issues.append(TAX_REVIEW_ISSUE)
        elif mode == "manual":
            if entry.get("manual_payg") in (None, "") or not str(entry.get("payg_reference") or "").strip():
                tax_issues.append("Enter the reviewed PAYG amount and the reference for how it was worked out")
            else:
                tax = _money(_dec(entry.get("manual_payg"), "Manual PAYG"))
        elif mode in SCALES:
            if not (TAX_FROM <= payday < TAX_UNTIL):
                tax_issues.append(TAX_YEAR_ISSUE)
            else:
                tax = weekly_withholding(taxable, mode)
        else:
            tax_issues.append("This tax setting is not worked out automatically; enter a reviewed manual PAYG amount")
    if tax is not None:
        tax += _money(_dec(entry.get("extra_withholding"), "Extra withholding"))
        if tax > gross:
            tax_issues.append("Withholding is more than gross pay")
        elif tax + deductions > gross + reimbursements:
            tax_issues.append("Withholding and deductions are more than the pay available")
    net = _money(gross - tax - deductions + reimbursements) if tax is not None else None

    # Super
    super_issues: list[str] = []
    qualifying: Decimal | None
    mode_super = "automatic"
    if entry.get("qualifying_earnings_override") not in (None, ""):
        qualifying, mode_super = _money(_dec(entry.get("qualifying_earnings_override"), "Qualifying earnings")), "override"
    else:
        qualifying = pay["ordinary"] + pay["annual"] + pay["personal"] + pay["public_holiday"] + leave_loading + superable_extra
        if lafha > 0 and not isinstance(entry.get("lafha_superable"), bool):
            super_issues.append(LAFHA_SUPER_ISSUE)
            qualifying = None
        elif lafha > 0 and entry.get("lafha_superable"):
            qualifying += lafha
        if penalty_hours > 0 and penalty_ordinary == penalty_hours and qualifying is not None:
            qualifying += pay["night"] + pay["holiday_work"]
        elif penalty_hours > 0 and penalty_ordinary > 0:
            super_issues.append(MIXED_PENALTY_ISSUE)
            qualifying = None
        if taxable_allowances > 0 or meal_pay > 0:
            super_issues.append("Confirm whether super applies to the allowances paid")
    if not entry.get("super_reviewed"):
        super_issues.append("Confirm super eligibility has been reviewed")
    if kind == "contractor":
        super_issues.append("Confirm super eligibility for this contractor")
    super_amount = _money(qualifying * _dec(rules.get("super_percent"), "Super percent") / Decimal(100)) if qualifying is not None else None

    # Leave balances (hours). Casuals and contractors do not accrue.
    accrues = kind in ("full_time", "part_time")
    annual_accrued = (paid_ordinary * _dec(profile.get("annual_weeks"), "Annual leave weeks") / Decimal(52)).quantize(_SIX, rounding=ROUND_HALF_UP) if accrues else Decimal("0")
    personal_accrued = (paid_ordinary * _dec(profile.get("personal_weeks"), "Personal leave weeks") / Decimal(52)).quantize(_SIX, rounding=ROUND_HALF_UP) if accrues else Decimal("0")
    annual_closing = _dec(entry.get("opening_annual"), "Opening annual leave", allow_negative=True) + annual_accrued - hours["annual"]
    personal_closing = _dec(entry.get("opening_personal"), "Opening personal leave", allow_negative=True) + personal_accrued - hours["personal"]

    all_issues = issues + tax_issues + super_issues
    number = lambda value: None if value is None else float(value)  # noqa: E731
    return {
        "ordinary_pay": float(pay["ordinary"]), "ot1_pay": float(pay["ot1"]), "ot2_pay": float(pay["ot2"]), "saturday_pay": float(pay["saturday"]), "sunday_pay": float(pay["sunday"]),
        "lafha_pay": float(lafha), "annual_pay": float(pay["annual"]), "personal_pay": float(pay["personal"]), "public_holiday_pay": float(pay["public_holiday"]),
        "leave_loading": float(leave_loading), "taxable_allowances": float(taxable_allowances), "night_pay": float(pay["night"]), "holiday_work_pay": float(pay["holiday_work"]),
        "meal_allowance_pay": float(meal_pay), "configured_allowances": float(totals["allowances"]), "other_earnings": float(totals["earnings"]),
        "annual_closing": float(annual_closing), "personal_closing": float(personal_closing), "applied_rates": {k: float(v) for k, v in rates.items()},
        "allowance_lines": lines["allowances"], "earning_lines": lines["earnings"], "gross": float(gross), "payg": number(tax), "net": number(net), "super": number(super_amount),
        "super_mode": mode_super, "super_issues": super_issues, "qualifying_earnings": number(qualifying), "annual_accrued": float(annual_accrued), "personal_accrued": float(personal_accrued),
        "annual_base_value": float(_money(annual_closing * rates["ordinary"])), "reimbursements": float(reimbursements), "deductions": float(deductions),
        "issues": all_issues, "review_ready": not all_issues,
    }
