"""Repite el resultado de una operación PostgreSQL durante 24 horas."""

import json
from collections.abc import Callable
from hashlib import sha256
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from .users import users

idempotency_requests = sa.Table(
    "idempotency_requests",
    users.metadata,
    sa.Column("user_id", sa.Uuid, sa.ForeignKey(users.c.id, ondelete="CASCADE"), primary_key=True),
    sa.Column("scope", sa.String(255), primary_key=True),
    sa.Column("key", sa.String(128), primary_key=True),
    sa.Column("request_hash", sa.LargeBinary, nullable=False),
    sa.Column("response", JSONB),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("octet_length(request_hash) = 32", name="idempotency_hash"),
)
sa.Index("idempotency_expiry_idx", idempotency_requests.c.expires_at)


def run_idempotent(
    conn: Connection, *, scope: str, key: str, payload: Any, operation: Callable[[Connection], Any]
) -> Any:
    """Ejecuta una operación transaccional con un resultado serializable como JSON."""
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    request_hash = sha256(encoded).digest()
    with conn.begin_nested():
        reservation = conn.execute(
            sa.select(sa.func.app.reserve_idempotency(scope, key, request_hash))
        ).scalar_one()
        if reservation["completed"]:
            return reservation["response"]
        response = operation(conn)
        return conn.execute(
            sa.select(
                sa.func.app.complete_idempotency(scope, key, request_hash, sa.cast(response, JSONB))
            )
        ).scalar_one()


def install_idempotency(conn: Connection) -> None:
    idempotency_requests.create(conn, checkfirst=True)
    conn.exec_driver_sql("ALTER TABLE app.idempotency_requests ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.idempotency_requests FORCE ROW LEVEL SECURITY")
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.reserve_idempotency(
            request_scope text, request_key text, fingerprint bytea
        ) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.course_actor();
            entry app.idempotency_requests;
            current_at timestamptz;
        BEGIN
            IF NOT EXISTS (SELECT FROM app.users WHERE id = actor AND status = 'active') THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            IF request_scope IS NULL OR char_length(request_scope) NOT BETWEEN 1 AND 255
                OR request_scope !~ '[^[:space:]]' OR request_key IS NULL
                OR char_length(request_key) NOT BETWEEN 1 AND 128 OR request_key !~ '[^[:space:]]'
                OR octet_length(fingerprint) IS DISTINCT FROM 32 THEN
                RAISE EXCEPTION 'INVALID_IDEMPOTENCY_KEY' USING ERRCODE = '23514';
            END IF;

            INSERT INTO app.idempotency_requests (user_id, scope, key, request_hash, expires_at)
            VALUES (actor, request_scope, request_key, fingerprint, clock_timestamp() + interval '24 hours')
            ON CONFLICT (user_id, scope, key) DO NOTHING;
            SELECT * INTO STRICT entry FROM app.idempotency_requests
            WHERE user_id = actor AND scope = request_scope AND key = request_key FOR UPDATE;

            current_at := clock_timestamp();
            IF entry.expires_at <= current_at THEN
                UPDATE app.idempotency_requests SET request_hash = fingerprint,
                    response = NULL, expires_at = current_at + interval '24 hours'
                WHERE user_id = actor AND scope = request_scope AND key = request_key;
                RETURN jsonb_build_object('completed', false);
            END IF;
            IF entry.request_hash <> fingerprint THEN
                RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT' USING ERRCODE = '23505';
            END IF;
            RETURN jsonb_build_object('completed', entry.response IS NOT NULL, 'response', entry.response);
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.complete_idempotency(
            request_scope text, request_key text, fingerprint bytea, result jsonb
        ) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE stored jsonb;
        BEGIN
            UPDATE app.idempotency_requests SET response = coalesce(result, 'null'::jsonb),
                expires_at = clock_timestamp() + interval '24 hours'
            WHERE user_id = app.course_actor() AND scope = request_scope AND key = request_key
              AND request_hash = fingerprint AND response IS NULL
            RETURNING response INTO stored;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'INVALID_IDEMPOTENCY_RESERVATION' USING ERRCODE = '23514';
            END IF;
            RETURN stored;
        END
        $$"""
    )
    signatures = "app.reserve_idempotency(text, text, bytea), app.complete_idempotency(text, text, bytea, jsonb)"
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signatures} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {signatures} TO tutoria_api")
