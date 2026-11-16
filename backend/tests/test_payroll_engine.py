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


# ---------- hours into money (B09) ----------

@pytest.mark.parametrize("case", FIXTURES["payroll_rows"], ids=lambda c: c["id"])
def test_pay_calculation_matches_the_pack(case):
    i = case["input"]
    assert engine.calculate_row(i["profile"], i["entry"], i["rules"], i["payday"]) == case["expected"]


BASE = FIXTURES["payroll_rows"][0]["input"]


def calc(profile=None, entry=None, rules=None, payday="2026-10-29"):
    return engine.calculate_row({**BASE["profile"], **(profile or {})}, {**BASE["entry"], **(entry or {})}, {**BASE["rules"], **(rules or {})}, payday)


def test_withholding_table_edges():
    from decimal import Decimal as D
    w = engine.weekly_withholding
    assert w(D("0"), "resident_threshold") == 0 and w(D("361.00"), "resident_threshold") == 0  # under the first limit
    assert w(D("362"), "resident_threshold") == 0 and w(D("400"), "resident_threshold") == 6  # 0.15 x 400.99 - 54.3462
    assert w(D("1140.99"), "resident_threshold") == w(D("1140.00"), "resident_threshold") == 183  # cents within the dollar do not matter
    assert w(D("5000"), "resident_threshold") == 1695 and w(D("100"), "resident_no_threshold") == 15
    assert calc(profile={"tax_mode": "resident_no_threshold"})["payg"] == 293.0  # 0.32 x 1140.99 - 71.6508 = 293.47
    # The table's dates: the first day it covers works, the day before does not.
    assert calc(payday="2026-07-01")["payg"] == 183.0 and calc(payday="2026-06-30")["payg"] is None
    assert calc(profile={"tax_mode": "foreign_resident"})["issues"] == ["This tax setting is not worked out automatically; enter a reviewed manual PAYG amount"]
    assert calc(profile={"tax_mode": "manual"})["payg"] is None  # manual with no amount and reference is not a zero


def test_rates_gross_and_net():
    # Salary: 79,040 a year over 52 weeks of 38 hours is $40.00 an hour.
    salaried = calc(profile={"hourly_rate": 0, "annual_salary": 79040})
    assert salaried["applied_rates"]["ordinary"] == 40.0 and salaried["ordinary_pay"] == 1520.0
    # Every category at its own rate; nothing stacks; each line is rounded before adding.
    full = calc(entry={"ordinary": 30.33, "saturday": 4, "sunday": 2, "night": 3, "holiday_work": 1, "penalty_ordinary": 4, "public_holiday": 7.6, "personal": 1})
    assert (full["ordinary_pay"], full["saturday_pay"], full["sunday_pay"], full["night_pay"], full["holiday_work_pay"], full["public_holiday_pay"], full["personal_pay"]) == (909.9, 180.0, 120.0, 180.0, 75.0, 228.0, 30.0)
    assert full["gross"] == 1722.9 and full["qualifying_earnings"] == 1422.9  # all penalty hours ordinary: night and holiday pay count for super
    assert calc(rules={"saturday_multiplier": 1.25, "sunday_multiplier": 1.75}, entry={"saturday": 1, "sunday": 1})["applied_rates"]["saturday"] == 37.5
    # Reimbursements and deductions sit outside gross; extra withholding is added after the table.
    net = calc(entry={"reimbursements": 50, "deductions": 20.005, "extra_withholding": 10})
    assert (net["gross"], net["payg"], net["net"], net["reimbursements"], net["deductions"]) == (1140.0, 193.0, 976.99, 50.0, 20.01)
    assert "Withholding and deductions are more than the pay available" in calc(entry={"deductions": 2000})["issues"]
    # LAFHA once classified: not taxed, not superable.
    lafha = calc(entry={"lafha": 100, "lafha_taxable": False, "lafha_superable": False})
    assert (lafha["gross"], lafha["payg"], lafha["net"], lafha["super"], lafha["review_ready"]) == (1240.0, 183.0, 1057.0, 136.8, True)
    assert calc(entry={"lafha": 100, "lafha_taxable": True, "lafha_superable": True})["qualifying_earnings"] == 1240.0
    # Meals: taxable by default; exempt when the settings say so; unknown stops the tax calculation.
    assert calc(entry={"meal_count": 2})["meal_allowance_pay"] == 40.0 and calc(entry={"meal_count": 2})["payg"] == 196.0
    assert calc(entry={"meal_count": 2}, rules={"meal_tax_treatment": "exempt"})["payg"] == 183.0
    assert calc(entry={"meal_count": 2}, rules={"meal_tax_treatment": None})["payg"] is None
    # Night hours with none marked ordinary are overtime-like: paid, but not in qualifying earnings.
    assert calc(entry={"ordinary": 30, "night": 4})["qualifying_earnings"] == 900.0
    assert calc(entry={"qualifying_earnings_override": 1000})["super"] == 120.0 and calc(entry={"qualifying_earnings_override": 1000})["super_mode"] == "override"
    # A zero week owes nothing and has no issues about amounts.
    zero = calc(entry={"ordinary": 0})
    assert (zero["gross"], zero["payg"], zero["net"], zero["super"]) == (0.0, 0.0, 0.0, 0.0)


