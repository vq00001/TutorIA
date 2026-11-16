"""Invitaciones directas a clases, adaptadas del esquema original de TutorIA."""

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    select,
)
from sqlalchemy.engine import Connection, RowMapping

from .courses import courses

direct_invites = Table(
    "direct_invites",
    courses.metadata,
    Column("id", Uuid, primary_key=True, server_default=func.gen_random_uuid()),
    Column("course_id", Uuid, ForeignKey("app.courses.id", ondelete="CASCADE"), nullable=False),
    Column("recipient_id", Uuid, ForeignKey("app.users.id", ondelete="CASCADE"), nullable=False),
    Column("inviter_id", Uuid, ForeignKey("app.users.id", ondelete="SET NULL")),
    Column("status", Text, nullable=False, server_default="pending"),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    ),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "status IN ('pending', 'accepted', 'declined')",
        name="direct_invites_valid_status",
    ),
    CheckConstraint("expires_at > created_at", name="direct_invites_expiration"),
    UniqueConstraint("course_id", "recipient_id", name="direct_invites_course_recipient_key"),
)


def install_invitations(conn: Connection) -> None:
    """Instala el almacén privado y las operaciones de invitación."""
    direct_invites.create(conn, checkfirst=True)
    conn.exec_driver_sql("ALTER TABLE app.direct_invites ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.direct_invites FORCE ROW LEVEL SECURITY")
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.invite_member(cid uuid, candidate_email text)
        RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.course_actor();
            recipient uuid;
            issued_at timestamptz;
        BEGIN
            PERFORM 1 FROM app.courses WHERE id = cid AND status = 'active' FOR UPDATE;
            IF NOT FOUND OR NOT app.has_course_role(cid, ARRAY['coordinator']) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;

            SELECT u.id INTO recipient
            FROM app.users AS u
            WHERE u.email = lower(btrim(candidate_email)) AND u.status = 'active';
            IF recipient IS NULL OR EXISTS (
                SELECT FROM app.memberships
                WHERE course_id = cid AND user_id = recipient AND status = 'active'
            ) THEN
                RETURN;
            END IF;

            issued_at := clock_timestamp();
            INSERT INTO app.direct_invites AS i (
                course_id, recipient_id, inviter_id, created_at, expires_at
            ) VALUES (cid, recipient, actor, issued_at, issued_at + interval '7 days')
            ON CONFLICT (course_id, recipient_id) DO UPDATE
            SET id = EXCLUDED.id, inviter_id = EXCLUDED.inviter_id, status = 'pending',
                created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at
            WHERE i.status <> 'pending' OR i.expires_at <= issued_at;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.my_invitations()
        RETURNS TABLE (
            id uuid, course_id uuid, course_name text, created_at timestamptz, expires_at timestamptz
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT i.id, i.course_id, c.name::text, i.created_at, i.expires_at
            FROM app.direct_invites AS i
            JOIN app.courses AS c ON c.id = i.course_id
            JOIN app.users AS u ON u.id = i.recipient_id
            WHERE i.recipient_id = app.course_actor() AND u.status = 'active'
              AND c.status = 'active' AND i.status = 'pending'
              AND i.expires_at > statement_timestamp()
            ORDER BY i.created_at DESC, i.id
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.respond_invitation(invitation_id uuid, accept_invite boolean)
        RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            actor uuid := app.course_actor();
            invitation app.direct_invites;
            decision text;
        BEGIN
            IF accept_invite IS NULL THEN
                RETURN NULL;
            END IF;
            decision := CASE WHEN accept_invite THEN 'accepted' ELSE 'declined' END;

            SELECT * INTO invitation FROM app.direct_invites
            WHERE id = invitation_id AND recipient_id = actor;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;

            -- Comparte el orden de bloqueo de las operaciones de membresía.
            PERFORM 1 FROM app.courses WHERE id = invitation.course_id AND status = 'active' FOR UPDATE;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            PERFORM 1 FROM app.users WHERE id = actor AND status = 'active' FOR SHARE;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;

            SELECT * INTO invitation FROM app.direct_invites
            WHERE id = invitation_id AND recipient_id = actor FOR UPDATE;
            IF NOT FOUND OR invitation.expires_at <= clock_timestamp() THEN
                RETURN NULL;
            END IF;
            IF invitation.status <> 'pending' THEN
                RETURN CASE WHEN invitation.status = decision THEN invitation.course_id END;
            END IF;

            IF accept_invite THEN
                INSERT INTO app.memberships AS m (course_id, user_id, role)
                VALUES (invitation.course_id, actor, 'student')
                ON CONFLICT (course_id, user_id) DO UPDATE
                SET role = 'student', status = 'active', left_at = NULL, joined_at = clock_timestamp()
                WHERE m.status <> 'active';
            END IF;

            UPDATE app.direct_invites SET status = decision WHERE id = invitation_id;
            RETURN invitation.course_id;
        END
        $$"""
    )
    functions = (
        "app.invite_member(uuid, text), app.my_invitations(), app.respond_invitation(uuid, boolean)"
    )
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {functions} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {functions} TO tutoria_api")


def invite_member(conn: Connection, course_id: UUID, email: str) -> None:
    """Invita sin revelar si el correo corresponde a una cuenta elegible."""
    conn.execute(select(func.app.invite_member(course_id, email)))


def list_my_invitations(conn: Connection) -> list[RowMapping]:
    """Lista las invitaciones pendientes y vigentes de la cuenta actual."""
    invitations = func.app.my_invitations().table_valued(
        "id",
        "course_id",
        "course_name",
        "created_at",
        "expires_at",
    )
    statement = select(invitations).order_by(invitations.c.created_at.desc(), invitations.c.id)
    return list(conn.execute(statement).mappings())


def respond_to_invitation(conn: Connection, invitation_id: UUID, *, accept: bool) -> UUID | None:
    """Aplica la decisión y devuelve la clase si la invitación sigue siendo válida."""
    return conn.scalar(select(func.app.respond_invitation(invitation_id, accept)))
