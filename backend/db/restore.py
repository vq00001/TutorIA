"""Barrera y reproducción de un manifiesto externo de purgas."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateSchema

from .connection import RESTORE_LOCK
from .purge import _purge, _request_purge, acknowledge_receipt, deletion_requests

gate = sa.Table(
    "gate",
    sa.MetaData(schema="restore_control"),
    sa.Column("singleton", sa.Boolean, primary_key=True),
    sa.Column("verified_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint("singleton", name="restore_gate_singleton"),
)


@dataclass(frozen=True)
class DeletionReceipt:
    id: UUID
    scope: str
    subject_id: UUID
    requested_at: datetime


def _install_gate(conn: Connection) -> None:
    conn.execute(CreateSchema("restore_control", if_not_exists=True))
    gate.create(conn, checkfirst=True)
    conn.exec_driver_sql("REVOKE ALL ON SCHEMA restore_control FROM PUBLIC")
    conn.exec_driver_sql("REVOKE ALL ON restore_control.gate FROM PUBLIC")


def install_restore(conn: Connection) -> None:
    _install_gate(conn)
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.restore_is_ready() RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT NOT EXISTS (SELECT FROM restore_control.gate WHERE verified_at IS NULL)
        $$"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.restore_is_ready() FROM PUBLIC")
    conn.exec_driver_sql(
        "GRANT EXECUTE ON FUNCTION app.restore_is_ready() TO tutoria_api, tutoria_worker"
    )


def begin_restore(conn: Connection) -> None:
    """Cierra la barrera antes de importar un respaldo en una base aislada."""
    conn.execute(sa.select(sa.func.pg_advisory_xact_lock(RESTORE_LOCK)))
    _install_gate(conn)
    conn.execute(
        insert(gate)
        .values(singleton=True, verified_at=None)
        .on_conflict_do_update(index_elements=[gate.c.singleton], set_={"verified_at": None})
    )


def receipt_manifest_digest(receipts: Iterable[DeletionReceipt]) -> str:
    entries = {}
    for receipt in receipts:
        if receipt.requested_at.utcoffset() is None:
            raise ValueError("INVALID_RECEIPT_TIMESTAMP")
        entry = (
            str(receipt.id),
            receipt.scope,
            str(receipt.subject_id),
            receipt.requested_at.astimezone(timezone.utc).isoformat(),
        )
        if entries.setdefault(receipt.id, entry) != entry:
            raise ValueError("DELETION_ID_CONFLICT")
    return sha256(json.dumps(sorted(entries.values()), separators=(",", ":")).encode()).hexdigest()


def replay_restore(
    conn: Connection, receipts: Iterable[DeletionReceipt], *, expected_digest: str
) -> None:
    """Reproduce el manifiesto completo y abre la barrera en la misma transacción."""
    receipts = tuple(receipts)
    if receipt_manifest_digest(receipts) != expected_digest:
        raise ValueError("RESTORE_MANIFEST_MISMATCH")
    with conn.begin_nested():
        conn.execute(sa.select(sa.func.pg_advisory_xact_lock(RESTORE_LOCK)))
        pending = conn.scalar(sa.select(gate.c.singleton).where(gate.c.verified_at.is_(None)))
        if not pending:
            raise ValueError("RESTORE_NOT_PENDING")
        for receipt in sorted(receipts, key=lambda item: item.requested_at):
            canonical = _request_purge(
                conn,
                request_id=receipt.id,
                scope=receipt.scope,
                subject_id=receipt.subject_id,
                requested_at=receipt.requested_at,
            )
            row = (
                conn.execute(
                    sa.select(deletion_requests).where(deletion_requests.c.id == canonical)
                )
                .mappings()
                .one()
            )
            if canonical != receipt.id or row.requested_at != receipt.requested_at:
                raise ValueError("DELETION_RECEIPT_CONFLICT")
            acknowledge_receipt(conn, canonical)
            if row.completed_at is None:
                _purge(conn, row)
        conn.execute(gate.update().values(verified_at=sa.func.clock_timestamp()))
