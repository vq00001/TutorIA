"""Prepara PostgreSQL para TutorIA."""

import os

from psycopg import sql
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateSchema

from .account_tokens import install_account_tokens
from .citations import install_citations
from .connection import compose_database_url
from .conversations import install_conversations
from .courses import install_courses
from .documents import install_documents
from .exam_attempts import install_exam_attempts
from .exams import install_exams
from .generated_materials import install_generated_materials
from .generations import install_generations
from .idempotency import install_idempotency
from .invitations import install_invitations
from .jobs import install_jobs
from .learning_catalog import install_learning_catalog
from .learning_plans import install_learning_plans
from .learning_progress import install_learning_progress
from .processing import install_processing
from .quotas import install_quotas
from .sessions import install_sessions
from .study_sessions import install_study_sessions
from .users import install_users
from .vector_search import install_vector_search


def initialize(connection: Connection, *, role_passwords: dict[str, str] | None = None) -> None:
    """Prepara la extensión y los módulos en la transacción de arranque."""
    connection.execute(select(func.pg_advisory_xact_lock(0x5455544F)))
    if role_passwords is not None:
        ensure_roles(connection, role_passwords)
    connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    connection.execute(CreateSchema("app", if_not_exists=True))
    connection.exec_driver_sql("REVOKE ALL ON SCHEMA app FROM PUBLIC")
    connection.exec_driver_sql("GRANT USAGE ON SCHEMA app TO tutoria_api, tutoria_worker")
    install_users(connection)
    install_sessions(connection)
    install_account_tokens(connection)
    install_courses(connection)
    install_invitations(connection)
    install_documents(connection)
    install_processing(connection)
    install_learning_catalog(connection)
    install_vector_search(connection)
    install_generations(connection)
    install_quotas(connection)
    install_jobs(connection)
    install_idempotency(connection)
    install_generated_materials(connection)
    install_exams(connection)
    install_exam_attempts(connection)
    install_learning_progress(connection)
    install_learning_plans(connection)
    install_study_sessions(connection)
    install_conversations(connection)
    install_citations(connection)


def ensure_roles(connection: Connection, passwords: dict[str, str]) -> None:
    for role, password in passwords.items():
        exists = connection.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
        ).scalar_one_or_none()
        if exists is None:
            statement = sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
            with connection.connection.driver_connection.cursor() as cursor:
                cursor.execute(statement)


def main() -> None:
    role_passwords = {
        "tutoria_api": os.environ["API_DB_PASSWORD"],
        "tutoria_worker": os.environ["WORKER_DB_PASSWORD"],
    }
    engine = create_engine(compose_database_url(), hide_parameters=True)
    try:
        with engine.begin() as connection:
            initialize(connection, role_passwords=role_passwords)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
