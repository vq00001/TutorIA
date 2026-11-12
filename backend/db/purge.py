"""Retirada y purga transaccional por un operador de PostgreSQL."""

from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from .connection import require_restore_ready
from .conversations import conversations
from .courses import courses, memberships
from .documents import document_versions, documents
from .generations import generation_requests, generation_runs, generation_sources
from .jobs import jobs
from .learning_plans import learning_goals
from .learning_progress import mastery_events
from .processing import chunk_embeddings, processing_runs
from .sessions import sessions
from .study_sessions import study_sessions
from .users import users

deletion_requests = sa.Table(
    "deletion_requests", users.metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("scope", sa.Text, nullable=False),
    sa.Column("subject_id", sa.Uuid, nullable=False),
    sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.UniqueConstraint("scope", "subject_id"),
    sa.CheckConstraint("scope IN ('document', 'course', 'account')", name="deletion_scope"),
)
sa.Index(
    "deletion_pending_idx", deletion_requests.c.requested_at, deletion_requests.c.id,
    postgresql_where=deletion_requests.c.completed_at.is_(None),
)
deletion_ledger = sa.Table(
    "deletion_ledger", users.metadata,
    sa.Column("id", sa.Uuid, sa.ForeignKey(deletion_requests.c.id, ondelete="CASCADE"),
              primary_key=True),
    sa.Column("persisted_at", sa.DateTime(timezone=True)),
)


def install_purge(conn: Connection) -> None:
    for table in (deletion_requests, deletion_ledger):
        table.create(conn, checkfirst=True)
        conn.exec_driver_sql(f"REVOKE ALL ON {table.fullname} FROM PUBLIC, tutoria_api, tutoria_worker")


def _lock_courses(conn: Connection, condition) -> None:
    conn.execute(
        sa.select(courses.c.id).where(condition).order_by(courses.c.id).with_for_update()
    ).all()


def _withdraw_documents(conn: Connection, condition) -> None:
    withdrawn = conn.execute(
        documents.update()
        .where(condition, documents.c.status != "deletion_pending")
        .values(status="deletion_pending", active_run_id=None)
        .returning(documents.c.id, documents.c.course_id)
    ).all()
    if not withdrawn:
        return
    document_ids = [row.id for row in withdrawn]
    course_ids = {row.course_id for row in withdrawn}
    run_ids = sa.select(processing_runs.c.id).where(
        processing_runs.c.document_id.in_(document_ids)
    )
    affected_generations = sa.select(generation_requests.c.generation_id).join(
        generation_sources, generation_sources.c.request_id == generation_requests.c.id
    ).where(generation_sources.c.run_id.in_(run_ids))
    conn.execute(jobs.delete().where(sa.or_(
        sa.and_(jobs.c.kind == "ingest_document", jobs.c.target_id.in_(document_ids)),
        sa.and_(jobs.c.kind != "ingest_document", jobs.c.target_id.in_(affected_generations)),
    )))
    conn.execute(chunk_embeddings.delete().where(chunk_embeddings.c.run_id.in_(run_ids)))
    conn.execute(courses.update().where(courses.c.id.in_(course_ids)).values(
        corpus_revision=courses.c.corpus_revision + 1
    ))


def _account_courses(subject: UUID):
    return courses.c.id.in_(
        sa.select(memberships.c.course_id).where(memberships.c.user_id == subject)
        .union(sa.select(documents.c.course_id).where(documents.c.uploaded_by == subject))
    )


def _withdraw(conn: Connection, scope: str, subject: UUID) -> None:
    if scope == "document":
        _lock_courses(conn, courses.c.id.in_(
            sa.select(documents.c.course_id).where(documents.c.id == subject)
        ))
        _withdraw_documents(conn, documents.c.id == subject)
    elif scope == "course":
        _lock_courses(conn, courses.c.id == subject)
        conn.execute(courses.update().where(courses.c.id == subject).values(status="deletion_pending"))
        _withdraw_documents(conn, documents.c.course_id == subject)
        conn.execute(jobs.delete().where(jobs.c.course_id == subject))
    elif scope == "account":
        _lock_courses(conn, _account_courses(subject))
        conn.execute(sa.select(users.c.id).where(users.c.id == subject).with_for_update()).all()
        _lock_courses(conn, _account_courses(subject))
        peer = memberships.alias("peer")
        peer_user = users.alias("peer_user")
        active_peer = sa.exists(
            sa.select(1).select_from(peer.join(peer_user, peer.c.user_id == peer_user.c.id))
            .where(peer.c.course_id == memberships.c.course_id, peer.c.user_id != subject,
                   peer.c.status == "active", peer.c.role == "coordinator",
                   peer_user.c.status == "active")
        )
        if conn.scalar(sa.select(sa.exists(
            sa.select(1).select_from(memberships.join(courses, memberships.c.course_id == courses.c.id))
            .where(memberships.c.user_id == subject, memberships.c.role == "coordinator",
                   memberships.c.status == "active", courses.c.status.in_(("active", "archived")),
                   ~active_peer)
        ))):
            raise ValueError("LAST_COORDINATOR")
        conn.execute(users.update().where(users.c.id == subject).values(status="deletion_pending"))
        conn.execute(sessions.delete().where(sessions.c.user_id == subject))
        conn.execute(memberships.update().where(
            memberships.c.user_id == subject, memberships.c.status == "active"
        ).values(status="removed", left_at=sa.func.clock_timestamp()))
        _withdraw_documents(conn, documents.c.uploaded_by == subject)
        conn.execute(jobs.delete().where(jobs.c.requested_by == subject))


