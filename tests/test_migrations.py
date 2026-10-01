"""Static checks that the SQL vocabularies match what the code writes.

No Postgres runs in the test suite, so a new action/resource type added in
Python but not in migration 009's CHECK constraints would only fail in
production, on insert. These tests catch that drift.
"""
import os
import re

from backend.app.services.action_runner import REVERSIBLE, VERIFY_TARGETS

MIGRATIONS = os.path.join(os.path.dirname(__file__), "..", "db", "migrations")


def _sql(name):
    with open(os.path.join(MIGRATIONS, name), encoding="utf-8") as f:
        return f.read()


def _vocab(sql, constraint):
    m = re.search(re.escape(constraint) + r"',\s*\$c\$(.*?)\$c\$", sql, re.S)
    assert m, f"{constraint} not found"
    return set(re.findall(r"'([^']+)'", m.group(1)))


SQL_009 = _sql("009_grants_and_constraints.sql")


def test_action_types_written_by_code_are_allowed():
    allowed = _vocab(SQL_009, "optimization_actions_action_type_check")
    written = set(REVERSIBLE) | set(REVERSIBLE.values()) | set(VERIFY_TARGETS) | {"apply_tags"}
    assert written <= allowed, written - allowed


def test_seeded_policies_satisfy_policy_constraints():
    seeds = _sql("004_seed_policies.sql") + _sql("006_security_and_seed.sql")
    # Seed rows end with: ..., '<action_type>', '<risk_level>', <requires_approval>, <priority>
    pairs = re.findall(r"'(\w+)',\s*'(LOW|MEDIUM|HIGH)',\s*(?:true|false)", seeds)
    assert len(pairs) >= 4
    allowed_actions = _vocab(SQL_009, "policies_action_type_check")
    allowed_risk = _vocab(SQL_009, "policies_risk_level_check")
    for action, risk in pairs:
        assert action in allowed_actions and risk in allowed_risk
    assert {"ec2", "lambda", "ebs", "*"} <= _vocab(SQL_009, "policies_resource_type_check")


def test_discovered_resource_types_are_allowed():
    from backend.app.services import discovery
    with open(discovery.__file__, encoding="utf-8") as f:
        src = f.read()
    discovered = set(re.findall(r'\("(\w+)", self\.cloud_adapter\.discover_', src))
    assert discovered and discovered <= _vocab(SQL_009, "resources_resource_type_check")


def test_migrations_are_numbered_contiguously():
    nums = sorted(int(f[:3]) for f in os.listdir(MIGRATIONS) if re.match(r"^\d{3}_.*\.sql$", f))
    assert nums == list(range(1, len(nums) + 1))
