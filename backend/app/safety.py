"""Safety registers: the register kinds, their built-in capture forms and workflow rules.

Every register entry is captured on a form schema (the built-in one below, or a published
form template of the matching category) and stores that schema with the answers.

Two workflows:
- review:   draft -> submitted -> approved | changes_requested | rejected
            (changes_requested can be edited and submitted again)
- tracking: open -> in_progress -> closed (and back), for hazards and incidents
"""
from dataclasses import dataclass
from datetime import date
from typing import Any

from . import forms as engine

REVIEW_STATUSES = ["draft", "submitted", "changes_requested", "approved", "rejected"]
TRACKING_STATUSES = ["open", "in_progress", "closed"]
EDITABLE_REVIEW = {"draft", "changes_requested"}

SEVERITIES = ["Low", "Medium", "High", "Critical"]
INCIDENT_CATEGORIES = ["Near miss", "First aid", "Medical treatment", "Lost time", "Environmental", "Property damage", "Hazard"]
PPE = ["Hard hat", "Hi-vis clothing", "Safety boots", "Gloves", "Eye protection", "Hearing protection", "Respirator", "Sun protection"]

# Risk scoring rule, always shown beside any score (spec section 6).
RISK_RULE = (
    "Score = likelihood (1 rare to 5 almost certain) x consequence (1 minor to 5 catastrophic). "
    "1-4 Low, 5-9 Medium, 10-16 High, 17-25 Extreme."
)


def risk_rating(likelihood: Any, consequence: Any) -> dict[str, Any] | None:
    try:
        l, c = int(str(likelihood).strip()), int(str(consequence).strip())
    except (TypeError, ValueError):
        return None
    if not (1 <= l <= 5 and 1 <= c <= 5):
        return None
    score = l * c
    rating = "Low" if score <= 4 else "Medium" if score <= 9 else "High" if score <= 16 else "Extreme"
    return {"score": score, "rating": rating}


def _f(fid: str, ftype: str, label: str, required: bool = False, **extra: Any) -> dict[str, Any]:
    field: dict[str, Any] = {"id": fid, "type": ftype, "label": label, "required": required}
    if "options" in extra:
        field["options"] = extra.pop("options")
    if "help" in extra:
        field["help"] = extra.pop("help")
    if extra:
        field["config"] = extra
    return field


_RISK_COLUMNS = ["Hazard", "Likelihood (1-5)", "Consequence (1-5)", "Controls", "Residual likelihood (1-5)", "Residual consequence (1-5)"]

