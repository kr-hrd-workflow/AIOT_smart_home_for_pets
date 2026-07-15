from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


BACKEND_ROOT = Path(__file__).parents[1]


class SecretUrl(str):
    def __repr__(self) -> str:
        return "<redacted database URL>"


def validate_test_database_url(value: str) -> SecretUrl:
    parsed = urlsplit(value.replace("postgresql+psycopg", "postgresql", 1))
    if (
        parsed.scheme != "postgresql"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port != 55432
        or parsed.path != "/petcare_test"
    ):
        raise ValueError("TEST_DATABASE_URL must target the dedicated loopback petcare_test database on port 55432")
    return SecretUrl(value)


def ensure_test_database(value: str) -> None:
    admin_url = make_url(value).set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.exec_driver_sql(
                "SELECT 1 FROM pg_database WHERE datname='petcare_test'"
            ).scalar_one_or_none()
            if exists is None:
                connection.exec_driver_sql("CREATE DATABASE petcare_test")
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL", "")
    try:
        validated = validate_test_database_url(value)
    except ValueError as error:
        pytest.fail(str(error))
    ensure_test_database(validated)
    return validated
