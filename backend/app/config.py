from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://banahaw:banahaw@localhost:5432/banahaw"
    slot_step_min: int = 15


settings = Settings()
