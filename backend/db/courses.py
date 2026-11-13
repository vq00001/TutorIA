"""Persistencia de clases y membresías, adaptada del esquema original de TutorIA."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    Uuid,
    func,
    select,
    text,
    true,
    tuple_,
    update,
)
from sqlalchemy.engine import Connection, RowMapping

from .users import users

courses = Table(
    "courses",
    users.metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", String(100), nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("objectives", Text, nullable=False, server_default=""),
    Column("subject", String(150), nullable=False, server_default=""),
    Column("color", String(30), nullable=False, server_default="mauve"),
    Column("icon", String(16), nullable=False, server_default="✦"),
    Column("created_by", Uuid, ForeignKey("app.users.id", ondelete="RESTRICT")),
    Column("status", Text, nullable=False, server_default="active"),
    Column("corpus_revision", BigInteger, nullable=False, server_default=text("0")),
    Column("graph_revision", BigInteger, nullable=False, server_default=text("0")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("row_version", BigInteger, nullable=False, server_default=text("1")),
    CheckConstraint("length(btrim(name)) BETWEEN 1 AND 100", name="courses_name_present"),
    CheckConstraint(
        "color IN ('mauve','blue','green','peach','pink','yellow','teal','lavender')",
        name="courses_valid_color",
    ),
    CheckConstraint(
        "status IN ('active','archived','deletion_pending')",
        name="courses_valid_status",
    ),
)

memberships = Table(
    "memberships",
    users.metadata,
    Column("course_id", Uuid, ForeignKey("app.courses.id", ondelete="RESTRICT"), primary_key=True),
    Column("user_id", Uuid, ForeignKey("app.users.id", ondelete="RESTRICT"), primary_key=True),
    Column("role", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="active"),
    Column("joined_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("left_at", DateTime(timezone=True)),
    Column("row_version", BigInteger, nullable=False, server_default=text("1")),
    CheckConstraint("role IN ('student','coordinator')", name="memberships_valid_role"),
    CheckConstraint("status IN ('active','left','removed')", name="memberships_valid_status"),
)
Index("memberships_user_idx", memberships.c.user_id, memberships.c.status, memberships.c.course_id)

_editable_course_fields = {
    "name",
    "description",
    "objectives",
    "subject",
    "color",
    "icon",
    "status",
}


def install_courses(conn: Connection) -> None:
    """Instala clases, membresías, permisos e invariantes."""
    courses.create(conn, checkfirst=True)
    memberships.create(conn, checkfirst=True)

    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.course_actor() RETURNS uuid
        LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.has_course_role(cid uuid, allowed_roles text[])
        RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT EXISTS (
                SELECT FROM app.memberships AS m
                JOIN app.users AS u ON u.id = m.user_id
                JOIN app.courses AS c ON c.id = m.course_id
                WHERE m.course_id = cid AND m.user_id = app.course_actor()
                  AND m.status = 'active' AND m.role = ANY(allowed_roles)
                  AND u.status = 'active' AND c.status IN ('active', 'archived')
            )
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.create_course(
            cid uuid, title text, descr text, objs text, topic text, palette text, symbol text
        ) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE actor uuid := app.course_actor();
        BEGIN
            PERFORM 1 FROM app.users WHERE id = actor AND status = 'active' FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;

            INSERT INTO app.courses (id, name, description, objectives, subject, color, icon, created_by)
            VALUES (cid, title, descr, objs, topic, palette, symbol, actor);
            INSERT INTO app.memberships (course_id, user_id, role)
            VALUES (cid, actor, 'coordinator');
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.course_members(cid uuid)
        RETURNS TABLE (
            user_id uuid, nickname text, display_name text, role text,
            status text, joined_at timestamptz, row_version bigint
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT u.id, u.nickname::text, u.display_name::text, m.role,
                   m.status, m.joined_at, m.row_version
            FROM app.memberships AS m JOIN app.users AS u ON u.id = m.user_id
            WHERE m.course_id = cid AND m.status = 'active' AND u.status = 'active'
              AND app.has_course_role(cid, ARRAY['student', 'coordinator'])
            ORDER BY u.display_name, u.id
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.change_course_member(
            cid uuid, member_id uuid, new_role text, new_status text, expected_version bigint
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            PERFORM 1 FROM app.courses WHERE id = cid FOR UPDATE;
            IF NOT app.has_course_role(cid, ARRAY['coordinator']) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            UPDATE app.memberships
            SET role = new_role, status = new_status,
                left_at = CASE WHEN new_status = 'active' THEN NULL ELSE clock_timestamp() END
            WHERE course_id = cid AND user_id = member_id AND row_version = expected_version;
            RETURN FOUND;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.leave_course(cid uuid) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            PERFORM 1 FROM app.courses WHERE id = cid FOR UPDATE;
            IF NOT app.has_course_role(cid, ARRAY['student', 'coordinator']) THEN
                RAISE EXCEPTION 'NOT_AUTHORIZED' USING ERRCODE = '42501';
            END IF;
            UPDATE app.memberships SET status = 'left', left_at = clock_timestamp()
            WHERE course_id = cid AND user_id = app.course_actor();
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.touch_course_row() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'courses' THEN
                NEW.updated_at := clock_timestamp();
            ELSIF ROW(NEW.course_id, NEW.user_id) IS DISTINCT FROM ROW(OLD.course_id, OLD.user_id) THEN
                RAISE EXCEPTION 'MEMBERSHIP_IDENTITY_IMMUTABLE' USING ERRCODE = '23514';
            END IF;
            NEW.row_version := OLD.row_version + 1;
            RETURN NEW;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.require_coordinator() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE cid uuid;
        BEGIN
            IF TG_TABLE_NAME = 'courses' THEN
                cid := NEW.id;
            ELSIF TG_OP = 'DELETE' THEN
                cid := OLD.course_id;
            ELSE
                cid := NEW.course_id;
            END IF;
            PERFORM 1 FROM app.courses WHERE id = cid FOR UPDATE;
            IF EXISTS (SELECT FROM app.courses WHERE id = cid AND status IN ('active', 'archived'))
              AND NOT EXISTS (
                SELECT FROM app.memberships
                WHERE course_id = cid AND status = 'active' AND role = 'coordinator'
              ) THEN
                RAISE EXCEPTION 'LAST_COORDINATOR' USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $$"""
    )

    conn.exec_driver_sql("DROP TRIGGER IF EXISTS course_touch ON app.courses")
    conn.exec_driver_sql(
        """CREATE TRIGGER course_touch BEFORE UPDATE ON app.courses
        FOR EACH ROW EXECUTE FUNCTION app.touch_course_row()"""
    )
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS membership_touch ON app.memberships")
    conn.exec_driver_sql(
        """CREATE TRIGGER membership_touch BEFORE UPDATE ON app.memberships
        FOR EACH ROW EXECUTE FUNCTION app.touch_course_row()"""
    )
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS course_coordinator ON app.courses")
    conn.exec_driver_sql(
        """CREATE CONSTRAINT TRIGGER course_coordinator AFTER INSERT OR UPDATE ON app.courses
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.require_coordinator()"""
    )
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS membership_coordinator ON app.memberships")
    conn.exec_driver_sql(
        """CREATE CONSTRAINT TRIGGER membership_coordinator
        AFTER INSERT OR UPDATE OR DELETE ON app.memberships
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.require_coordinator()"""
    )

    conn.exec_driver_sql("ALTER TABLE app.courses ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.courses FORCE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.memberships ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.memberships FORCE ROW LEVEL SECURITY")
    conn.exec_driver_sql("DROP POLICY IF EXISTS courses_read ON app.courses")
    conn.exec_driver_sql(
        """CREATE POLICY courses_read ON app.courses FOR SELECT TO tutoria_api
        USING (app.has_course_role(id, ARRAY['student', 'coordinator']))"""
    )
    conn.exec_driver_sql("DROP POLICY IF EXISTS courses_update ON app.courses")
    conn.exec_driver_sql(
        """CREATE POLICY courses_update ON app.courses FOR UPDATE TO tutoria_api
        USING (app.has_course_role(id, ARRAY['coordinator']))
        WITH CHECK (status IN ('active', 'archived') AND app.has_course_role(id, ARRAY['coordinator']))"""
    )
    conn.exec_driver_sql("DROP POLICY IF EXISTS memberships_read ON app.memberships")
    conn.exec_driver_sql(
        """CREATE POLICY memberships_read ON app.memberships FOR SELECT TO tutoria_api
        USING (user_id = app.course_actor() OR app.has_course_role(course_id, ARRAY['coordinator']))"""
    )
    conn.exec_driver_sql("GRANT SELECT ON app.courses, app.memberships TO tutoria_api")
    conn.exec_driver_sql(
        "GRANT UPDATE (name, description, objectives, subject, color, icon, status) "
        "ON app.courses TO tutoria_api"
    )

    entrypoints = (
        "app.course_actor(), app.has_course_role(uuid, text[]), "
        "app.create_course(uuid, text, text, text, text, text, text), app.course_members(uuid), "
        "app.change_course_member(uuid, uuid, text, text, bigint), app.leave_course(uuid)"
    )
    conn.exec_driver_sql(f"REVOKE ALL ON FUNCTION {entrypoints} FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {entrypoints} TO tutoria_api")
    conn.exec_driver_sql(
        "REVOKE ALL ON FUNCTION app.touch_course_row(), app.require_coordinator() FROM PUBLIC"
    )


