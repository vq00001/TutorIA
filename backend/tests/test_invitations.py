from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import ProgrammingError

from db.connection import set_user_context
from db.courses import create_course, get_member_course, leave_course, memberships, update_course
from db.invitations import (
    direct_invites,
    invite_member,
    list_my_invitations,
    respond_to_invitation,
)
from db.users import users


@pytest.fixture
def actors(postgres):
    identities = {name: uuid4() for name in ("owner", "recipient", "outsider", "pending")}
    postgres.execute(
        insert(users),
        [
            {
                "id": identifier,
                "nickname": f"u_{identifier.hex}",
                "email": f"{identifier.hex}@example.test",
                "display_name": "Synthetic",
                "password_hash": "test-only",
                "status": "pending" if name == "pending" else "active",
            }
            for name, identifier in identities.items()
        ],
    )
    return identities


def _as_user(conn, identifier):
    conn.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    set_user_context(conn, identifier)


def _email(identifier):
    return f"{identifier.hex}@example.test"


def test_invite_is_private_and_does_not_extend_an_existing_invitation(postgres, actors):
    _as_user(postgres, actors["owner"])
    course_id = create_course(postgres, name="Cálculo")
    assert invite_member(postgres, course_id, _email(actors["pending"])) is None
    assert invite_member(postgres, course_id, _email(actors["owner"])) is None
    assert invite_member(postgres, course_id, "absent@example.test") is None
    invite_member(postgres, course_id, _email(actors["recipient"]).upper())

    _as_user(postgres, actors["recipient"])
    (first,) = list_my_invitations(postgres)
    assert first["course_id"] == course_id
    assert first["course_name"] == "Cálculo"
    assert first["expires_at"] - first["created_at"] == timedelta(days=7)
    assert get_member_course(postgres, course_id) is None
    with pytest.raises(ProgrammingError), postgres.begin_nested():
        postgres.execute(select(direct_invites))

    _as_user(postgres, actors["outsider"])
    assert list_my_invitations(postgres) == []
    assert respond_to_invitation(postgres, first["id"], accept=True) is None
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        invite_member(postgres, course_id, _email(actors["outsider"]))

    _as_user(postgres, actors["owner"])
    invite_member(postgres, course_id, _email(actors["recipient"]))
    _as_user(postgres, actors["recipient"])
    assert list_my_invitations(postgres) == [first]

    postgres.exec_driver_sql("RESET ROLE")
    assert postgres.scalar(select(func.count()).select_from(direct_invites)) == 1


def test_rejection_can_be_replayed_and_reinvite_uses_a_fresh_identifier(postgres, actors):
    _as_user(postgres, actors["owner"])
    course_id = create_course(postgres, name="Curso")
    invite_member(postgres, course_id, _email(actors["recipient"]))

    _as_user(postgres, actors["recipient"])
    (previous,) = list_my_invitations(postgres)
    assert respond_to_invitation(postgres, previous["id"], accept=False) == course_id
    assert respond_to_invitation(postgres, previous["id"], accept=False) == course_id
    assert respond_to_invitation(postgres, previous["id"], accept=True) is None
    assert get_member_course(postgres, course_id) is None
    assert list_my_invitations(postgres) == []

    _as_user(postgres, actors["owner"])
    invite_member(postgres, course_id, _email(actors["recipient"]))
    _as_user(postgres, actors["recipient"])
    (current,) = list_my_invitations(postgres)
    assert current["id"] != previous["id"]
    assert respond_to_invitation(postgres, previous["id"], accept=True) is None
    assert respond_to_invitation(postgres, current["id"], accept=True) == course_id
    postgres.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    assert respond_to_invitation(postgres, current["id"], accept=True) == course_id
    assert get_member_course(postgres, course_id)["my_role"] == "student"
    assert list_my_invitations(postgres) == []


def test_new_invitation_reactivates_a_membership_after_leaving(postgres, actors):
    _as_user(postgres, actors["owner"])
    course_id = create_course(postgres, name="Curso")
    invite_member(postgres, course_id, _email(actors["recipient"]))
    _as_user(postgres, actors["recipient"])
    (first,) = list_my_invitations(postgres)
    assert respond_to_invitation(postgres, first["id"], accept=True) == course_id
    leave_course(postgres, course_id)
    assert respond_to_invitation(postgres, first["id"], accept=True) == course_id
    assert get_member_course(postgres, course_id) is None

    _as_user(postgres, actors["owner"])
    invite_member(postgres, course_id, _email(actors["recipient"]))
    _as_user(postgres, actors["recipient"])
    (new,) = list_my_invitations(postgres)
    assert respond_to_invitation(postgres, new["id"], accept=True) == course_id
    member = (
        postgres.execute(
            select(memberships).where(
                memberships.c.course_id == course_id,
                memberships.c.user_id == actors["recipient"],
            )
        )
        .mappings()
        .one()
    )
    assert (member["role"], member["status"], member["left_at"], member["row_version"]) == (
        "student",
        "active",
        None,
        3,
    )


def test_expired_and_archived_invitations_cannot_be_accepted(postgres, actors):
    _as_user(postgres, actors["owner"])
    course_id = create_course(postgres, name="Curso")
    invite_member(postgres, course_id, _email(actors["recipient"]))
    _as_user(postgres, actors["recipient"])
    (expired,) = list_my_invitations(postgres)
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        update(direct_invites)
        .where(direct_invites.c.id == expired["id"])
        .values(
            created_at=func.now() - timedelta(days=8),
            expires_at=func.now() - timedelta(days=1),
        )
    )
    _as_user(postgres, actors["recipient"])
    assert list_my_invitations(postgres) == []
    assert respond_to_invitation(postgres, expired["id"], accept=True) is None

    _as_user(postgres, actors["owner"])
    invite_member(postgres, course_id, _email(actors["recipient"]))
    _as_user(postgres, actors["recipient"])
    (current,) = list_my_invitations(postgres)
    assert current["id"] != expired["id"]
    _as_user(postgres, actors["owner"])
    assert update_course(postgres, course_id, 1, status="archived")
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        invite_member(postgres, course_id, _email(actors["outsider"]))

    _as_user(postgres, actors["recipient"])
    assert list_my_invitations(postgres) == []
    assert respond_to_invitation(postgres, current["id"], accept=True) is None
