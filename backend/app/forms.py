"""Form engine core: field families, template schema checks and answer validation.

The same definitions are served to the office app and the phone app
(`GET /api/forms/field-types`), so both validate against one contract and the
server has the final say.

JSON value shape for each field family
--------------------------------------
text, textarea     string
number             number (finite)
date               "YYYY-MM-DD"
time               "HH:MM" (24 hour)
select             string, or list of strings when config.multiple is true
radio              string
compliance         {"result": "pass"|"fail"|"na", "note": string?}   a fail needs a note
reference_matrix   {row_id: column_value}
attachment, photo  list of file ids already uploaded to this submission for this field
signature          {"file_id": id, "signed_name": string}
gps                {"lat": number, "lng": number, "accuracy_m": number?, "captured_at": iso?}
                   or {"unavailable_reason": string}
navixy_vehicle     {"label": string, "tracker_id": string?, "asset_id": string?, "source": "navixy"|"manual"}
worker_picker      {"id": string, "label": string}, or a list when config.multiple is true
job_picker         {"id": string, "label": string}
site_picker        {"id": string, "label": string}
asset_qr           {"asset_id": string, "label": string, "scan_token": string?}
corrective_actions list of {"description": string, "responsible": string, "due_date": "YYYY-MM-DD"?, "status": "open"|"done"}
table              list of rows, each {column_id: string}; config.columns is [{"id", "label"}]
section_header     no value (layout only)
"""
import math
import re
import uuid
from datetime import date, datetime
from typing import Any

FIELD_TYPES: dict[str, dict[str, Any]] = {
    "text": {"label": "Short text", "config": ["max_length", "uppercase"]},
    "textarea": {"label": "Long text", "config": ["max_length"]},
    "number": {"label": "Number", "config": ["min", "max", "unit"]},
    "date": {"label": "Date", "config": ["default_now"]},
    "time": {"label": "Time", "config": ["default_now"]},
    "select": {"label": "Dropdown", "needs_options": True, "config": ["multiple"]},
    "radio": {"label": "Choose one", "needs_options": True, "config": []},
    "compliance": {"label": "Pass / Fail / N/A", "config": []},
    "reference_matrix": {"label": "Reference matrix", "config": ["rows", "columns"]},
    "attachment": {"label": "File attachment", "file": True, "config": ["max_files"]},
    "photo": {"label": "Photo", "file": True, "config": ["max_files"]},
    "signature": {"label": "Signature", "file": True, "config": []},
    "gps": {"label": "GPS location", "config": []},
    "navixy_vehicle": {"label": "Vehicle (Navixy)", "config": []},
    "worker_picker": {"label": "Worker", "config": ["multiple"]},
    "job_picker": {"label": "Job", "config": []},
    "site_picker": {"label": "Site", "config": []},
    "asset_qr": {"label": "Asset QR scan", "config": []},
    "corrective_actions": {"label": "Corrective actions", "config": []},
    "table": {"label": "Table of rows", "config": ["table_columns"]},
    "section_header": {"label": "Section heading", "layout": True, "config": []},
}

CATEGORIES = {
    "general": "General",
    "pre_start": "Daily pre-start",
    "site_diary": "Site diary",
    "hazard": "Hazard report",
    "incident": "Incident report",
    "inspection": "Inspection",
    "risk_assessment": "Risk assessment",
    "ssra": "Site-specific risk assessment",
    "service_check": "Service check",
}

_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_FIELDS = 200
MAX_TEXT = 20000


