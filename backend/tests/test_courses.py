from uuid import uuid4

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError, ProgrammingError

from db.connection import set_user_context
from db.courses import (
    change_course_member,
    courses,
    create_course,
    get_member_course,
    leave_course,
    list_course_members,
    list_member_courses,
    memberships,
    update_course,
)
from db.users import users


def _user(conn, status="active"):
    identifier = uuid4()
    conn.execute(
        insert(users).values(
            id=identifier,
            nickname=f"u_{identifier.hex}",
            email=f"{identifier.hex}@example.test",
            display_name="Synthetic",
            password_hash="test-only",
            status=status,
        )
    )
    return identifier


def _as_user(conn, identifier):
    conn.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    set_user_context(conn, identifier)


def _verify_deferred_constraints(conn):
    conn.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    conn.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")


def test_course_creation_bootstraps_coordinator_and_paginates_own_courses(postgres):
    first, second = _user(postgres), _user(postgres)
    _as_user(postgres, first)
    a = create_course(postgres, name="Cálculo", subject="Matemáticas")
    b = create_course(postgres, name="Física")
    _as_user(postgres, second)
    hidden = create_course(postgres, name="Clase ajena")
    _verify_deferred_constraints(postgres)

    _as_user(postgres, first)
    assert get_member_course(postgres, hidden) is None
    assert get_member_course(postgres, a)["subject"] == "Matemáticas"
    (initial,) = list_member_courses(postgres, limit=1)
    assert initial["my_role"] == "coordinator"
    (following,) = list_member_courses(
        postgres, limit=1, after=(initial["created_at"], initial["id"])
    )
    assert {initial["id"], following["id"]} == {a, b}


def test_only_active_authenticated_accounts_can_create_courses(postgres):
    pending = _user(postgres, status="pending")
    for identifier in (None, pending):
        _as_user(postgres, identifier)
        with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
            create_course(postgres, name="Sin autorización")

    with pytest.raises(ProgrammingError), postgres.begin_nested():
        postgres.execute(insert(courses).values(id=uuid4(), name="Inserción directa"))


def test_member_list_exposes_public_fields_and_only_coordinator_can_edit(postgres):
    owner, student, outsider = (_user(postgres) for _ in range(3))
    _as_user(postgres, owner)
    course_id = create_course(postgres, name="Curso")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        insert(memberships).values(course_id=course_id, user_id=student, role="student")
    )

    _as_user(postgres, outsider)
    assert list_course_members(postgres, course_id) == []
    _as_user(postgres, student)
    roster = list_course_members(postgres, course_id)
    assert {row["user_id"] for row in roster} == {owner, student}
    assert set(roster[0]) == {
        "user_id",
        "nickname",
        "display_name",
        "role",
        "status",
        "joined_at",
        "row_version",
    }
    assert not update_course(postgres, course_id, 1, name="Cambio ajeno")
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        change_course_member(
            postgres, course_id, student, role="coordinator", status="active", expected_version=1
        )

    _as_user(postgres, owner)
    assert update_course(postgres, course_id, 1, name="Actualizado", status="archived")
    updated = get_member_course(postgres, course_id)
    assert (updated["name"], updated["status"], updated["row_version"]) == (
        "Actualizado",
        "archived",
        2,
    )
    assert not update_course(postgres, course_id, 1, name="Versión anterior")
    assert change_course_member(
        postgres, course_id, student, role="coordinator", status="active", expected_version=1
    )
    assert not change_course_member(
        postgres, course_id, student, role="student", status="active", expected_version=1
    )
    _verify_deferred_constraints(postgres)


def test_last_coordinator_cannot_leave_or_be_demoted(postgres):
    # Adapted from DAT_14: enforce the invariant in PostgreSQL at transaction end.
    owner = _user(postgres)
    _as_user(postgres, owner)
    course_id = create_course(postgres, name="Curso")
    _verify_deferred_constraints(postgres)

    with pytest.raises(IntegrityError, match="LAST_COORDINATOR"), postgres.begin_nested():
        leave_course(postgres, course_id)
        _verify_deferred_constraints(postgres)

    with pytest.raises(IntegrityError, match="LAST_COORDINATOR"), postgres.begin_nested():
        change_course_member(
            postgres, course_id, owner, role="student", status="active", expected_version=1
        )
        _verify_deferred_constraints(postgres)

    assert get_member_course(postgres, course_id)["my_role"] == "coordinator"


def test_leaving_preserves_membership_history_and_removes_course_access(postgres):
    owner, student = _user(postgres), _user(postgres)
    _as_user(postgres, owner)
    course_id = create_course(postgres, name="Curso")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        insert(memberships).values(course_id=course_id, user_id=student, role="student")
    )

    _as_user(postgres, student)
    leave_course(postgres, course_id)
    _verify_deferred_constraints(postgres)
    assert get_member_course(postgres, course_id) is None
    membership = (
        postgres.execute(
            select(memberships).where(
                memberships.c.course_id == course_id,
                memberships.c.user_id == student,
            )
        )
        .mappings()
        .one()
    )
    assert membership["status"] == "left"
    assert membership["left_at"] is not None
    assert membership["row_version"] == 2
