import secrets
from typing import Optional
from pydantic_settings import BaseSettings


_DEFAULT_SECRET = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"


class Settings(BaseSettings):
    app_env: str = "development"

    # Database — defaults to SQLite for local dev; use MySQL for production:
    # mysql+pymysql://USER:PASS@HOST/DB_NAME
    database_url: str = "sqlite:///./lead_crm.db"

    # SECURITY: Override this with a long random string in production.
    # Generate one with: python -c "import secrets; print(secrets.token_hex(32))"
    secret_key: str = _DEFAULT_SECRET

    # Host / CORS
    host_url: str = "http://localhost:8000"
    # Comma-separated list of allowed origins, e.g. "https://crm.example.com,https://app.example.com"
    allowed_origins: str = ""

    # Meta (Facebook) App credentials
    meta_app_id: str = "local_dev_meta_app_id"
    meta_app_secret: str = "local_dev_meta_app_secret"
    meta_config_id: str = "1648844739991868"
    meta_page_access_token: str = ""
    meta_webhook_verify_token: str = "local_dev_verify_token"

    # WhatsApp Cloud API
    whatsapp_phone_number_id: str = "local_dev_wa_phone_id"
    whatsapp_access_token: str = "local_dev_wa_access_token"
    whatsapp_webhook_verify_token: str = "local_dev_wa_verify_token"

    # Google Sheets Service Account — path to the downloaded JSON key file
    google_sheets_credentials_file: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

    def get_allowed_origins(self) -> list[str]:
        """Return CORS allowed origins as a list."""
        base = [self.host_url, "http://localhost", "http://localhost:8000"]
        if self.allowed_origins:
            extras = [o.strip() for o in self.allowed_origins.split(",") if o.strip()]
            base.extend(extras)
        return list(set(base))

    def validate_production(self) -> None:
        """Raise ValueError for dangerous production misconfigurations."""
        if self.app_env == "production":
            if self.secret_key == _DEFAULT_SECRET:
                raise ValueError(
                    "SECRET_KEY must be changed from the default value in production. "
                    "Run: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if self.meta_app_id == "local_dev_meta_app_id":
                raise ValueError("META_APP_ID must be set in production.")
            if self.meta_app_secret == "local_dev_meta_app_secret":
                raise ValueError("META_APP_SECRET must be set in production.")


settings = Settings()
