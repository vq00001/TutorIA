"""Prepara PostgreSQL para TutorIA."""

import os

from sqlalchemy import URL, create_engine, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateSchema

from .account_tokens import install_account_tokens
from .citations import install_citations
from .conversations import install_conversations
from .courses import install_courses
from .documents import install_documents
from .exam_attempts import install_exam_attempts
from .exams import install_exams
from .generated_materials import install_generated_materials
from .generations import install_generations
from .invitations import install_invitations
from .learning_catalog import install_learning_catalog
from .learning_plans import install_learning_plans
from .learning_progress import install_learning_progress
from .processing import install_processing
from .quotas import install_quotas
from .sessions import install_sessions
from .study_sessions import install_study_sessions
from .users import install_users
from .vector_search import install_vector_search


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
    install_courses(connection)
    install_invitations(connection)
    install_documents(connection)
    install_processing(connection)
    install_learning_catalog(connection)
    install_vector_search(connection)
    install_generations(connection)
    install_quotas(connection)
    install_generated_materials(connection)
    install_exams(connection)
    install_exam_attempts(connection)
    install_learning_progress(connection)
    install_learning_plans(connection)
    install_study_sessions(connection)
    install_conversations(connection)
    install_citations(connection)


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