def create_course(
    conn: Connection,
    *,
    name: str,
    description: str = "",
    objectives: str = "",
    subject: str = "",
    color: str = "mauve",
    icon: str = "✦",
) -> UUID:
    """Crea la clase y su primera membresía de coordinación en la misma transacción."""
    identifier = uuid4()
    conn.execute(
        select(
            func.app.create_course(identifier, name, description, objectives, subject, color, icon)
        )
    )
    return identifier


def _member_courses():
    return (
        select(courses, memberships.c.role.label("my_role"))
        .join(memberships, memberships.c.course_id == courses.c.id)
        .where(memberships.c.user_id == func.app.course_actor(), memberships.c.status == "active")
    )


def list_member_courses(
    conn: Connection,
    *,
    limit: int,
    after: tuple[datetime, UUID] | None = None,
) -> list[RowMapping]:
    """Lista las clases visibles del usuario con cursor por fecha e identificador."""
    if not 1 <= limit <= 100:
        raise ValueError("limit debe estar entre 1 y 100")
    statement = _member_courses().order_by(courses.c.created_at, courses.c.id).limit(limit)
    if after is not None:
        statement = statement.where(tuple_(courses.c.created_at, courses.c.id) > tuple_(*after))
    return list(conn.execute(statement).mappings())


