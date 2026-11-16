"""The payroll rules as pure functions, checked against the reconstruction pack's worked examples."""
import json
from pathlib import Path

import pytest

from app import payroll_engine as engine

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "payroll_fixtures.json").read_text())


@pytest.mark.parametrize("case", FIXTURES["shifts"], ids=lambda c: c["id"])
def test_shift_allocation_matches_the_pack(case):
    if "expected_error" in case:
        with pytest.raises(engine.EngineError) as error:
            engine.allocate_shifts(case["shifts"], case["rules"], separate_weekends=case["separate_weekends"])
        assert str(error.value) == case["expected_error"]
    else:
        assert engine.allocate_shifts(case["shifts"], case["rules"], separate_weekends=case["separate_weekends"]) == case["expected"]


def shift(day="2026-10-19", start="07:00", finish="15:00", **extra):
    return {"date": day, "start": start, "finish": finish, **extra}


def test_shift_allocation_beyond_the_examples():
    # 12 hours on a weekday: 7.6 ordinary, 2 at the first overtime rate, the rest at the second; one meal at 10 hours.
    long_day = engine.allocate_shifts([shift(start="06:00", finish="18:00")])
    assert (long_day["ordinary"], long_day["ot1"], long_day["ot2"], long_day["meal_count"]) == (7.6, 2.0, 2.4, 1)
    assert engine.allocate_shifts([shift(start="04:00", finish="18:00")])["meal_count"] == 2  # 14 hours
    # Legacy mode: Saturday counts as the first overtime rate and Sunday as the second.
    assert engine.allocate_shifts([shift(day="2026-10-24")], separate_weekends=False)["ot1"] == 8.0
    assert engine.allocate_shifts([shift(day="2026-10-25")], separate_weekends=False)["ot2"] == 8.0
    assert engine.allocate_shifts([shift(day="2026-10-25")])["sunday"] == 8.0
    # A Friday night shift that runs into Saturday stays a night shift; a Saturday evening is Saturday.
    assert engine.allocate_shifts([shift(day="2026-10-23", start="20:00", finish="04:00", next_day=True)])["night"] == 8.0
    assert engine.allocate_shifts([shift(day="2026-10-24", start="18:00", finish="22:00")])["saturday"] == 4.0
    # Penalties never stack: a public holiday worked at night is holiday work only.
    holiday_night = engine.allocate_shifts([shift(start="18:00", finish="23:00", public_holiday=True)])
    assert holiday_night["holiday_work"] == 5.0 and holiday_night["night"] == 0.0
    # A night shift not marked as replacing a day shift earns no "penalty ordinary" hours.
    assert engine.allocate_shifts([shift(start="18:00", finish="02:00", next_day=True)])["penalty_ordinary"] == 0.0
    # A break with a stated start may cross zones; it comes off the zone it falls in.
    split = engine.allocate_shifts([shift(start="16:00", finish="22:00", break_minutes=30, break_start="18:00")])
    assert (split["ordinary"], split["night"]) == (2.0, 3.5)
    # Two shifts in a day share the day's ordinary hours; back-to-back shifts do not overlap.
    two = engine.allocate_shifts([shift(start="06:00", finish="10:00"), shift(start="10:00", finish="16:00")])
    assert (two["ordinary"], two["ot1"], two["ot2"]) == (7.6, 2.0, 0.4)
    # An overnight break after midnight is placed on the next day.
    assert engine.allocate_shifts([shift(start="20:00", finish="04:00", next_day=True, break_minutes=30, break_start="01:00")])["night"] == 7.5


@pytest.mark.parametrize("bad, words", [
    (shift(start="25:00"), "time like 07:30"),
    (shift(start="15:00", finish="07:00"), "finishing the next day"),
    (shift(start="07:00", finish="07:00"), "after start"),
    (shift(break_minutes=600), "shorter than the shift"),
    (shift(break_minutes=-5), "shorter than the shift"),
    (shift(break_minutes=30, break_start="16:00"), "inside the shift"),
    (shift(day="19/10/2026"), "date like"),
    (shift(break_minutes="lots"), "whole number"),
])
def test_unusable_times_are_refused_with_a_reason(bad, words):
    with pytest.raises(engine.EngineError) as error:
        engine.allocate_shifts([bad])
    assert words in str(error.value)


def test_overlap_counts_the_whole_span_and_the_night_window_must_be_real():
    # The second shift starts during the first one's unpaid break: still an overlap.
    with pytest.raises(engine.EngineError) as error:
        engine.allocate_shifts([shift(start="07:00", finish="12:00", break_minutes=30, break_start="11:30"), shift(start="11:45", finish="15:00")])
    assert str(error.value) == engine.OVERLAP_ERROR
    with pytest.raises(engine.EngineError):  # an overnight shift overlapping the next morning's
        engine.allocate_shifts([shift(start="22:00", finish="06:30", next_day=True), shift(day="2026-10-20", start="06:00", finish="10:00")])
    with pytest.raises(engine.EngineError):
        engine.allocate_shifts([shift()], {"night_start": "18:00", "night_end": "18:00"})
