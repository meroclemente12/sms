import pytest

from app.config import DEV_JWT_SECRET, Settings

NEON = "postgresql://owner:pw@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"


@pytest.mark.parametrize("prefix", ["postgres://", "postgresql://"])
def test_plain_connection_string_uses_psycopg(prefix):
    s = Settings(database_url=prefix + "u:p@host/db?sslmode=require")
    assert s.database_url == "postgresql+psycopg://u:p@host/db?sslmode=require"


def test_explicit_driver_kept():
    url = "postgresql+psycopg://u:p@host/db"
    assert Settings(database_url=url).database_url == url


def test_production_refuses_dev_secret():
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(environment="production", database_url=NEON, jwt_secret=DEV_JWT_SECRET)
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(environment="production", database_url=NEON, jwt_secret="short")


def test_production_ok():
    s = Settings(environment="production", database_url=NEON, jwt_secret="x" * 40)
    assert s.database_url.startswith("postgresql+psycopg://")
