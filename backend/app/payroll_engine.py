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