_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "swms": [
        _f("title", "text", "SWMS title", True, max_length=200),
        _f("code", "text", "Document code", max_length=40),
        _f("job_description", "textarea", "Job description", True),
        _f("scope", "textarea", "Scope of work"),
        _f("location", "text", "Location"),
        _f("high_risk_construction_work", "textarea", "High-risk construction work involved"),
        _f("activity_analysis", "table", "Job steps, hazards and controls", True,
           table_columns=["Job step", "Hazards", "Initial risk", "Controls", "Residual risk", "Responsible"]),
        _f("ppe", "select", "PPE required", options=PPE, multiple=True),
        _f("environmental_risks", "textarea", "Environmental risks and controls"),
        _f("training_requirements", "textarea", "Training and competencies needed"),
        _f("equipment_list", "textarea", "Plant and equipment"),
        _f("emergency_procedures", "textarea", "Emergency procedures"),
        _f("legislation_and_codes", "textarea", "Legislation and codes of practice"),
        _f("prepared_by", "text", "Prepared by", True),
        _f("review_date", "date", "Review date"),
        _f("source_file", "attachment", "Original document", max_files=3),
    ],
    "pre_start": [
        _f("date", "date", "Date", True),
        _f("crew_lead", "text", "Crew leader", True),
        _f("work_summary", "textarea", "Today's work", True),
        _f("vehicle", "navixy_vehicle", "Vehicle or plant"),
        _f("meter_reading", "number", "Odometer or hour meter", min=0),
        _f("linked_swms", "text", "SWMS in use"),
        _f("linked_permits", "text", "Permits"),
        _f("hazards_discussed", "textarea", "Hazards discussed", True),
        _f("sign_ons", "table", "Crew sign-on", True, table_columns=["Name", "Role", "Time"]),
        _f("notes", "textarea", "Notes"),
        _f("gps", "gps", "Location"),
        _f("signature", "signature", "Crew leader signature"),
    ],
    "site_diary": [
        _f("date", "date", "Date", True),
        _f("raw_notes", "textarea", "Diary notes", True, help="Write what happened in your own words."),
        _f("weather", "select", "Weather", options=["Fine", "Overcast", "Showers", "Rain", "Windy", "Frost", "Hot"]),
        _f("crew", "table", "Crew on site", table_columns=["Name", "Hours"]),
        _f("activities", "table", "Activities", table_columns=["Activity", "Detail"]),
        _f("issues", "textarea", "Issues or delays"),
        _f("photos", "photo", "Photos"),
    ],
    "hazard": [
        _f("title", "text", "What is the hazard?", True, max_length=200),
        _f("hazard_type", "select", "Type", True, options=["Hazard", "Near miss"]),
        _f("description", "textarea", "Describe it", True),
        _f("location", "text", "Where is it?"),
        _f("severity", "select", "Severity", True, options=SEVERITIES),
        _f("controls", "textarea", "Controls in place or needed"),
        _f("photos", "photo", "Photos"),
        _f("gps", "gps", "Location"),
    ],
    "incident": [
        _f("title", "text", "Incident title", True, max_length=200),
        _f("category", "select", "Category", True, options=INCIDENT_CATEGORIES),
        _f("occurred_date", "date", "Date it happened", True),
        _f("occurred_time", "time", "Time it happened", True),
        _f("location", "text", "Where did it happen?"),
        _f("description", "textarea", "What happened?", True),
        _f("immediate_actions", "textarea", "Immediate actions taken", True),
        _f("severity", "select", "Severity", options=SEVERITIES),
        _f("persons_involved", "table", "People involved", table_columns=["Name", "Role", "Injury or involvement"]),
        _f("root_cause", "textarea", "Root cause (once known)"),
        _f("photos", "photo", "Evidence photos"),
        _f("follow_up_actions", "corrective_actions", "Follow-up actions"),
        _f("gps", "gps", "Location"),
        _f("signature", "signature", "Reported by (signature)"),
    ],
    "inspection": [
        _f("inspection_type", "text", "What is being inspected?", True),
        _f("date", "date", "Date", True),
        _f("s1", "section_header", "Checklist"),
        _f("housekeeping", "compliance", "Housekeeping and access", True),
        _f("signage", "compliance", "Signage and barricades", True),
        _f("ppe", "compliance", "PPE in use", True),
        _f("plant", "compliance", "Plant and equipment", True),
        _f("excavations", "compliance", "Excavations and trenches", True),
        _f("traffic", "compliance", "Traffic management", True),
        _f("environment", "compliance", "Environmental controls", True),
        _f("s2", "section_header", "Follow up"),
        _f("corrective_actions", "corrective_actions", "Corrective actions"),
        _f("photos", "photo", "Photos"),
        _f("notes", "textarea", "Notes"),
        _f("gps", "gps", "Location"),
        _f("operator_signature", "signature", "Inspector signature", True),
    ],
    "risk_assessment": [
        _f("title", "text", "Activity being assessed", True, max_length=200),
        _f("date", "date", "Date", True),
        _f("hazards", "table", "Hazards and controls", True, table_columns=_RISK_COLUMNS, help=RISK_RULE),
        _f("notes", "textarea", "Notes"),
        _f("operator", "text", "Assessed by", True),
        _f("operator_signature", "signature", "Signature"),
    ],
    "ssra": [
        _f("title", "text", "Task", True, max_length=200),
        _f("site", "text", "Site", True),
        _f("date", "date", "Date", True),
        _f("hazards", "table", "Site-specific hazards and controls", True, table_columns=_RISK_COLUMNS, help=RISK_RULE),
        _f("crew", "table", "Crew briefed", table_columns=["Name", "Role"]),
        _f("gps", "gps", "Location"),
        _f("operator_signature", "signature", "Supervisor signature"),
    ],
}


