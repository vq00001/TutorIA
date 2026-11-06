import os

from alembic import context
from sqlalchemy import create_engine

url = os.environ["MIGRATION_DATABASE_URL"]
with create_engine(url, pool_pre_ping=True).connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
