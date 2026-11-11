"""Prepara PostgreSQL para TutorIA."""

import os

from sqlalchemy import URL, create_engine, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateSchema

from .account_tokens import install_account_tokens
from .sessions import install_sessions
from .users import install_users


def initialize(connection: Connection) -> None:
    """Prepara la extensión y los módulos en la transacción de arranque."""
    connection.execute(select(func.pg_advisory_xact_lock(0x5455544F)))
    connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    connection.execute(CreateSchema("app", if_not_exists=True))
    connection.exec_driver_sql("REVOKE ALL ON SCHEMA app FROM PUBLIC")
    connection.exec_driver_sql("GRANT USAGE ON SCHEMA app TO tutoria_api, tutoria_worker")
    install_users(connection)
    install_sessions(connection)
    install_account_tokens(connection)


def main() -> None:
    url = URL.create(
        "postgresql+psycopg",
        host=os.environ["POSTGRES_HOST"],
        database=os.environ["POSTGRES_DB"],
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            initialize(connection)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