@dataclass(frozen=True)
class Kind:
    key: str
    prefix: str  # URL segment under /api
    resource: str  # permission resource
    label: str
    plural: str
    workflow: str  # review | tracking
    form_categories: tuple[str, ...]  # form template categories that project into this register

    @property
    def schema(self) -> list[dict[str, Any]]:
        return engine.normalise_schema(_SCHEMAS[self.key])

    @property
    def statuses(self) -> list[str]:
        return REVIEW_STATUSES if self.workflow == "review" else TRACKING_STATUSES


KINDS: dict[str, Kind] = {
    k.key: k
    for k in [
        Kind("swms", "swms", "swms", "SWMS", "SWMS", "review", ()),
        Kind("pre_start", "pre-starts", "pre_starts", "Pre-start", "Pre-starts", "review", ("pre_start",)),
        Kind("site_diary", "site-diary", "site_diary", "Site diary entry", "Site diary", "review", ("site_diary",)),
        Kind("hazard", "hazards", "hazards", "Hazard", "Hazards", "tracking", ("hazard",)),
        Kind("incident", "incidents", "incidents", "Incident", "Incidents", "tracking", ("incident",)),
        Kind("inspection", "inspections", "inspections", "Inspection", "Inspections", "review", ("inspection", "service_check")),
        Kind("risk_assessment", "risk-assessments", "risk_assessments", "Risk assessment", "Risk assessments", "review", ("risk_assessment",)),
        # SSRA is its own destination and never also lands in the general risk register.
        Kind("ssra", "ssra", "risk_assessments", "Site-specific risk assessment", "Site-specific risk assessments", "review", ("ssra",)),
    ]
}
KIND_FOR_CATEGORY: dict[str, str] = {category: k.key for k in KINDS.values() for category in k.form_categories}

STATUS_LABELS = {
    "draft": "Draft", "submitted": "Waiting for review", "changes_requested": "Changes requested",
    "approved": "Approved", "rejected": "Rejected", "open": "Open", "in_progress": "In progress", "closed": "Closed",
}


def derive_title(kind: str, values: dict[str, Any], fallback: str) -> str:
    def text(key: str) -> str:
        return str(values.get(key) or "").strip()

    def day(key: str = "date") -> str:
        raw = text(key)
        parts = raw.split("-")
        return "/".join(reversed(parts)) if len(parts) == 3 else raw

    if text("title"):
        return text("title")[:300]
    if kind == "pre_start":
        return " · ".join(x for x in ["Pre-start", day(), text("crew_lead")] if x)[:300]
    if kind == "site_diary":
        return " · ".join(x for x in ["Site diary", day()] if x)[:300]
    if kind == "inspection":
        return " · ".join(x for x in [text("inspection_type") or "Inspection", day()] if x)[:300]
    return fallback[:300]


def derive_date(values: dict[str, Any]) -> date | None:
    for key in ("date", "occurred_date"):
        raw = values.get(key)
        if isinstance(raw, str):
            try:
                return date.fromisoformat(raw)
            except ValueError:
                continue
    return None


def failed_checks(schema: list[dict[str, Any]], values: dict[str, Any]) -> list[dict[str, str]]:
    """Failed checklist items, so a failure stays visible on the record and is never read as a pass."""
    out = []
    for field in schema:
        value = values.get(field["id"])
        if field["type"] == "compliance" and isinstance(value, dict) and value.get("result") == "fail":
            out.append({"field_id": field["id"], "label": field["label"], "note": value.get("note", "")})
    return out


def risk_summary(schema: list[dict[str, Any]], values: dict[str, Any]) -> dict[str, Any] | None:
    """Scores for a hazards table that uses the standard risk columns, with the rule used."""
    field = next((f for f in schema if f["id"] == "hazards" and f["type"] == "table"), None)
    rows = values.get("hazards")
    if field is None or not isinstance(rows, list):
        return None
    ids = [c["id"] for c in field.get("config", {}).get("table_columns", [])]
    if not {"likelihood_1_5", "consequence_1_5"} <= set(ids):
        return None
    scored = []
    for row in rows:
        scored.append({
            "hazard": row.get("hazard", ""),
            "initial": risk_rating(row.get("likelihood_1_5"), row.get("consequence_1_5")),
            "residual": risk_rating(row.get("residual_likelihood_1_5"), row.get("residual_consequence_1_5")),
        })
    return {"rule": RISK_RULE, "rows": scored}