def page_member_courses(
    conn: Connection, *, page: int, page_size: int
) -> tuple[list[RowMapping], int]:
    """Devuelve una página y el total de clases visibles en una sola consulta."""
    visible = _member_courses().cte("visible_courses")
    total = select(func.count()).select_from(visible).scalar_subquery()
    window = (
        select(visible).order_by(visible.c.created_at, visible.c.id)
        .offset((page - 1) * page_size).limit(page_size).subquery()
    )
    counts = select(total.label("total")).subquery()
    statement = (
        select(window, counts.c.total)
        .select_from(counts.outerjoin(window, true()))
        .order_by(window.c.created_at, window.c.id)
    )
    rows = conn.execute(statement).mappings().all()
    return [row for row in rows if row.id is not None], rows[0].total


def get_member_course(conn: Connection, course_id: UUID) -> RowMapping | None:
    return conn.execute(_member_courses().where(courses.c.id == course_id)).mappings().one_or_none()


def update_course(conn: Connection, course_id: UUID, expected_version: int, **changes) -> bool:
    """Actualiza campos editables si la versión y los permisos siguen vigentes."""
    if not changes or changes.keys() - _editable_course_fields:
        raise ValueError("Se requieren campos editables de la clase")
    statement = (
        update(courses)
        .where(courses.c.id == course_id, courses.c.row_version == expected_version)
        .values(**changes)
        .returning(courses.c.id)
    )
    return conn.scalar(statement) is not None


def list_course_members(conn: Connection, course_id: UUID) -> list[RowMapping]:
    """Devuelve solo datos públicos de miembros activos de una clase visible."""
    members = func.app.course_members(course_id).table_valued(
        "user_id",
        "nickname",
        "display_name",
        "role",
        "status",
        "joined_at",
        "row_version",
    )
    return list(conn.execute(select(members)).mappings())


def change_course_member(
    conn: Connection,
    course_id: UUID,
    user_id: UUID,
    *,
    role: str,
    status: str,
    expected_version: int,
) -> bool:
    """Cambia rol y estado con control de versión, conservando la última coordinación."""
    statement = select(
        func.app.change_course_member(course_id, user_id, role, status, expected_version)
    )
    return conn.scalar(statement) is True


def leave_course(conn: Connection, course_id: UUID) -> None:
    conn.execute(select(func.app.leave_course(course_id)))
