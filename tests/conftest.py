import os

import pytest

# Force (not setdefault) test values so a developer's real .env — pydantic
# settings read it from the working directory — can never point the test run at
# a live database, Supabase project or AWS account.
_TEST_ENV = {
    "SUPABASE_JWT_SECRET": "test-secret-key-for-hs256",
    "JWT_AUDIENCE": "authenticated",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_JWKS_URL": "https://example.supabase.co/auth/v1/.well-known/jwks.json",
    "SUPABASE_SERVICE_ROLE_KEY": "test-service-role",
    "DATABASE_URL": "",
    "OPERATOR_USER_IDS": "operator-1,operator-2",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SECURITY_TOKEN": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
    "CLOUD_PROVIDER": "aws",
    "GLOBAL_AUTOMATION_ENABLED": "false",
    "DRY_RUN_MODE": "true",
    "CLOUDSENTRY_TESTING": "1",
    "LOG_FORMAT": "text",
    "ML_MODEL_PATH": "",
    "ML_IF_DECISION_THRESHOLD": "0.0",
    "ML_CONTAMINATION": "0.05",
    "LAMBDA_CONCURRENCY_LIMIT": "10",
    "REQUIRED_TAGS": "Project,Owner",
    "MAX_ACTIONS_PER_DAY": "5",
    "ACTION_COOLDOWN_MINUTES": "30",
    "MAX_DAILY_SPEND_USD": "2.00",
    "MAX_MONTHLY_BUDGET_USD": "10.00",
}
os.environ.update(_TEST_ENV)


@pytest.fixture(autouse=True)
def _reset_runtime_flags():
    """Runtime kill-switch/dry-run state is process-global; isolate every test."""
    from backend.app.services import runtime_config
    runtime_config._cache.clear()
    runtime_config.load_from_env()
    yield
    runtime_config._cache.clear()
    runtime_config.load_from_env()


@pytest.fixture
def fake_db(monkeypatch):
    """Route every get_supabase_client() call to one shared in-memory fake."""
    from tests.fakes import FakeSupabase
    from backend.app.db import supabase_client

    from backend.app.api import actions

    db = FakeSupabase()
    monkeypatch.setattr(supabase_client, "_client", db)
    # Cached services hold the client/boto3 clients of whichever test built them.
    actions.get_runner.cache_clear()
    yield db
    actions.get_runner.cache_clear()
