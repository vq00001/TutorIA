from contextlib import nullcontext
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select

from backend import http_api
from backend.routes import dependencies
from db.audit import record_audit_event
from db.courses import create_course, memberships
from db.invitations import invite_member
from db.sessions import issue_session
from db.study_sessions import start_study_session, study_intervals
from db.users import users
from test_courses import _as_user, _user


def test_dashboard_activity_and_notifications_use_private_data(postgres, monkeypatch):
    owner, student, invitee = (_user(postgres) for _ in range(3))
    tokens = {user: issue_session(postgres, user) for user in (owner, student, invitee)}
    email = postgres.scalar(select(users.c.email).where(users.c.id == invitee))
    _as_user(postgres, owner)
    course = create_course(postgres, name="Dashboard")
    invite_member(postgres, course, email)
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=student, role="student"))
    _as_user(postgres, student)
    event = record_audit_event(
        postgres, action="course.study", entity_type="course", request_id=uuid4(),
        result="success", course_id=course,
    )
    session = start_study_session(postgres, course)
    postgres.exec_driver_sql("RESET ROLE")
    now = postgres.scalar(select(func.clock_timestamp()))
    postgres.execute(insert(study_intervals).values(
        session_id=session, sequence_no=1, course_id=course, user_id=student,
        start_at=now - timedelta(seconds=30), end_at=now,
    ))
    _as_user(postgres, owner)
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)

    def get(path, user, **kwargs):
        response = client.get(
            "/api/v1" + path, headers={"Authorization": f"Bearer {tokens[user]}"}, **kwargs
        )
        assert response.status_code == 200
        return response.json()

    summary = get("/dashboard/summary", student)["data"]
    assert len(summary["studyByDay"]) == 7
    assert sum(day["seconds"] for day in summary["studyByDay"]) == 30
    assert get("/dashboard/summary", owner)["data"]["studyHours"] == 0
    assert get("/dashboard/summary", invitee)["data"]["pendingInvitations"] == 1

    activity = get("/activity", student, params={"type": "course", "pageSize": 1})
    assert [row["id"] for row in activity["data"]] == [str(event)]
    assert get("/activity", student, params={"page": 2})["meta"]["total"] == 1
    assert get("/activity", owner)["data"] == []

    notifications = get("/notifications", invitee)["data"]
    assert [item["courseId"] for item in notifications] == [str(course)]
    assert get(f"/courses/{course}/notifications", invitee)["data"] == notifications
    assert get(f"/courses/{course}/notifications", owner)["data"] == []