def request_purge(conn: Connection, *, request_id: UUID, scope: str, subject_id: UUID) -> UUID:
    """Registra una solicitud ya autorizada y retira su ámbito en la misma transacción."""
    require_restore_ready(conn)
    return _request_purge(conn, request_id=request_id, scope=scope, subject_id=subject_id)


def _request_purge(
    conn: Connection, *, request_id: UUID, scope: str, subject_id: UUID,
    requested_at: datetime | None = None,
) -> UUID:
    if scope not in {"document", "course", "account"}:
        raise ValueError("INVALID_DELETION_SCOPE")
    with conn.begin_nested():
        conn.execute(insert(deletion_requests).values(
            id=request_id, scope=scope, subject_id=subject_id,
            requested_at=sa.func.clock_timestamp() if requested_at is None else requested_at,
        ).on_conflict_do_nothing())
        by_id = conn.execute(sa.select(deletion_requests).where(
            deletion_requests.c.id == request_id
        )).mappings().one_or_none()
        if by_id is not None and (by_id.scope, by_id.subject_id) != (scope, subject_id):
            raise ValueError("DELETION_ID_CONFLICT")
        row = conn.execute(sa.select(deletion_requests).where(
            deletion_requests.c.scope == scope, deletion_requests.c.subject_id == subject_id
        ).with_for_update()).mappings().one()
        if row.completed_at is None:
            _withdraw(conn, scope, subject_id)
        conn.execute(insert(deletion_ledger).values(id=row.id).on_conflict_do_nothing())
        return row.id


def _delete_documents(conn: Connection, condition) -> None:
    conn.execute(documents.update().where(condition, documents.c.active_run_id.is_not(None))
                 .values(active_run_id=None))
    identifiers = sa.select(documents.c.id).where(condition)
    conn.execute(document_versions.delete().where(document_versions.c.document_id.in_(identifiers)))
    conn.execute(documents.delete().where(condition))


def delete_private_data(
    conn: Connection, *, course_id: UUID | None = None, user_id: UUID | None = None
) -> None:
    if course_id is None and user_id is None:
        raise ValueError("PURGE_SUBJECT_REQUIRED")
    # Las conversaciones referencian generaciones, por lo que se borran primero.
    for table in (conversations, generation_runs, learning_goals, study_sessions, mastery_events, jobs):
        condition = []
        if course_id is not None:
            condition.append(table.c.course_id == course_id)
        if user_id is not None:
            owner = table.c.requested_by if table is jobs else table.c.user_id
            condition.append(owner == user_id)
        conn.execute(table.delete().where(*condition))


def _purge(conn: Connection, row) -> None:
    subject = row.subject_id
    _withdraw(conn, row.scope, subject)
    if row.scope == "document":
        _delete_documents(conn, documents.c.id == subject)
    elif row.scope == "course":
        _delete_documents(conn, documents.c.course_id == subject)
        delete_private_data(conn, course_id=subject)
        conn.execute(memberships.delete().where(memberships.c.course_id == subject))
        conn.execute(courses.delete().where(courses.c.id == subject))
    else:
        _delete_documents(conn, documents.c.uploaded_by == subject)
        delete_private_data(conn, user_id=subject)
        conn.execute(memberships.delete().where(memberships.c.user_id == subject))
        conn.execute(courses.update().where(courses.c.created_by == subject).values(created_by=None))
        conn.execute(users.delete().where(users.c.id == subject))
    conn.execute(deletion_requests.update().where(deletion_requests.c.id == row.id).values(
        completed_at=sa.func.clock_timestamp()
    ))


def purge_pending(conn: Connection, *, limit: int = 25) -> int:
    """Reclama y completa un lote. Un rollback libera la reserva y conserva la solicitud."""
    if not 1 <= limit <= 100:
        raise ValueError("INVALID_PURGE_LIMIT")
    require_restore_ready(conn)
    persisted = sa.exists(sa.select(1).where(
        deletion_ledger.c.id == deletion_requests.c.id,
        deletion_ledger.c.persisted_at.is_not(None),
    ))
    pending = conn.execute(sa.select(deletion_requests).where(
        deletion_requests.c.completed_at.is_(None), persisted
    ).order_by(deletion_requests.c.requested_at, deletion_requests.c.id)
        .limit(limit).with_for_update(skip_locked=True)).mappings().all()
    for row in pending:
        _purge(conn, row)
    return len(pending)


def acknowledge_receipt(conn: Connection, request_id: UUID) -> bool:
    """El operador confirma que guardó el comprobante fuera de la base."""
    return conn.execute(deletion_ledger.update().where(
        deletion_ledger.c.id == request_id, deletion_ledger.c.persisted_at.is_(None)
    ).values(persisted_at=sa.func.clock_timestamp())).rowcount == 1


def pending_receipts(conn: Connection):
    """Lista comprobantes cuya copia externa aún no se ha confirmado."""
    return conn.execute(sa.select(
        deletion_requests.c.id, deletion_requests.c.scope, deletion_requests.c.subject_id,
        deletion_requests.c.requested_at, deletion_requests.c.completed_at,
    ).join(deletion_ledger, deletion_ledger.c.id == deletion_requests.c.id).where(
        deletion_ledger.c.persisted_at.is_(None)
    ).order_by(deletion_requests.c.requested_at, deletion_requests.c.id)).mappings().all()


def expire_receipts(conn: Connection, at: datetime) -> None:
    confirmed = sa.select(deletion_ledger.c.id).where(
        deletion_ledger.c.persisted_at <= at - timedelta(days=30)
    )
    conn.execute(deletion_requests.delete().where(
        deletion_requests.c.id.in_(confirmed),
        deletion_requests.c.completed_at <= at - timedelta(days=30),
    ))
