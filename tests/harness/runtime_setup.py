"""Create one new E2E database and ephemeral TLS material before runtime startup."""

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import psycopg
from alembic import command
from alembic.config import Config
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import create_engine, text

from tests.harness.cluster_guard import guard_connection

SPACE = "9f8f3dd3-e2b5-49ce-9fce-888ce2e6e725"


def main():
    control, run, nonce = guard_connection()
    with control:
        if control.execute("SELECT run_id::text,nonce FROM test_harness.run_guard").fetchall() != [
            (run, nonce)
        ]:
            raise RuntimeError("Cluster guard mismatch")
    database = "tutoria_test_" + UUID(run).hex + "_browser"
    if os.environ["TEST_DB_NAME"] != database:
        raise RuntimeError("Runtime database does not belong to this run")
    password = os.environ["TEST_DB_PASSWORD"]
    with psycopg.connect(
        host="db-test", dbname="postgres", user="postgres", password=password, autocommit=True
    ) as admin:
        admin.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(database))
        )
    url = f"postgresql+psycopg://postgres:{password}@db-test:5432/{database}"
    os.environ["MIGRATION_DATABASE_URL"] = url
    engine = create_engine(url, hide_parameters=True)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE SCHEMA test_harness; CREATE TABLE test_harness.run_guard(run_id uuid,case_id text,nonce text)"
        )
        conn.execute(
            text("INSERT INTO test_harness.run_guard VALUES(:run,:case,:nonce)"),
            dict(run=run, case=database, nonce=nonce),
        )
    command.upgrade(Config("/app/alembic.ini"), "head")
    with engine.begin() as conn:
        for role, key in [
            ("tutoria_api", "TEST_API_PASSWORD"),
            ("tutoria_worker", "TEST_WORKER_PASSWORD"),
        ]:
            value = os.environ[key]
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise RuntimeError("Invalid synthetic role credential")
            conn.exec_driver_sql(f"ALTER ROLE {role} LOGIN PASSWORD '{value}'")
        conn.execute(
            text(
                "INSERT INTO app.embedding_spaces(id,provider,model_name,model_revision,dimension) VALUES(:id,'ollama','synthetic','fixture-v1',3)"
            ),
            dict(id=SPACE),
        )
    engine.dispose()
    directory = Path("/runtime")
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TutorIA ephemeral test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "web-test")]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("web-test")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory / "server.crt").write_bytes(
        leaf.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM)
    )
    (directory / "server.key").write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public = server_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    digest = hashes.Hash(hashes.SHA256())
    digest.update(public)
    (directory / "server.spki").write_text(
        base64.b64encode(digest.finalize()).decode(), encoding="utf-8"
    )
    os.chmod(directory / "server.key", 0o600)
    print(json.dumps({"runId": run, "caseId": "browser", "runtime": "prepared"}))


if __name__ == "__main__":
    main()
