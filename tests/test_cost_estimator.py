from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.cost_estimator import HOURS_PER_MONTH, CostEstimationService, load_pricing

NOW = datetime.now(timezone.utc)


@pytest.fixture
def est(fake_db):
    return CostEstimationService()


def test_pricing_falls_back_to_us_east_1():
    assert load_pricing("xx-nowhere-9") == load_pricing("us-east-1")


@pytest.mark.parametrize("state,billed", [("running", True), ("pending", True), ("stopped", False),
                                          ("terminated", False)])
def test_ec2_billed_only_in_billable_states(est, state, billed):
    cost = est.estimate_hourly({"resource_type": "ec2", "state": state, "metadata": {"instance_type": "t3.micro"}})
    assert (cost > 0) is billed
    if billed:
        assert cost == est.pricing["ec2"]["t3.micro"]


def test_unpriced_type_costs_zero_and_warns_once(est, caplog):
    r = {"resource_type": "ec2", "state": "running", "metadata": {"instance_type": "x99.mega"}}
    assert est.estimate_hourly(r) == 0.0
    assert est.estimate_hourly(r) == 0.0
    assert caplog.text.count("x99.mega") == 1


def test_ebs_billed_by_size_even_when_unattached(est):
    r = {"resource_type": "ebs", "state": "available", "metadata": {"volume_type": "gp3", "size": 100}}
    expected = est.pricing["ebs"]["gp3_per_gb_month"] * 100 / HOURS_PER_MONTH
    assert est.estimate_hourly(r) == pytest.approx(expected)


def test_lambda_uses_invocations_duration_and_memory(est):
    r = {"resource_type": "lambda", "metadata": {"memory": 256}}
    lam = est.pricing["lambda"]
    cost = est.estimate_hourly(r, {"Invocations": 1000, "Duration": 100})
    expected = 1000 * 100 * lam["per_1ms_128mb"] * 2 + 1000 / 1_000_000 * lam["per_1M_requests"]
    assert cost == pytest.approx(expected)
    assert est.estimate_hourly(r, {"Invocations": 0}) == 0.0


def test_s3_without_size_sample_costs_zero(est):
    assert est.estimate_hourly({"resource_type": "s3"}, {}) == 0.0


def test_run_records_previous_hour_idempotently(est, fake_db):
    fake_db.rows("resources").extend([
        {"id": "r1", "resource_type": "ec2", "state": "running", "metadata": {"instance_type": "t3.micro"}},
        {"id": "r2", "resource_type": "ec2", "state": "stopped", "metadata": {"instance_type": "t3.micro"}},
        {"id": "r3", "resource_type": "ec2", "state": "terminated", "metadata": {"instance_type": "t3.micro"}},
    ])
    assert est.run() == 1
    assert est.run() == 1
    rows = fake_db.rows("cost_records")
    assert len(rows) == 1  # upsert on (resource_id, recorded_at): no double counting
    hour_start = NOW.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    assert rows[0]["resource_id"] == "r1" and rows[0]["recorded_at"] == hour_start.isoformat()
    assert rows[0]["source"] == "estimated"
