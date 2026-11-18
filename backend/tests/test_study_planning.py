from dataclasses import replace
from datetime import date, timedelta

import pytest
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError, ProgrammingError

from db.courses import courses
from db.learning_catalog import add_prerequisite, create_concept
from db.learning_plans import (
    build_roadmap,
    create_learning_goal,
    list_learning_goals,
    list_roadmap_steps,
    list_roadmaps,
    set_learning_goal_status,
)
from db.study_sessions import (
    finish_study_session,
    get_study_session,
    heartbeat_study_session,
    list_study_intervals,
    list_study_sessions,
    start_study_session,
    study_sessions,
)

from . import test_exams
from .test_exam_attempts import ALTERNATIVES, _exam
from .test_exams import _role
from .test_learning_progress import _submit_choice

planning_context = test_exams.exam_context


def test_roadmap_orders_transitive_prerequisites_and_versions_graph_changes(
    postgres, planning_context
):
    _, _, course, _ = planning_context
    target = create_concept(postgres, course, label="A Meta")
    branch = create_concept(postgres, course, label="B Rama")
    base = create_concept(postgres, course, label="C Base")
    later = create_concept(postgres, course, label="D Nuevo")
    create_concept(postgres, course, label="Sin relación")
    add_prerequisite(postgres, course, base, branch)
    add_prerequisite(postgres, course, branch, target)
    add_prerequisite(postgres, course, base, target)
    deadline = date(2027, 1, 15)
    goal = create_learning_goal(
        postgres,
        course,
        title="Aprender la materia",
        concept_ids=[target, branch, target],
        target_date=deadline,
    )
    (entry,) = list_learning_goals(postgres, course)
    assert (entry["id"], entry["status"], entry["target_date"]) == (goal, "active", deadline)
    assert set(entry["concept_ids"]) == {target, branch}
    first = build_roadmap(postgres, course, goal)
    assert build_roadmap(postgres, course, goal) == first
    steps = list_roadmap_steps(postgres, course, first)
    assert [(r["position"], r["concept_id"], r["mastery_level"]) for r in steps] == [
        (1, base, "unknown"),
        (2, branch, "unknown"),
        (3, target, "unknown"),
    ]

    add_prerequisite(postgres, course, later, base)
    second = build_roadmap(postgres, course, goal)
    assert [(r["id"], r["version_no"]) for r in list_roadmaps(postgres, course, goal)] == [
        (second, 2),
        (first, 1),
    ]
    assert [r["concept_id"] for r in list_roadmap_steps(postgres, course, second)] == [
        later,
        base,
        branch,
        target,
    ]
    assert list_roadmap_steps(postgres, course, first) == steps
    assert set_learning_goal_status(postgres, course, goal, "completed") == goal
    with pytest.raises(IntegrityError, match="GOAL_INACTIVE"), postgres.begin_nested():
        build_roadmap(postgres, course, goal)
    with pytest.raises(IntegrityError, match="INVALID_GOAL_TRANSITION"), postgres.begin_nested():
        set_learning_goal_status(postgres, course, goal, "active")


def test_saved_roadmap_steps_show_current_mastery(postgres, planning_context):
    _, _, course, _ = planning_context
    concept = create_concept(postgres, course, label="Derivadas")
    goal = create_learning_goal(postgres, course, title="Derivación", concept_ids=[concept])
    route = build_roadmap(postgres, course, goal)
    assert list_roadmap_steps(postgres, course, route)[0]["mastery_level"] == "unknown"

    _, exam, (question,) = _exam(postgres, course, [replace(ALTERNATIVES, concept_id=concept)])
    _submit_choice(postgres, course, exam, question, correct=False)
    assert list_roadmap_steps(postgres, course, route)[0]["mastery_level"] == "reinforce"
    assert build_roadmap(postgres, course, goal) == route


def test_goals_routes_and_study_are_private_by_user_and_class(postgres, planning_context):
    owner, peer, course, other = planning_context
    concept = create_concept(postgres, course, label="Local")
    foreign = create_concept(postgres, other, label="Otra clase")
    for targets in ([], [concept, foreign]):
        with pytest.raises(IntegrityError, match="INVALID_GOAL_CONCEPTS"), postgres.begin_nested():
            create_learning_goal(postgres, course, title="Meta inválida", concept_ids=targets)
    assert list_learning_goals(postgres, course) == []
    goal = create_learning_goal(postgres, course, title="Meta privada", concept_ids=[concept])
    route = build_roadmap(postgres, course, goal)
    session = start_study_session(postgres, course)
    assert start_study_session(postgres, other) != session

    _role(postgres, peer)
    assert list_learning_goals(postgres, course) == []
    assert list_roadmaps(postgres, course, goal) == []
    assert list_roadmap_steps(postgres, course, route) == []
    assert get_study_session(postgres, course, session) is None
    assert list_study_intervals(postgres, course, session) == []
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        build_roadmap(postgres, course, goal)
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        finish_study_session(postgres, course, session)
    assert create_learning_goal(postgres, course, title="Meta del alumno", concept_ids=[concept])
    assert start_study_session(postgres, course) != session

    _role(postgres, owner)
    assert get_study_session(postgres, other, session) is None
    postgres.execute(update(courses).where(courses.c.id == course).values(status="archived"))
    assert list_learning_goals(postgres, course)[0]["id"] == goal
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        start_study_session(postgres, course)


def test_study_credits_bounded_intervals_and_closes_expired_sessions(postgres, planning_context):
    owner, _, course, _ = planning_context
    session = start_study_session(postgres, course)
    assert start_study_session(postgres, course) == session
    assert heartbeat_study_session(postgres, course, session) == 0

    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        update(study_sessions)
        .where(study_sessions.c.id == session)
        .values(
            started_at=func.now() - timedelta(seconds=90),
            last_heartbeat_at=func.now() - timedelta(seconds=80),
        )
    )
    _role(postgres, owner)
    assert heartbeat_study_session(postgres, course, session) == 30
    assert heartbeat_study_session(postgres, course, session) == 30
    (interval,) = list_study_intervals(postgres, course, session)
    assert (interval["sequence_no"], interval["end_at"] - interval["start_at"]) == (
        1,
        timedelta(seconds=30),
    )
    assert finish_study_session(postgres, course, session) == 30
    assert finish_study_session(postgres, course, session) == 30
    assert get_study_session(postgres, course, session)["ended_at"] is not None

    expiring = start_study_session(postgres, course)
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        update(study_sessions)
        .where(study_sessions.c.id == expiring)
        .values(
            started_at=func.now() - timedelta(minutes=3),
            last_heartbeat_at=func.now() - timedelta(minutes=3),
        )
    )
    _role(postgres, owner)
    assert heartbeat_study_session(postgres, course, expiring) == 0
    expired = get_study_session(postgres, course, expiring)
    assert expired["ended_at"] == expired["last_heartbeat_at"]
    assert list_study_intervals(postgres, course, expiring) == []
    assert start_study_session(postgres, course) != expiring
    assert len(list_study_sessions(postgres, course)) == 3
