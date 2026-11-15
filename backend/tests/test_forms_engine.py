"""Pure tests of the form engine: schema checks and the value shape of every field family."""
import pytest

from app import forms as engine

ALL_FIELDS = [
    {"id": "h", "type": "section_header", "label": "Vehicle checks"},
    {"id": "t", "type": "text", "label": "Rego", "required": True, "config": {"uppercase": True, "max_length": 10}},
    {"id": "ta", "type": "textarea", "label": "Notes"},
    {"id": "n", "type": "number", "label": "Odometer", "config": {"min": 0, "max": 999999, "unit": "km"}},
    {"id": "d", "type": "date", "label": "Date"},
    {"id": "tm", "type": "time", "label": "Start"},
    {"id": "s", "type": "select", "label": "Weather", "options": ["Fine", "Rain"]},
    {"id": "sm", "type": "select", "label": "PPE", "options": ["Hat", "Vest", "Boots"], "config": {"multiple": True}},
    {"id": "r", "type": "radio", "label": "Shift", "options": ["Day", "Night"]},
    {"id": "c", "type": "compliance", "label": "Tyres", "required": True},
    {"id": "m", "type": "reference_matrix", "label": "Risk", "config": {"rows": ["Likelihood", "Consequence"], "columns": ["Low", "High"]}},
    {"id": "a", "type": "attachment", "label": "Docs"},
    {"id": "p", "type": "photo", "label": "Photos", "required": True, "config": {"max_files": 2}},
    {"id": "sig", "type": "signature", "label": "Sign", "required": True},
    {"id": "g", "type": "gps", "label": "Location"},
    {"id": "v", "type": "navixy_vehicle", "label": "Vehicle"},
    {"id": "w", "type": "worker_picker", "label": "Crew", "config": {"multiple": True}},
    {"id": "j", "type": "job_picker", "label": "Job"},
    {"id": "si", "type": "site_picker", "label": "Site"},
    {"id": "q", "type": "asset_qr", "label": "Asset"},
    {"id": "ca", "type": "corrective_actions", "label": "Actions"},
]
FILES = {"p": {"ph1", "ph2", "ph3"}, "sig": {"sg1"}, "a": {"at1"}}
GOOD = {
    "t": " pt-031 ", "ta": "All fine", "n": 120450.5, "d": "2026-11-15", "tm": "07:04", "s": "Fine", "sm": ["Hat", "Vest", "Hat"],
    "r": "Day", "c": {"result": "fail", "note": "Nail in rear left"}, "m": {"likelihood": "Low", "consequence": "High"},
    "a": ["at1"], "p": ["ph1", "ph2"], "sig": {"file_id": "sg1", "signed_name": "Jess Marlow"},
    "g": {"lat": -41.43, "lng": 147.14, "accuracy_m": 8, "captured_at": "2026-11-15T07:04:00+11:00"},
    "v": {"label": "PT-031 Tipper", "tracker_id": "88123", "source": "navixy"},
    "w": [{"id": "w1", "label": "Tom Okafor"}], "j": {"id": "j9", "label": "Northbank stage 2"},
    "si": {"id": "s3", "label": "Northbank"}, "q": {"asset_id": "a7", "label": "EX-07", "scan_token": "tok"},
    "ca": [{"description": "Replace tyre", "responsible": "Workshop", "due_date": "2026-11-16"}],
}


def schema():
    return engine.normalise_schema(ALL_FIELDS)


def test_every_field_family_is_covered_and_round_trips():
    s = schema()
    assert {f["type"] for f in s} == set(engine.FIELD_TYPES)
    cleaned, errors = engine.validate_values(s, GOOD, final=True, files_by_field=FILES)
    assert errors == []
    assert cleaned["t"] == "PT-031"  # trimmed and upper-cased as configured
    assert cleaned["sm"] == ["Hat", "Vest"]
    assert cleaned["ca"][0]["status"] == "open"
    assert cleaned["m"] == {"likelihood": "Low", "consequence": "High"}
    # Cleaning is stable: validating the cleaned answers again changes nothing.
    again, errors = engine.validate_values(s, cleaned, final=True, files_by_field=FILES)
    assert errors == [] and again == cleaned


