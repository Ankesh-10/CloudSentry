import pytest

from backend.app.services import runtime_config


@pytest.mark.parametrize("key,value", [
    ("GLOBAL_AUTOMATION_ENABLED", "maybe"),
    ("DRY_RUN_MODE", ""),
    ("MAX_ACTIONS_PER_DAY", "-1"),
    ("MAX_DAILY_SPEND_USD", "abc"),
    ("MAX_DAILY_SPEND_USD", "nan"),
    ("MAX_DAILY_SPEND_USD", "NaN"),
    ("MAX_MONTHLY_BUDGET_USD", "inf"),
    ("MAX_MONTHLY_BUDGET_USD", "-inf"),
    ("MAX_MONTHLY_BUDGET_USD", "1e309"),
    ("MAX_ACTIONS_PER_DAY", "1001"),
    ("MAX_ACTIONS_PER_DAY", True),
    ("ACTION_COOLDOWN_MINUTES", str(7 * 24 * 60 + 1)),
    ("MAX_CW_API_CALLS_PER_HOUR", "100001"),
    ("MAX_DAILY_SPEND_USD", "100000.01"),
    ("NOT_A_KEY", "1"),
])
def test_invalid_values_rejected(key, value):
    with pytest.raises(ValueError):
        runtime_config.set_flag(key, value, persist=False)


@pytest.mark.parametrize("key,value", [
    ("MAX_ACTIONS_PER_DAY", "1000"),
    ("ACTION_COOLDOWN_MINUTES", "0"),
    ("MAX_DAILY_SPEND_USD", "100000"),
])
def test_boundary_values_accepted(key, value):
    runtime_config.set_flag(key, value, persist=False)


def test_invalid_env_value_fails_startup(monkeypatch):
    from backend.app.config import settings
    monkeypatch.setattr(settings, "MAX_DAILY_SPEND_USD", float("nan"))
    with pytest.raises(ValueError):
        runtime_config.load_from_env()


def test_nan_in_db_does_not_replace_cap(fake_db):
    fake_db.rows("system_config").append({"key": "MAX_DAILY_SPEND_USD", "value": "nan"})
    assert runtime_config.refresh_from_db() is True
    assert runtime_config.get_flag("MAX_DAILY_SPEND_USD") == 2.0


def test_unsafe_change_needs_second_operator(fake_db):
    first = runtime_config.request_change("GLOBAL_AUTOMATION_ENABLED", "true", "USER:a")
    assert first["status"] == "pending_confirmation"
    assert runtime_config.automation_enabled() is False

    with pytest.raises(PermissionError):
        runtime_config.request_change("GLOBAL_AUTOMATION_ENABLED", "true", "USER:a")
    assert runtime_config.automation_enabled() is False

    second = runtime_config.request_change("GLOBAL_AUTOMATION_ENABLED", "true", "USER:b")
    assert second["status"] == "applied" and second["requested_by"] == "USER:a"
    assert runtime_config.automation_enabled() is True
    assert not [r for r in fake_db.rows("system_config") if r["key"].startswith(runtime_config.PENDING_PREFIX)]


def test_expired_request_is_not_confirmable(fake_db):
    import json
    from datetime import datetime, timezone
    stale = datetime.now(timezone.utc) - runtime_config.PENDING_TTL - runtime_config.timedelta(minutes=1)
    fake_db.rows("system_config").append({
        "key": runtime_config.PENDING_PREFIX + "DRY_RUN_MODE",
        "value": json.dumps({"value": "false", "requested_by": "USER:a", "requested_at": stale.isoformat()}),
    })
    res = runtime_config.request_change("DRY_RUN_MODE", "false", "USER:b")
    assert res["status"] == "pending_confirmation"
    assert runtime_config.dry_run_mode() is True


def test_safe_change_applies_immediately(fake_db):
    runtime_config.set_flag("DRY_RUN_MODE", False, persist=False)
    res = runtime_config.request_change("DRY_RUN_MODE", "true", "USER:a")
    assert res["status"] == "applied" and runtime_config.dry_run_mode() is True


@pytest.mark.parametrize("key,looser,tighter", [
    ("MAX_ACTIONS_PER_DAY", "1000", "1"),
    ("MAX_DAILY_SPEND_USD", "100000", "0.5"),
    ("MAX_MONTHLY_BUDGET_USD", "1000000", "1"),
    ("MAX_CW_API_CALLS_PER_HOUR", "100000", "10"),
    ("ACTION_COOLDOWN_MINUTES", "0", "600"),
])
def test_loosening_a_cap_needs_a_second_operator(fake_db, key, looser, tighter):
    before = runtime_config.get_flag(key)
    first = runtime_config.request_change(key, looser, "USER:a")
    assert first["status"] == "pending_confirmation" and runtime_config.get_flag(key) == before
    with pytest.raises(PermissionError):
        runtime_config.request_change(key, looser, "USER:a")
    second = runtime_config.request_change(key, looser, "USER:b")
    assert second["status"] == "applied" and second["confirmed_by"] == "USER:b"
    # Tightening is always the safe direction: one operator, immediate.
    assert runtime_config.request_change(key, tighter, "USER:a")["status"] == "applied"


def test_two_person_rule_can_be_disabled(fake_db, monkeypatch):
    from backend.app.config import settings
    monkeypatch.setattr(settings, "REQUIRE_TWO_PERSON_CONFIG", False)
    res = runtime_config.request_change("GLOBAL_AUTOMATION_ENABLED", "true", "USER:a")
    assert res["status"] == "applied" and runtime_config.automation_enabled() is True


def test_emergency_stop_cancels_pending_enable(fake_db):
    runtime_config.request_change("GLOBAL_AUTOMATION_ENABLED", "true", "USER:a")
    runtime_config.emergency_stop()
    res = runtime_config.request_change("GLOBAL_AUTOMATION_ENABLED", "true", "USER:b")
    assert res["status"] == "pending_confirmation"
    assert runtime_config.automation_enabled() is False


def test_unsafe_change_not_applied_if_persist_fails(fake_db):
    fake_db.fail_tables["system_config"] = True
    with pytest.raises(Exception):
        runtime_config.set_flag("GLOBAL_AUTOMATION_ENABLED", "true", persist=True)
    assert runtime_config.automation_enabled() is False


def test_emergency_stop_effective_even_if_persist_fails(fake_db):
    runtime_config.set_flag("GLOBAL_AUTOMATION_ENABLED", True, persist=False)
    fake_db.fail_tables["system_config"] = True
    _, persisted = runtime_config.emergency_stop()
    assert persisted is False
    assert runtime_config.automation_enabled() is False


def test_corrupt_db_value_fails_safe(fake_db):
    runtime_config.set_flag("GLOBAL_AUTOMATION_ENABLED", True, persist=False)
    fake_db.rows("system_config").extend([
        {"key": "GLOBAL_AUTOMATION_ENABLED", "value": "yes-please"},
        {"key": "DRY_RUN_MODE", "value": "garbage"},
    ])
    assert runtime_config.refresh_from_db() is True
    assert runtime_config.automation_enabled() is False
    assert runtime_config.dry_run_mode() is True
