from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-secret-change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "production" turns on the safety checks below. Render sets it in render.yaml.
    environment: str = "development"
    database_url: str = "postgresql+psycopg://banahaw:banahaw@localhost:5432/banahaw"
    slot_step_min: int = 15
    # Set a long random value in production (e.g. `openssl rand -hex 32`).
    jwt_secret: str = DEV_JWT_SECRET
    access_token_hours: int = 12
    # Comma-separated origins allowed to call the API from a browser.
    cors_origins: str = "http://localhost:5173"

    @field_validator("database_url")
    @classmethod
    def use_psycopg_driver(cls, url: str) -> str:
        """Accept the plain connection string Neon and Render show
        (postgres://... or postgresql://...) and use the psycopg 3 driver for it."""
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url

    @model_validator(mode="after")
    def check_production(self) -> "Settings":
        if self.environment.lower() != "production":
            return self
        if self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET must be set to a random value of 32+ characters")
        return self


settings = Settings()