def test_required_only_enforced_on_final_submit():
    s = schema()
    cleaned, errors = engine.validate_values(s, {"ta": "partial"}, final=False)
    assert errors == [] and cleaned == {"ta": "partial"}
    _, errors = engine.validate_values(s, {"ta": "partial"}, final=True)
    assert {e["field_id"] for e in errors} == {"t", "c", "p", "sig"}


@pytest.mark.parametrize("field_id,bad", [
    ("t", 12), ("t", "X" * 11), ("n", "12"), ("n", float("nan")), ("n", float("inf")), ("n", -1), ("n", True),
    ("d", "15/11/2026"), ("tm", "7:04"), ("tm", "25:00"), ("s", "Snow"), ("sm", ["Hat", "Gloves"]), ("sm", "Hat"),
    ("r", "Evening"), ("c", {"result": "maybe"}), ("c", {"result": "fail"}), ("c", {"result": "fail", "note": "  "}),
    ("m", {"likelihood": "Extreme"}), ("m", {"other": "Low"}), ("a", ["someone-elses-file"]), ("p", ["ph1", "ph2", "ph3"]),
    ("p", "ph1"), ("sig", {"file_id": "ph1", "signed_name": "X"}), ("sig", {"file_id": "sg1"}),
    ("g", {"lat": 95, "lng": 147}), ("g", {"lat": -41}), ("g", {"unavailable_reason": ""}), ("g", "here"),
    ("v", {"label": ""}), ("v", {"label": "PT-031", "source": "navixy"}), ("w", {"id": "w1", "label": "Tom"}),
    ("w", [{"label": "Tom Okafor"}]), ("j", "Northbank"), ("si", {"id": "s3"}), ("q", {"label": "EX-07"}),
    ("ca", [{"description": "Fix it"}]), ("ca", [{"description": "Fix", "responsible": "Me", "due_date": "soon"}]),
])
def test_bad_values_are_rejected(field_id, bad):
    _, errors = engine.validate_values(schema(), {field_id: bad}, final=False, files_by_field=FILES)
    assert [e["field_id"] for e in errors] == [field_id]


def test_passing_and_not_applicable_checks_need_no_note_and_gps_has_a_manual_path():
    s = schema()
    cleaned, errors = engine.validate_values(s, {"c": {"result": "pass"}, "g": {"unavailable_reason": "No signal in the cutting"},
                                                 "v": {"label": "Hire ute", "source": "manual"}}, final=False)
    assert errors == [] and cleaned["c"] == {"result": "pass"}
    assert cleaned["g"] == {"unavailable_reason": "No signal in the cutting"} and cleaned["v"]["source"] == "manual"


def test_unknown_field_ids_are_reported_not_dropped():
    _, errors = engine.validate_values(schema(), {"legacy_field": "x"}, final=False)
    assert errors == [{"field_id": "legacy_field", "message": "This field is not part of the form"}]


def test_schema_rules():
    out = engine.normalise_schema([{"type": "text", "label": " Name "}])
    assert out[0]["label"] == "Name" and out[0]["id"].startswith("f_") and out[0]["required"] is False
    assert engine.normalise_schema([{"id": "h", "type": "section_header", "label": "H", "required": True}])[0]["required"] is False
    for bad in (
        "nope", [{"type": "text"}], [{"type": "hologram", "label": "X"}], [{"type": "select", "label": "X"}],
        [{"type": "select", "label": "X", "options": []}], [{"id": "a", "type": "text", "label": "A"}, {"id": "a", "type": "text", "label": "B"}],
        [{"id": "bad id!", "type": "text", "label": "A"}], [{"type": "reference_matrix", "label": "M"}],
        [{"type": "number", "label": "N", "config": {"min": 5, "max": 1}}], [{"type": "text", "label": "T", "config": {"max_length": "long"}}],
    ):
        with pytest.raises(engine.SchemaError):
            engine.normalise_schema(bad)
