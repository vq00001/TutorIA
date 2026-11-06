"""Runs only inside the harness against its guarded, disposable PostgreSQL cluster."""

import os
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


@pytest.fixture
def database(monkeypatch):
    run = UUID(os.environ["TEST_RUN_ID"])
    nonce = os.environ["TEST_NONCE"]
    if os.environ.get("TEST_DB_HOST") != "db-test" or not nonce:
        raise RuntimeError("Unsafe test database destination")
    name = "tutoria_test_" + run.hex + "_" + uuid4().hex[:8]
    password = os.environ["TEST_DB_PASSWORD"]
    with psycopg.connect(
        host="db-test", user="postgres", password=password, dbname="postgres", autocommit=True
    ) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
        try:
            admin_url = f"postgresql+psycopg://postgres:{password}@db-test:5432/{name}"
            monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
            engine = create_engine(admin_url)
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "CREATE SCHEMA test_harness; CREATE TABLE test_harness.run_guard(run_id uuid,case_id text,nonce text)"
                )
                conn.execute(
                    text("INSERT INTO test_harness.run_guard VALUES(:id,:case,:nonce)"),
                    dict(id=run, case=name, nonce=nonce),
                )
            command.upgrade(Config("/app/alembic.ini"), "head")
            with engine.begin() as conn:
                # Passwords are generated alphanumeric harness credentials, never user values.
                conn.exec_driver_sql(
                    "ALTER ROLE tutoria_api LOGIN PASSWORD '"
                    + password
                    + "'; ALTER ROLE tutoria_worker LOGIN PASSWORD '"
                    + password
                    + "'"
                )
            yield engine, f"postgresql+psycopg://tutoria_api:{password}@db-test:5432/{name}"
        finally:
            if "engine" in locals():
                with engine.connect() as conn:
                    guard = conn.execute(
                        text("SELECT run_id,case_id,nonce FROM test_harness.run_guard")
                    ).one()
                    if guard != (run, name, nonce):
                        raise RuntimeError("Run guard mismatch; refuse destructive cleanup")
                engine.dispose()
            admin.execute(
                psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(name)
                )
            )


def seed(engine):
    uid, cid = uuid4(), uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.users(id,nickname,display_name,email,password_hash,status) VALUES(:id,:nick,'Synthetic',:email,'unused','active')"
            ),
            dict(id=uid, nick="u_" + uid.hex, email=uid.hex + "@example.test"),
        )
        conn.execute(text("SELECT set_config('app.user_id',:id,true)"), dict(id=str(uid)))
        conn.execute(text("SELECT auth.create_course(:id,'Synthetic course','','')"), dict(id=cid))
    return uid, cid


def test_DAT_01_03_rls_and_pool_context(database):
    admin, url = database
    a, ca = seed(admin)
    b, cb = seed(admin)
    app = create_engine(url, pool_size=1, max_overflow=0)
    try:
        with app.begin() as conn:
            conn.execute(text("SELECT set_config('app.user_id',:id,true)"), dict(id=str(a)))
            assert conn.execute(text("SELECT id FROM app.courses")).scalars().all() == [ca]
            assert not conn.execute(
                text("SELECT id FROM app.courses WHERE id=:id"), dict(id=cb)
            ).first()
        with app.begin() as conn:
            assert not conn.execute(text("SELECT id FROM app.courses")).first()
            conn.execute(text("SELECT set_config('app.user_id',:id,true)"), dict(id=str(b)))
            assert conn.execute(text("SELECT id FROM app.courses")).scalars().all() == [cb]
    finally:
        app.dispose()


def test_DAT_14_last_coordinator(database):
    admin, url = database
    user, cid = seed(admin)
    with pytest.raises(Exception, match="LAST_COORDINATOR"):
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE app.memberships SET status='left' WHERE course_id=:cid AND user_id=:uid"
                ),
                dict(cid=cid, uid=user),
            )


def test_DAT_02_private_conversation(database):
    admin, url = database
    a, cid = seed(admin)
    b, other = seed(admin)
    tid = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text("INSERT INTO app.memberships(course_id,user_id,role) VALUES(:cid,:uid,'student')"),
            dict(cid=cid, uid=b),
        )
        conn.execute(
            text(
                "INSERT INTO app.conversations(id,course_id,user_id,title) VALUES(:id,:cid,:uid,'Private synthetic')"
            ),
            dict(id=tid, cid=cid, uid=a),
        )
    app = create_engine(url)
    try:
        with app.begin() as conn:
            conn.execute(text("SELECT set_config('app.user_id',:id,true)"), dict(id=str(b)))
            assert not conn.execute(
                text("SELECT id FROM app.conversations WHERE id=:id"), dict(id=tid)
            ).first()
    finally:
        app.dispose()


def test_DAT_13_cycle_rejected_in_sql(database):
    admin, url = database
    user, cid = seed(admin)
    a, b = uuid4(), uuid4()
    with admin.begin() as conn:
        for key in (a, b):
            conn.execute(
                text(
                    "INSERT INTO app.concepts(id,course_id,canonical_key,label,status) VALUES(:id,:cid,:key,'Synthetic','approved')"
                ),
                dict(id=key, cid=cid, key=str(key)),
            )
        conn.execute(
            text(
                "INSERT INTO app.concept_edges(id,course_id,source_id,target_id,relation,status) VALUES(:id,:cid,:a,:b,'prerequisite','approved')"
            ),
            dict(id=uuid4(), cid=cid, a=a, b=b),
        )
    with pytest.raises(Exception, match="GRAPH_CYCLE"):
        with admin.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app.concept_edges(id,course_id,source_id,target_id,relation,status) VALUES(:id,:cid,:a,:b,'prerequisite','approved')"
                ),
                dict(id=uuid4(), cid=cid, a=b, b=a),
            )
