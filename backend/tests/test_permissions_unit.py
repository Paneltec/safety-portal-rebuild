"""Pure tests of the permission resolver (business rule B01)."""
import pytest

from app import permissions as perms


def test_catalogue_is_loaded():
    assert len(perms.ACTIONS) == 12
    assert "payroll" in perms.RESOURCES and "swms" in perms.RESOURCES
    assert set(perms.SYSTEM_ROLES) >= {"worker", "supervisor", "hseq_lead", "auditor", "manager", "admin",
                                       "contractor_rep", "contractor_rep_submit_only"}


def test_override_beats_role_default_then_deny():
    role = perms.system_role_matrix("worker")
    assert role["pre_starts"]["edit"] is True and role["users"]["view"] is False
    eff = perms.effective_permissions(role, {"pre_starts": {"edit": False}, "users": {"view": True}})
    assert eff["pre_starts"]["edit"] is False  # explicit deny wins
    assert eff["users"]["view"] is True  # explicit allow wins
    assert eff["pre_starts"]["view"] is True  # role default
    assert eff["integrations"]["view"] is False  # nothing granted, so denied


def test_unsupported_actions_are_always_denied():
    admin = perms.system_role_matrix("admin")
    assert admin["vehicles"]["email"] is False  # email not supported for vehicles
    assert admin["audit_exports"]["delete"] is False  # delete not supported
    eff = perms.effective_permissions(admin, {"vehicles": {"email": True}})
    assert eff["vehicles"]["email"] is False
    with pytest.raises(ValueError):
        perms.clean_overrides({"vehicles": {"email": True}})


def test_string_false_is_rejected_not_treated_as_true():
    with pytest.raises(ValueError):
        perms.clean_overrides({"swms": {"edit": "false"}})
    with pytest.raises(ValueError):
        perms.clean_overrides({"swms": {"edit": 1}})
    assert perms.clean_overrides({"swms": {"edit": False, "view": None}}) == {"swms": {"edit": False}}


def test_unknown_names_are_rejected():
    for bad in ({"nope": {"view": True}}, {"swms": {"fly": True}}, {"swms": True}, ["swms"]):
        with pytest.raises(ValueError):
            perms.clean_overrides(bad)


def test_payroll_is_never_granted_by_role_or_override():
    admin = perms.system_role_matrix("admin")
    assert not any(admin["payroll"].values())
    eff = perms.effective_permissions(admin, {"payroll": {"view": True}})
    assert not any(eff["payroll"].values())
    with pytest.raises(ValueError):
        perms.clean_overrides({"payroll": {"view": True}}, allow_special=True)
    with pytest.raises(ValueError):
        perms.tokens_to_matrix(["payroll.view"])
    owner = perms.effective_permissions(perms.system_role_matrix("worker"), None, payroll_owner=True)
    assert owner["payroll"]["view"] is True and owner["payroll"]["edit"] is True
    assert owner["payroll"]["delete"] is False  # not a supported action


def test_comms_safe_mode_needs_an_explicit_override():
    admin = perms.system_role_matrix("admin")
    assert perms.effective_permissions(admin, None)["comms_safe_mode"]["edit"] is False
    with pytest.raises(ValueError):
        perms.clean_overrides({"comms_safe_mode": {"edit": True}})
    cleaned = perms.clean_overrides({"comms_safe_mode": {"edit": True}}, allow_special=True)
    assert perms.effective_permissions(admin, cleaned)["comms_safe_mode"]["edit"] is True


def test_tokens_round_trip():
    matrix = perms.tokens_to_matrix(["swms.view", "swms.edit", "forms.use"])
    assert sorted(perms.matrix_to_tokens(matrix)) == ["forms.use", "swms.edit", "swms.view"]
    for bad in ("swms", "swms.fly", "nope.view", "vehicles.email"):
        with pytest.raises(ValueError):
            perms.tokens_to_matrix([bad])


def test_observed_role_defaults_are_preserved():
    assert perms.system_role_matrix("auditor")["users"]["view"] is False
    assert perms.system_role_matrix("hseq_lead")["users"]["edit"] is True
    assert perms.system_role_matrix("supervisor")["workers"]["team_view"] is True
    assert perms.system_role_matrix("worker")["workers"]["team_view"] is False
    assert perms.system_role_matrix("contractor_rep_submit_only")["forms"]["edit"] is True
    # Decision D-004: manager starts as a copy of supervisor.
    assert perms.system_role_matrix("manager") == perms.system_role_matrix("supervisor")
