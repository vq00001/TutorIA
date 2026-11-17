"""RNF-S-2: matriz de autorización por rol, recurso y acción."""

from contextlib import nullcontext
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import insert

from backend import file_storage, http_api
from backend.db.courses import create_course, memberships
from backend.db.sessions import issue_session
from backend.routes import dependencies

from .test_courses import _as_user, _user
from .test_job_integration import _upload


with (Path(__file__).parent / "fixtures" / "rnf_s2.json").open(encoding="utf-8") as source:
    MATRIX = json.load(source)

COURSE_NAME = "Curso de prueba S-2"
NEW_NAME = "Curso editado S-2"
OWNER_FILE = b"Apuntes de coordinacion."
STUDENT_FILE = b"Apuntes del estudiante."
CASES = [
    pytest.param(action, role, action["esperado"][role], id=f"{action['id']}-{role}")
    for action in MATRIX["acciones"]
    for role in MATRIX["roles"]
]


@pytest.fixture
def permission_setup(postgres, monkeypatch, tmp_path):
    users = {role: _user(postgres) for role in MATRIX["roles"]}
    tokens = {role: issue_session(postgres, user_id) for role, user_id in users.items()}
    _as_user(postgres, users["coordinator"])
    course = create_course(postgres, name=COURSE_NAME)
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=users["student"], role="student"))

    documents = {}
    for role, content in (("coordinator", OWNER_FILE), ("student", STUDENT_FILE)):
        _as_user(postgres, users[role])
        row = _upload(postgres, course, f"seed-{role}", data=content)
        documents[role] = row["document_id"]

    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    monkeypatch.setattr(file_storage, "FILES_DIR", tmp_path)
    with TestClient(http_api.app) as client:
        yield client, str(course), users, tokens, documents


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def snapshot(client, base, token):
    headers = auth(token)
    course = client.get(base, headers=headers)
    members = client.get(f"{base}/members", headers=headers)
    documents = client.get(f"{base}/documents", headers=headers)
    for response in (course, members, documents):
        assert response.status_code == 200, response.text
    return (
        course.json()["data"],
        {member["userId"]: member for member in members.json()["data"]},
        {document["id"]: document for document in documents.json()["data"]},
    )


def test_rnf_s2_matriz_de_operaciones_protegidas():
    actions = MATRIX["acciones"]
    assert len({action["id"] for action in actions}) == len(actions)
    assert set(MATRIX["roles"]) == {"coordinator", "student", "outsider"}
    paths = http_api.app.openapi()["paths"]
    for action in actions:
        assert set(action["esperado"]) == set(MATRIX["roles"])
        statuses = action["esperado"].values()
        assert set(statuses) <= {200, 202, 204, 403, 404}
        assert any(200 <= status < 300 for status in statuses)
        assert any(status in {403, 404} for status in statuses)
        operation = paths[MATRIX["prefijo"] + action["ruta"]][action["metodo"].lower()]
        assert any("bearerAuth" in security for security in operation.get("security", []))


@pytest.mark.parametrize("action,role,expected", CASES)
def test_rnf_s2_autoriza_por_rol_recurso_y_accion(permission_setup, action, role, expected):
    client, course, users, tokens, documents = permission_setup
    base = MATRIX["prefijo"] + f"/courses/{course}"
    before = snapshot(client, base, tokens["coordinator"])
    before_course, before_members, before_documents = before
    student_id = str(users["student"])
    assert before_course["name"] == COURSE_NAME
    assert before_members[student_id]["role"] == "student"
    assert set(before_documents) == set(documents.values())

    case = action["id"]
    target = documents["student"] if case == "borrar_documento_estudiante" else documents["coordinator"]
    path = MATRIX["prefijo"] + action["ruta"].format(courseId=course, userId=student_id, documentId=target)
    headers = auth(tokens[role])
    options = {}
    if case == "editar_curso":
        options["json"] = {"name": NEW_NAME, "rowVersion": before_course["rowVersion"]}
    elif case == "cambiar_rol":
        options["json"] = {"role": "coordinator", "rowVersion": before_members[student_id]["rowVersion"]}
    elif case == "subir_documento":
        headers["Idempotency-Key"] = "rnf-s2-upload"
        options["files"] = {"files": ("nuevo.txt", b"Documento nuevo para la matriz.", "text/plain")}

    response = client.request(action["metodo"], path, headers=headers, **options)
    assert response.status_code == expected, response.text
    after = snapshot(client, base, tokens["coordinator"])
    after_course, after_members, after_documents = after

    if expected in {403, 404}:
        assert after == before
    elif case == "editar_curso":
        assert after_course["name"] == response.json()["data"]["name"] == NEW_NAME
        assert after_members == before_members and after_documents == before_documents
    elif case == "cambiar_rol":
        assert after_members[student_id]["role"] == "coordinator"
        assert after_course["name"] == COURSE_NAME and after_documents == before_documents
    elif case == "subir_documento":
        uploaded, = response.json()["data"]
        assert uploaded["duplicate"] is False and uploaded["jobId"]
        assert set(after_documents) == set(before_documents) | {uploaded["documentId"]}
        assert after_documents[uploaded["documentId"]]["name"] == "nuevo.txt"
    elif case.startswith("borrar_documento"):
        assert target in before_documents and set(after_documents) == set(before_documents) - {target}
    else:
        assert after == before
        if case == "ver_curso":
            assert response.json()["data"]["id"] == course
        elif case == "listar_miembros":
            assert {member["userId"] for member in response.json()["data"]} == set(before_members)
        elif case == "listar_documentos":
            assert {document["id"] for document in response.json()["data"]} == set(before_documents)
        elif case == "descargar_documento":
            assert response.content == OWNER_FILE
