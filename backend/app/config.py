from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    DATABASE_URL: str = ""

    # AWS
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_DEFAULT_REGION: str = "us-east-1"

    # Backend
    BACKEND_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    ML_ANOMALY_THRESHOLD: float = -0.3

    # Safety
    GLOBAL_AUTOMATION_ENABLED: bool = False
    DRY_RUN_MODE: bool = True
    MAX_MONTHLY_BUDGET_USD: float = 10.00
    MAX_DAILY_SPEND_USD: float = 2.00
    MAX_ACTIONS_PER_DAY: int = 5
    ACTION_COOLDOWN_MINUTES: int = 30
    MAX_CW_API_CALLS_PER_HOUR: int = 200

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