def test_configured_earnings_need_a_definition_and_stated_treatment():
    rules = {"earning_rules": [{"code": "TOOL", "name": "Tool allowance", "basis": "fixed", "rate": 12.5, "taxable": True, "superable": False},
                               {"code": "LH", "name": "Leading hand", "basis": "base_rate", "multiplier": 0.1, "taxable": True, "superable": True},
                               {"code": "TRAVEL", "name": "Travel", "basis": "fixed", "rate": 30, "taxable": False, "superable": False},
                               {"code": "VAGUE", "name": "Unclassified", "basis": "fixed", "rate": 5}]}
    row = calc(rules=rules, entry={"allowances": [{"code": "TOOL", "units": 5}, {"code": "TRAVEL", "units": 2}], "earnings": [{"code": "LH", "units": 38}, {"code": "TOOL", "units": 0}]})
    assert row["configured_allowances"] == 122.5 and row["other_earnings"] == 114.0 and row["gross"] == 1376.5
    assert [(x["code"], x["amount"]) for x in row["allowance_lines"]] == [("TOOL", 62.5), ("TRAVEL", 60.0)] and row["earning_lines"][0]["rate"] == 3.0
    assert row["payg"] == 240.0  # tax on 1316.50: the travel allowance is not taxable
    assert row["qualifying_earnings"] == 1254.0  # ordinary pay plus the superable leading-hand earnings
    for bad, words in (({"allowances": [{"code": "NOPE", "units": 1}]}, "not a configured"), ({"earnings": [{"code": "VAGUE", "units": 1}]}, "tax and super treatment"),
                       ({"earnings": [{"code": "LH", "units": -1}]}, "cannot be negative")):
        with pytest.raises(engine.EngineError) as error:
            calc(rules=rules, entry=bad)
        assert words in str(error.value)


def test_review_issues_and_unusable_values():
    assert calc(profile={"hourly_rate": 0})["issues"][0].startswith("Enter an hourly rate")
    assert "Set the employment type" in calc(profile={"employment_type": None})["issues"]
    assert "Confirm this employee's pay conditions have been reviewed" in calc(profile={"conditions_reviewed": False})["issues"]
    assert "Confirm the hours have been reviewed" in calc(entry={"hours_reviewed": False})["issues"]
    assert calc(entry={"super_reviewed": False})["super_issues"] == ["Confirm super eligibility has been reviewed"]
    assert any("more than this employee's ordinary weekly hours" in i for i in calc(entry={"ordinary": 40})["issues"])
    casual = calc(profile={"employment_type": "casual"}, entry={"annual": 7.6, "ordinary": 30})
    assert "Casual and contractor profiles do not normally take paid annual or personal leave" in casual["issues"] and casual["annual_accrued"] == 0.0
    assert "Confirm super eligibility for this contractor" in calc(profile={"employment_type": "contractor"})["issues"]
    assert calc(entry={"opening_annual": -5})["annual_closing"] == -2.076923  # a balance already overdrawn stays visible
    for entry, words in (({"ordinary": float("nan")}, "not a usable number"), ({"ordinary": float("inf")}, "not a usable number"), ({"ordinary": -1}, "cannot be negative"),
                         ({"ordinary": 170}, "168 hours"), ({"night": 2, "penalty_ordinary": 3, "ordinary": 30}, "cannot be more than"), ({"ordinary": "lots"}, "not a number")):
        with pytest.raises(engine.EngineError) as error:
            calc(entry=entry)
        assert words in str(error.value)
