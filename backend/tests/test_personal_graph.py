from contextlib import nullcontext
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert
from sqlalchemy.exc import DBAPIError

from backend.api import app as http_api
from backend.db.connection import set_user_context
from backend.db.learning_catalog import add_prerequisite, create_concept
from backend.db.personal_graph import create_personal_node
from backend.db.sessions import issue_session
from backend.api.routes import dependencies
from db.courses import create_course, memberships
from .test_courses import _as_user, _user


def test_accepted_gaps_are_private_and_leave_course_graph_unchanged(postgres, monkeypatch):
    owner, first_student, second_student = (_user(postgres) for _ in range(3))
    tokens = {user: issue_session(postgres, user) for user in (owner, first_student, second_student)}
    _as_user(postgres, owner)
    course = create_course(postgres, name="Cálculo")
    integral = create_concept(postgres, course, label="Integral doble")
    jacobian = create_concept(postgres, course, label="Jacobiano")
    approved_edge = add_prerequisite(postgres, course, integral, jacobian)
    postgres.exec_driver_sql("RESET ROLE")
    for student in (first_student, second_student):
        postgres.execute(insert(memberships).values(
            course_id=course, user_id=student, role="student"
        ))
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)
    base = f"/api/v1/courses/{course}/knowledge-graph"
    own = {"Authorization": f"Bearer {tokens[first_student]}"}
    peer = {"Authorization": f"Bearer {tokens[second_student]}"}
    coordinator = {"Authorization": f"Bearer {tokens[owner]}"}
    accepted = {
        "newNodes": [{"ref": "N1", "label": "Determinantes", "description": "Álgebra lineal"}],
        "recommendations": [
            {"targetConceptId": str(jacobian), "newNodeRef": "N1",
             "reason": "Repasar determinantes antes del jacobiano"},
            {"targetConceptId": str(jacobian), "existingConceptId": str(integral),
             "reason": "Repasar integrales dobles"},
        ],
    }

    assert client.post(base + "/learning-gaps/accept", json=accepted,
                       headers=coordinator).status_code == 403
    response = client.post(base + "/learning-gaps/accept", json=accepted, headers=own)
    assert response.status_code == 201
    first_node_id = response.json()["data"]["personalNodeIdsByRef"]["N1"]
    assert len(response.json()["data"]["recommendationIds"]) == 2

    first = client.get(base + "/learning-gaps/accepted", headers=own).json()["data"]
    assert [node["id"] for node in first["nodes"]] == [first_node_id]
    assert {item["targetConceptId"] for item in first["recommendations"]} == {str(jacobian)}
    assert {item["existingConceptId"] for item in first["recommendations"]} == {
        None, str(integral)
    }
    assert client.get(base + "/learning-gaps/accepted", headers=peer).json()["data"] == {
        "nodes": [], "recommendations": []
    }
    assert client.get(base + "/learning-gaps/accepted", headers=coordinator).status_code == 403

    personalized = client.get(base + "/personalized", headers=own)
    assert personalized.status_code == 200
    graph = personalized.json()["data"]
    assert {(node["key"], node["origin"]) for node in graph["nodes"]} == {
        (str(integral), "course"), (str(jacobian), "course"),
        (first_node_id, "personal"),
    }
    assert graph["edges"] == [{
        "key": str(approved_edge), "source": str(integral), "target": str(jacobian)
    }]
    assert {(item["source"], item["target"], item["kind"]) for item in graph["recommendations"]} == {
        (first_node_id, str(jacobian), "new"),
        (str(integral), str(jacobian), "existing"),
    }
    assert client.get(base + "/personalized", headers=coordinator).status_code == 403
    peer_graph = client.get(base + "/personalized", headers=peer).json()["data"]
    assert {node["key"] for node in peer_graph["nodes"]} == {str(integral), str(jacobian)}
    assert peer_graph["recommendations"] == []

    # El mismo tema se puede añadir al grafo privado de otro alumno.
    second = client.post(base + "/learning-gaps/accept", json=accepted, headers=peer)
    assert second.status_code == 201
    second_node_id = second.json()["data"]["personalNodeIdsByRef"]["N1"]
    assert second_node_id != first_node_id
    peer_graph = client.get(base + "/personalized", headers=peer).json()["data"]
    assert {node["key"] for node in peer_graph["nodes"]} == {
        str(integral), str(jacobian), second_node_id
    }
    assert all(item["source"] != first_node_id for item in peer_graph["recommendations"])
    assert client.post(base + "/learning-gaps/accept", json=accepted, headers=own).status_code == 409
    assert len(client.get(base + "/learning-gaps/accepted", headers=own).json()["data"]["nodes"]) == 1

    shared = client.get(base, headers=peer).json()["data"]
    assert {node["key"] for node in shared["nodes"]} == {str(integral), str(jacobian)}
    assert [(edge["source"], edge["target"]) for edge in shared["edges"]] == [
        (str(integral), str(jacobian))
    ]

    # La política de PostgreSQL también impide escribir en nombre de otro alumno.
    set_user_context(postgres, first_student)
    with pytest.raises(DBAPIError), postgres.begin_nested():
        create_personal_node(
            postgres, course, second_student, label="Tema ajeno", description="No permitido"
        )


def test_invalid_acceptance_does_not_create_personal_nodes(postgres, monkeypatch):
    owner, student = _user(postgres), _user(postgres)
    token = issue_session(postgres, student)
    _as_user(postgres, owner)
    course = create_course(postgres, name="Cálculo")
    concept = create_concept(postgres, course, label="Integral doble")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=student, role="student"))
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)
    base = f"/api/v1/courses/{course}/knowledge-graph/learning-gaps"
    headers = {"Authorization": f"Bearer {token}"}
    invalid = {
        "newNodes": [{"ref": "N1", "label": "Determinantes", "description": "Matrices"}],
        "recommendations": [
            {"targetConceptId": str(uuid4()), "newNodeRef": "N1", "reason": "Base previa"}
        ],
    }
    assert client.post(base + "/accept", json=invalid, headers=headers).status_code == 422
    invalid["recommendations"][0]["targetConceptId"] = str(concept)
    invalid["recommendations"][0]["newNodeRef"] = "N2"
    assert client.post(base + "/accept", json=invalid, headers=headers).status_code == 422
    assert client.get(base + "/accepted", headers=headers).json()["data"] == {
        "nodes": [], "recommendations": []
    }
