from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://banahaw:banahaw@localhost:5432/banahaw"
    slot_step_min: int = 15
    # Set a long random value in production (e.g. `openssl rand -hex 32`).
    jwt_secret: str = "dev-only-insecure-secret-change-me-in-production"
    access_token_hours: int = 12
    # Comma-separated origins allowed to call the API from a browser.
    cors_origins: str = "http://localhost:5173"


settings = Settings()