class SchemaError(ValueError):
    pass


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def normalise_schema(fields: Any) -> list[dict[str, Any]]:
    """Check and tidy a template's field list. Field ids are stable; missing ids are generated."""
    if not isinstance(fields, list):
        raise SchemaError("fields must be a list")
    if len(fields) > MAX_FIELDS:
        raise SchemaError(f"A form can have at most {MAX_FIELDS} fields")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(fields, start=1):
        if not isinstance(raw, dict):
            raise SchemaError(f"Field {index} must be an object")
        ftype = raw.get("type")
        if ftype not in FIELD_TYPES:
            raise SchemaError(f"Field {index}: unknown field type '{ftype}'")
        field_id = raw.get("id") or f"f_{uuid.uuid4().hex[:10]}"
        if not isinstance(field_id, str) or not _ID.match(field_id):
            raise SchemaError(f"Field {index}: id may only contain letters, numbers, dash and underscore")
        if field_id in seen:
            raise SchemaError(f"Field {index}: duplicate field id '{field_id}'")
        seen.add(field_id)
        label = _text(raw.get("label"), 200)
        if not label:
            raise SchemaError(f"Field {index}: a label is required")
        meta = FIELD_TYPES[ftype]
        field: dict[str, Any] = {
            "id": field_id,
            "type": ftype,
            "label": label,
            "help": _text(raw.get("help"), 500),
            "required": bool(raw.get("required")) and not meta.get("layout"),
        }
        if meta.get("needs_options"):
            options = raw.get("options")
            if not isinstance(options, list):
                raise SchemaError(f"Field {index} ({label}): options are required")
            cleaned = list(dict.fromkeys(_text(o, 200) for o in options if _text(o, 200)))
            if not cleaned:
                raise SchemaError(f"Field {index} ({label}): at least one option is required")
            field["options"] = cleaned[:200]
        config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        clean_config: dict[str, Any] = {}
        for key in meta["config"]:
            if key not in config or config[key] in (None, ""):
                continue
            value = config[key]
            if key in ("multiple", "uppercase", "default_now"):
                clean_config[key] = value is True
            elif key in ("max_length", "max_files"):
                if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_TEXT:
                    raise SchemaError(f"Field {index} ({label}): {key} must be a whole number")
                clean_config[key] = value
            elif key in ("min", "max"):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise SchemaError(f"Field {index} ({label}): {key} must be a number")
                clean_config[key] = value
            elif key == "unit":
                clean_config[key] = _text(value, 20)
            elif key == "table_columns":
                if not isinstance(value, list) or not value:
                    raise SchemaError(f"Field {index} ({label}): a table needs columns")
                cols = []
                for col in value:
                    col_label = _text(col.get("label") if isinstance(col, dict) else col, 100)
                    col_id = (col.get("id") if isinstance(col, dict) else None) or re.sub(r"[^a-z0-9]+", "_", col_label.lower()).strip("_")[:64]
                    if not col_label or not col_id or not _ID.match(col_id):
                        raise SchemaError(f"Field {index} ({label}): every table column needs a label")
                    cols.append({"id": col_id, "label": col_label})
                if len({c["id"] for c in cols}) != len(cols) or len(cols) > 12:
                    raise SchemaError(f"Field {index} ({label}): table columns must be different (12 at most)")
                clean_config[key] = cols
            elif key in ("rows", "columns"):
                if not isinstance(value, list) or not value:
                    raise SchemaError(f"Field {index} ({label}): {key} must be a list")
                if key == "rows":
                    rows = []
                    for row in value:
                        row_id = row.get("id") if isinstance(row, dict) else None
                        row_label = _text(row.get("label") if isinstance(row, dict) else row, 200)
                        row_id = row_id or re.sub(r"[^a-z0-9]+", "_", row_label.lower()).strip("_")[:64]
                        if not row_label or not row_id or not _ID.match(row_id):
                            raise SchemaError(f"Field {index} ({label}): every matrix row needs a label")
                        rows.append({"id": row_id, "label": row_label})
                    if len({r["id"] for r in rows}) != len(rows):
                        raise SchemaError(f"Field {index} ({label}): matrix rows must be different")
                    clean_config[key] = rows
                else:
                    clean_config[key] = list(dict.fromkeys(_text(c, 100) for c in value if _text(c, 100)))
        if ftype == "reference_matrix" and not (clean_config.get("rows") and clean_config.get("columns")):
            raise SchemaError(f"Field {index} ({label}): a matrix needs rows and columns")
        if ftype == "table" and not clean_config.get("table_columns"):
            raise SchemaError(f"Field {index} ({label}): a table needs columns")
        if "min" in clean_config and "max" in clean_config and clean_config["min"] > clean_config["max"]:
            raise SchemaError(f"Field {index} ({label}): min is greater than max")
        if clean_config:
            field["config"] = clean_config
        out.append(field)
    return out


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _ref(value: Any, id_key: str = "id") -> dict[str, str] | None:
    """Picker values are structured references; the label is never the key."""
    if not isinstance(value, dict):
        return None
    ident, label = value.get(id_key), value.get("label")
    if not isinstance(ident, str) or not ident.strip() or not isinstance(label, str) or not label.strip():
        return None
    return {id_key: ident.strip()[:100], "label": label.strip()[:300]}


