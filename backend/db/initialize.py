"""Prepara PostgreSQL para TutorIA."""

import os

from sqlalchemy import URL, create_engine
from sqlalchemy.schema import CreateSchema


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
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            connection.execute(CreateSchema("app", if_not_exists=True))
            connection.exec_driver_sql("REVOKE ALL ON SCHEMA app FROM PUBLIC")
            connection.exec_driver_sql("GRANT USAGE ON SCHEMA app TO tutoria_api, tutoria_worker")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
