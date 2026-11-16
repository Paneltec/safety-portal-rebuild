"""Permission catalogue and the single resolver used by every endpoint.

Resolution order (business rule B01):
  1. explicit per-user override (allow or deny)
  2. the user's role default
  3. deny

Constraints applied on top:
  - an action a resource does not support (email/delete) is always denied
  - `payroll` is never granted by a role or an override: only by the payroll
    owner/named-grant layer
  - `comms_safe_mode` is never granted by a role default: only by an explicit
    per-user override
"""
import json
import re
from pathlib import Path
from typing import Any

_DATA = json.loads((Path(__file__).parent / "data" / "permission_defaults.json").read_text())

ACTIONS: list[str] = _DATA["actions"]
RESOURCES: dict[str, dict[str, Any]] = _DATA["resources"]
TEAM_SCOPED_RESOURCES: list[str] = _DATA["team_scoped_resources"]

Matrix = dict[str, dict[str, bool]]

# Resources that role defaults and ordinary overrides can never grant.
GRANT_ONLY_RESOURCES = {"payroll"}
OVERRIDE_ONLY_RESOURCES = {"comms_safe_mode"}

REVIEWED_RESOURCES = ["swms", "pre_starts", "site_diary", "hazards", "incidents", "inspections", "risk_assessments"]

# System roles: (name, phone role key, source of defaults in the observed baseline).
# `manager` has no observed default matrix; decision D-004 seeds it as supervisor.
SYSTEM_ROLES: dict[str, dict[str, str]] = {
    "admin": {"name": "Admin", "legacy_role": "admin", "defaults": "admin"},
    "manager": {"name": "Manager", "legacy_role": "supervisor", "defaults": "supervisor"},
    "hseq_lead": {"name": "HSEQ lead", "legacy_role": "supervisor", "defaults": "hseq_lead"},
    "supervisor": {"name": "Supervisor", "legacy_role": "supervisor", "defaults": "supervisor"},
    "auditor": {"name": "Auditor", "legacy_role": "supervisor", "defaults": "auditor"},
    "worker": {"name": "Worker", "legacy_role": "worker", "defaults": "worker"},
    "contractor_rep": {"name": "Contractor rep", "legacy_role": "contractor", "defaults": "contractor_rep"},
    "contractor_rep_submit_only": {
        "name": "Contractor rep (submit only)",
        "legacy_role": "contractor",
        "defaults": "contractor_rep_submit_only",
    },
}


def supported(resource: str, action: str) -> bool:
    meta = RESOURCES.get(resource)
    if meta is None or action not in ACTIONS:
        return False
    if action == "email" and not meta.get("email_supported", False):
        return False
    if action == "delete" and not meta.get("delete_supported", False):
        return False
    return True


def empty_matrix() -> Matrix:
    return {r: {a: False for a in ACTIONS} for r in RESOURCES}


def system_role_matrix(role_key: str) -> Matrix:
    """Default matrix for a built-in role."""
    matrix = empty_matrix()
    if role_key == "admin":
        # Decision D-003: admin holds every supported action on ordinary resources.
        for r in RESOURCES:
            for a in ACTIONS:
                matrix[r][a] = supported(r, a)
    else:
        source = _DATA["role_defaults"].get(SYSTEM_ROLES[role_key]["defaults"], {})
        for r, actions in source.items():
            for a in actions:
                if supported(r, a):
                    matrix[r][a] = True
    if role_key == "hseq_lead":
        # Decision D-029: the spec gives review of safety records to "HSEQ/admin", so the HSEQ lead
        # holds `approve` on the capture registers although the observed matrix left it off.
        for r in REVIEWED_RESOURCES:
            matrix[r]["approve"] = True
    for r in GRANT_ONLY_RESOURCES | OVERRIDE_ONLY_RESOURCES:
        matrix[r] = {a: False for a in ACTIONS}
    return matrix


def tokens_to_matrix(tokens: list[str]) -> Matrix:
    """Custom roles are described as 'resource.action' tokens."""
    matrix = empty_matrix()
    for token in tokens:
        resource, sep, action = token.partition(".")
        if not sep or resource not in RESOURCES or action not in ACTIONS:
            raise ValueError(f"Unknown permission token: {token}")
        if resource in GRANT_ONLY_RESOURCES | OVERRIDE_ONLY_RESOURCES:
            raise ValueError(f"'{resource}' cannot be granted through a role")
        if not supported(resource, action):
            raise ValueError(f"'{resource}' does not support the '{action}' action")
        matrix[resource][action] = True
    return matrix


def matrix_to_tokens(matrix: Matrix) -> list[str]:
    return [f"{r}.{a}" for r in RESOURCES for a in ACTIONS if matrix.get(r, {}).get(a) is True]


def clean_overrides(raw: Any, *, allow_special: bool = False) -> Matrix:
    """Validate an overrides payload. Values must be real booleans (never the string 'false')."""
    if not isinstance(raw, dict):
        raise ValueError("overrides must be an object")
    cleaned: Matrix = {}
    for resource, cells in raw.items():
        if resource not in RESOURCES:
            raise ValueError(f"Unknown resource: {resource}")
        if resource in GRANT_ONLY_RESOURCES:
            raise ValueError("Payroll access is managed through payroll grants, not permission overrides")
        if resource in OVERRIDE_ONLY_RESOURCES and not allow_special:
            raise ValueError(f"Only an admin can change '{resource}' access")
        if not isinstance(cells, dict):
            raise ValueError(f"overrides.{resource} must be an object")
        for action, value in cells.items():
            if action not in ACTIONS:
                raise ValueError(f"Unknown action: {action}")
            if value is None:
                continue  # null clears the cell
            if not isinstance(value, bool):
                raise ValueError(f"overrides.{resource}.{action} must be true or false")
            if value and not supported(resource, action):
                raise ValueError(f"'{resource}' does not support the '{action}' action")
            cleaned.setdefault(resource, {})[action] = value
    return cleaned


PAYROLL_LEVELS = {"view": {"open", "view"}, "edit": {"open", "view", "edit"}}


def effective_permissions(role_matrix: Matrix, overrides: Matrix | None, *, payroll_owner: bool = False, payroll_level: str | None = None) -> Matrix:
    overrides = overrides or {}
    result = empty_matrix()
    for r in RESOURCES:
        for a in ACTIONS:
            if not supported(r, a):
                continue
            if r in GRANT_ONLY_RESOURCES:
                # Only the payroll owner, or a named grant held by an administrator, opens payroll.
                result[r][a] = payroll_owner or a in PAYROLL_LEVELS.get(payroll_level or "", set())
                continue
            override = overrides.get(r, {}).get(a)
            if isinstance(override, bool):
                result[r][a] = override
            elif r in OVERRIDE_ONLY_RESOURCES:
                result[r][a] = False
            else:
                result[r][a] = role_matrix.get(r, {}).get(a) is True
    return result


def can(effective: Matrix, resource: str, action: str) -> bool:
    return effective.get(resource, {}).get(action) is True


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug[:80]