def _check(field: dict[str, Any], value: Any, file_ids: set[str]) -> Any:
    """Return the cleaned value for one field, or raise ValueError with a plain-language reason."""
    ftype = field["type"]
    config = field.get("config", {})
    if ftype in ("text", "textarea"):
        if not isinstance(value, str):
            raise ValueError("must be text")
        value = value.strip()
        if len(value) > config.get("max_length", MAX_TEXT):
            raise ValueError(f"must be at most {config.get('max_length', MAX_TEXT)} characters")
        return value.upper() if config.get("uppercase") else value
    if ftype == "number":
        if not _is_number(value):
            raise ValueError("must be a number")
        if "min" in config and value < config["min"]:
            raise ValueError(f"must be at least {config['min']}")
        if "max" in config and value > config["max"]:
            raise ValueError(f"must be at most {config['max']}")
        return value
    if ftype == "date":
        if not isinstance(value, str):
            raise ValueError("must be a date (YYYY-MM-DD)")
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise ValueError("must be a date (YYYY-MM-DD)") from None
    if ftype == "time":
        if not isinstance(value, str) or not _TIME.match(value):
            raise ValueError("must be a time (HH:MM)")
        return value
    if ftype in ("select", "radio"):
        options = field.get("options", [])
        if ftype == "select" and config.get("multiple"):
            if not isinstance(value, list) or any(v not in options for v in value):
                raise ValueError("must be chosen from the list")
            return list(dict.fromkeys(value))
        if value not in options:
            raise ValueError("must be chosen from the list")
        return value
    if ftype == "compliance":
        if not isinstance(value, dict) or value.get("result") not in ("pass", "fail", "na"):
            raise ValueError("must be Pass, Fail or N/A")
        note = _text(value.get("note"), 2000)
        if value["result"] == "fail" and not note:
            raise ValueError("a failed check needs a note saying what is wrong")
        return {"result": value["result"], **({"note": note} if note else {})}
    if ftype == "reference_matrix":
        rows = {r["id"] for r in config.get("rows", [])}
        columns = config.get("columns", [])
        if not isinstance(value, dict) or any(k not in rows or v not in columns for k, v in value.items()):
            raise ValueError("has an answer that is not in the matrix")
        return value
    if ftype in ("attachment", "photo"):
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise ValueError("must be a list of uploaded files")
        if any(v not in file_ids for v in value):
            raise ValueError("refers to a file that was not uploaded for this field")
        if len(value) > config.get("max_files", 20):
            raise ValueError(f"can have at most {config.get('max_files', 20)} files")
        return list(dict.fromkeys(value))
    if ftype == "signature":
        if not isinstance(value, dict) or value.get("file_id") not in file_ids:
            raise ValueError("needs a signature")
        name = _text(value.get("signed_name"), 200)
        if not name:
            raise ValueError("needs the name of the person signing")
        return {"file_id": value["file_id"], "signed_name": name}
    if ftype == "gps":
        if not isinstance(value, dict):
            raise ValueError("must be a location")
        if "lat" in value or "lng" in value:
            lat, lng = value.get("lat"), value.get("lng")
            if not (_is_number(lat) and _is_number(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
                raise ValueError("is not a valid location")
            out: dict[str, Any] = {"lat": lat, "lng": lng}
            if _is_number(value.get("accuracy_m")) and value["accuracy_m"] >= 0:
                out["accuracy_m"] = value["accuracy_m"]
            if isinstance(value.get("captured_at"), str):
                try:
                    datetime.fromisoformat(value["captured_at"].replace("Z", "+00:00"))
                    out["captured_at"] = value["captured_at"]
                except ValueError:
                    raise ValueError("has an invalid capture time") from None
            return out
        reason = _text(value.get("unavailable_reason"), 500)
        if not reason:
            raise ValueError("needs a location, or a note saying why it is not available")
        return {"unavailable_reason": reason}
    if ftype == "navixy_vehicle":
        if not isinstance(value, dict) or not _text(value.get("label"), 300):
            raise ValueError("needs a vehicle")
        source = value.get("source") if value.get("source") in ("navixy", "manual") else "manual"
        out = {"label": _text(value["label"], 300), "source": source}
        for key in ("tracker_id", "asset_id"):
            if value.get(key) not in (None, ""):
                out[key] = str(value[key])[:100]
        if source == "navixy" and "tracker_id" not in out and "asset_id" not in out:
            raise ValueError("a Navixy vehicle needs its tracker or asset reference")
        return out
    if ftype in ("worker_picker", "job_picker", "site_picker"):
        if ftype == "worker_picker" and config.get("multiple"):
            refs = [_ref(v) for v in value] if isinstance(value, list) else [None]
            if any(r is None for r in refs):
                raise ValueError("must be chosen from the list")
            return refs
        ref = _ref(value)
        if ref is None:
            raise ValueError("must be chosen from the list")
        return ref
    if ftype == "asset_qr":
        ref = _ref(value, "asset_id")
        if ref is None:
            raise ValueError("needs a scanned or chosen asset")
        if value.get("scan_token"):
            ref["scan_token"] = str(value["scan_token"])[:200]
        return ref
    if ftype == "corrective_actions":
        if not isinstance(value, list):
            raise ValueError("must be a list of actions")
        actions = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("has an invalid action")
            description, responsible = _text(item.get("description"), 2000), _text(item.get("responsible"), 200)
            if not description or not responsible:
                raise ValueError("each action needs a description and a responsible person")
            action = {"description": description, "responsible": responsible,
                      "status": item.get("status") if item.get("status") in ("open", "done") else "open"}
            if item.get("due_date"):
                try:
                    action["due_date"] = date.fromisoformat(str(item["due_date"])).isoformat()
                except ValueError:
                    raise ValueError("has an invalid due date") from None
            actions.append(action)
        return actions
    if ftype == "table":
        columns = [c["id"] for c in config.get("table_columns", [])]
        if not isinstance(value, list) or len(value) > 200:
            raise ValueError("must be a list of rows")
        rows = []
        for row in value:
            if not isinstance(row, dict) or any(k not in columns for k in row):
                raise ValueError("has a row with an unknown column")
            clean = {c: _text(row.get(c), 2000) for c in columns}
            if any(clean.values()):
                rows.append(clean)
        return rows
    raise ValueError("cannot hold a value")


def validate_values(
    schema: list[dict[str, Any]], values: dict[str, Any], *, final: bool, files_by_field: dict[str, set[str]] | None = None
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Check answers against a schema snapshot.

    A draft (final=False) may be incomplete but every supplied answer must be well formed.
    A submission (final=True) must also satisfy every required field.
    Answers for field ids not in the schema are rejected rather than silently dropped.
    """
    files_by_field = files_by_field or {}
    errors: list[dict[str, str]] = []
    cleaned: dict[str, Any] = {}
    known = {f["id"]: f for f in schema}
    for field_id in values:
        if field_id not in known:
            errors.append({"field_id": field_id, "message": "This field is not part of the form"})
    for field in schema:
        if FIELD_TYPES[field["type"]].get("layout"):
            continue
        value = values.get(field["id"])
        if _is_empty(value):
            if final and field["required"]:
                errors.append({"field_id": field["id"], "message": f"{field['label']} is required"})
            continue
        try:
            checked = _check(field, value, files_by_field.get(field["id"], set()))
        except ValueError as exc:
            errors.append({"field_id": field["id"], "message": f"{field['label']} {exc}"})
            continue
        if _is_empty(checked):
            if final and field["required"]:
                errors.append({"field_id": field["id"], "message": f"{field['label']} is required"})
            continue
        cleaned[field["id"]] = checked
    return cleaned, errors


def field_type_catalogue() -> list[dict[str, Any]]:
    return [
        {"type": key, "label": meta["label"], "needs_options": bool(meta.get("needs_options")),
         "is_file": bool(meta.get("file")), "is_layout": bool(meta.get("layout")), "config": meta["config"]}
        for key, meta in FIELD_TYPES.items()
    ]
