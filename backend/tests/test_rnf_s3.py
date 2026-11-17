"""RNF-S-3: comprueba qué datos privados quedan visibles a cada actor."""

from contextlib import nullcontext
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import ProgrammingError

from backend import http_api
from backend.db.courses import create_course, memberships
from backend.db.generated_materials import GeneratedMaterial, store_generated_materials
from backend.db.sessions import issue_session
from backend.db.users import users
from backend.routes import dependencies

from .test_courses import _as_user, _user
from .test_exams import _generation
from .test_job_integration import _upload


with (Path(__file__).parent / "fixtures" / "rnf_s3.json").open(encoding="utf-8") as source:
    MATRIX = json.load(source)

PROFILE_FIELDS = {"id", "nickname", "email", "displayName", "status", "createdAt", "emailVerifiedAt"}
MEMBER_FIELDS = {"userId", "displayName", "role", "joinedAt", "rowVersion"}
DOC_A = b"RNF-S-3 contenido exclusivo de la clase A"
DOC_B = b"RNF-S-3 contenido exclusivo de la clase B"
PRIVATE_SUMMARY = "RNF-S-3 resumen exclusivo del titular"
CASES = [
    pytest.param(action, actor, action["esperado"][actor], id=f"{action['id']}-{actor}")
    for action in MATRIX["acciones"]
    for actor in MATRIX["actores"]
]


@pytest.fixture
def privacy_setup(postgres, monkeypatch):
    actors = {role: _user(postgres) for role in MATRIX["actores"]}
    tokens = {role: issue_session(postgres, user_id) for role, user_id in actors.items()}
    _as_user(postgres, actors["titular"])
    course_a = create_course(postgres, name="Privacidad A")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(
        course_id=course_a, user_id=actors["companero"], role="student",
    ))
    _as_user(postgres, actors["titular"])
    doc_a = _upload(postgres, course_a, "privacidad-a", filename="clase-a.md", data=DOC_A)["document_id"]
    generation = _generation(postgres, course_a, kind="chat")
    summary = GeneratedMaterial("Resumen privado A", "summary", {"text": PRIVATE_SUMMARY})
    store_generated_materials(postgres, course_a, generation, [summary])

    _as_user(postgres, actors["externo"])
    course_b = create_course(postgres, name="Privacidad B")
    doc_b = _upload(postgres, course_b, "privacidad-b", filename="clase-b.md", data=DOC_B)["document_id"]

    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    with TestClient(http_api.app) as client:
        yield client, actors, tokens, (str(course_a), str(course_b)), (doc_a, doc_b), str(summary.id)


def test_rnf_s3_matriz_de_lectura_protegida():
    actions = MATRIX["acciones"]
    assert len({action["id"] for action in actions}) == len(actions)
    assert set(MATRIX["actores"]) == {"titular", "companero", "externo"}
    paths = http_api.app.openapi()["paths"]
    for action in actions:
        assert set(action["esperado"]) == set(MATRIX["actores"])
        assert set(action["esperado"].values()) <= {200, 404}
        operation = paths[MATRIX["prefijo"] + action["ruta"]]["get"]
        assert any("bearerAuth" in security for security in operation.get("security", []))


@pytest.mark.parametrize("action,actor,expected", CASES)
def test_rnf_s3_devuelve_solo_datos_autorizados(privacy_setup, action, actor, expected):
    client, users_by_actor, tokens, (course_a, course_b), (doc_a, doc_b), summary = privacy_setup
    case = action["id"]
    course_id = course_b if case in {"descargar_documento_b", "documento_a_en_clase_b"} else course_a
    document_id = doc_b if case == "descargar_documento_b" else doc_a
    path = MATRIX["prefijo"] + action["ruta"].format(
        courseId=course_id, documentId=document_id, materialId=summary,
    )
    response = client.get(path, headers={"Authorization": f"Bearer {tokens[actor]}"})
    assert response.status_code == expected, response.text

    emails = {role: f"{identifier.hex}@example.test".encode() for role, identifier in users_by_actor.items()}
    if case == "perfil_propio":
        data = response.json()["data"]
        assert set(data) == PROFILE_FIELDS
        assert data["id"] == str(users_by_actor[actor])
        assert data["email"].encode() == emails[actor]
        assert response.headers["cache-control"] == "no-store"
        assert all(email not in response.content for role, email in emails.items() if role != actor)
        return

    assert all(email not in response.content for email in emails.values())
    if expected == 404:
        assert "data" not in response.json()
        assert DOC_A not in response.content and DOC_B not in response.content
        assert PRIVATE_SUMMARY.encode() not in response.content
    elif case == "campos_de_miembros":
        members = response.json()["data"]
        assert {entry["userId"] for entry in members} == {
            str(users_by_actor["titular"]), str(users_by_actor["companero"]),
        }
        assert all(set(entry) == MEMBER_FIELDS for entry in members)
    elif case == "listar_documentos_a":
        documents = response.json()["data"]
        assert [entry["id"] for entry in documents] == [doc_a]
        assert DOC_A not in response.content and DOC_B not in response.content
    elif case.startswith("descargar_documento"):
        assert response.content == (DOC_A if case.endswith("_a") else DOC_B)
    elif case == "listar_materiales_privados":
        materials = response.json()["data"]
        assert [entry["id"] for entry in materials] == ([summary] if actor == "titular" else [])
        assert all("content" not in entry for entry in materials)
        assert PRIVATE_SUMMARY.encode() not in response.content
    elif case == "leer_material_privado":
        assert response.json()["data"]["content"] == {"text": PRIVATE_SUMMARY}


@pytest.mark.parametrize("actor", MATRIX["actores"])
def test_rnf_s3_bd_restringe_perfiles_y_hashes(postgres, actor):
    ids = {role: _user(postgres) for role in MATRIX["actores"]}
    _as_user(postgres, ids[actor])

    # El rol de API puede leer su perfil y no debe ver siquiera filas ajenas.
    visible = postgres.execute(
        select(users.c.id, users.c.email).where(users.c.id.in_(ids.values()))
    ).all()
    assert visible == [(ids[actor], f"{ids[actor].hex}@example.test")]

    with pytest.raises(ProgrammingError) as denied, postgres.begin_nested():
        postgres.execute(select(users.c.password_hash).where(users.c.id == ids[actor]))
    assert denied.value.orig.sqlstate == "42501"
