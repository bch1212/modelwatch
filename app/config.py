"""Application configuration — loaded from environment variables."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # --- Core ---
    app_name: str = "ModelWatch"
    debug: bool = False
    port: int = 8000

    # --- Database (Supabase Postgres) ---
    database_url: str = "postgresql+asyncpg://localhost:5432/modelwatch"

    # --- Encryption key for stored API keys (Fernet, 32-byte base64) ---
    encryption_key: str = "CHANGE_ME_GENERATE_WITH_Fernet.generate_key"

    # --- Stripe ---
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_pro: str = ""
    stripe_price_team: str = ""
    stripe_price_enterprise: str = ""

    # --- SendGrid ---
    sendgrid_api_key: str = ""
    sendgrid_from_email: str = "alerts@modelwatch.app"

    # --- Tier limits ---
    free_specs: int = 5
    free_runs_per_month: int = 500
    free_endpoints: int = 1

    pro_specs: int = 50
    pro_runs_per_month: int = 10_000
    pro_endpoints: int = 5

    team_specs: int = 999_999
    team_runs_per_month: int = 100_000
    team_endpoints: int = 999_999

    enterprise_specs: int = 999_999
    enterprise_runs_per_month: int = 999_999
    enterprise_endpoints: int = 999_999

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
