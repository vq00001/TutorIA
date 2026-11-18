"""Shared PostgreSQL fixture. Test writes are rolled back."""

import os

import pytest
from sqlalchemy import create_engine

from db.connection import compose_database_url


@pytest.fixture
def postgres():
    url = os.environ.get("TUTORIA_TEST_ADMIN_URL")
    if not url and os.environ.get("POSTGRES_HOST"):
        url = compose_database_url()
    if not url:
        pytest.skip("Set TUTORIA_TEST_ADMIN_URL to a local initialized PostgreSQL database")
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            try:
                yield connection
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
