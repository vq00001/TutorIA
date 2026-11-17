"""RNF-S-4: compara el efecto HTTP con el evento de auditoría transaccional."""

from contextlib import nullcontext
import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import insert, select

from backend import file_storage, http_api
from backend.db.audit import audit_events
from backend.db.courses import create_course, memberships
from backend.db.sessions import issue_session
from backend.routes import dependencies

from .test_courses import _as_user, _user
from .test_job_integration import _upload


with (Path(__file__).parent / "fixtures" / "rnf_s4.json").open(encoding="utf-8") as source:
    CATALOG = json.load(source)


class MissingAuditEvent(AssertionError):
    """La operación respondió como se esperaba, pero no emitió un evento."""


# Registrar cada acción pendiente individualmente permite retirar su marca al integrarla.
PENDING = {
    "editar_perfil", "cerrar_sesion", "crear_clase", "editar_clase", "cambiar_rol",
    "cargar_documento", "borrar_documento", "borrado_denegado_a_estudiante",
    "borrado_denegado_a_externo",
}
CASES = [
    pytest.param(
        case,
        id=case["id"],
        marks=pytest.mark.xfail(
            reason="La ruta aún no integra el registrador de auditoría",
            raises=MissingAuditEvent,
            strict=True,
        ) if case["id"] in PENDING else (),
    )
    for case in CATALOG["acciones"]
]


@pytest.fixture
def audit_setup(postgres, monkeypatch, tmp_path):
    users = {role: _user(postgres) for role in ("coordinator", "student", "outsider")}
    tokens = {role: issue_session(postgres, user_id) for role, user_id in users.items()}
    _as_user(postgres, users["coordinator"])
    course = create_course(postgres, name="Auditoría RNF-S-4")
    document = UUID(_upload(postgres, course, "rnf-s4-seed")["document_id"])
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(
        course_id=course, user_id=users["student"], role="student",
    ))
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    monkeypatch.setattr(file_storage, "FILES_DIR", tmp_path)
    with TestClient(http_api.app) as client:
        yield client, users, tokens, course, document


def test_rnf_s4_catalogo_de_acciones_protegidas():
    actions = CATALOG["acciones"]
    assert len({case["id"] for case in actions}) == len(actions)
    assert PENDING <= {case["id"] for case in actions}
    assert {case["categoria"] for case in actions} == {"cuentas", "clases", "materiales"}
    paths = http_api.app.openapi()["paths"]
    for case in actions:
        operation = paths[CATALOG["prefijo"] + case["ruta"]][case["metodo"].lower()]
        assert any("bearerAuth" in security for security in operation.get("security", []))
        assert str(case["http"]) in operation["responses"]
        assert case["evento"]["resultado"] == ("success" if case["http"] < 300 else "denied")


@pytest.mark.parametrize("case", CASES)
def test_rnf_s4_accion_de_api_deja_evento(postgres, audit_setup, case):
    client, users, tokens, course, document = audit_setup
    actor = users[case["actor"]]
    _as_user(postgres, actor)
    prefix = CATALOG["prefijo"]
    path = prefix + case["ruta"].format(courseId=course, userId=users["student"], documentId=document)
    headers = {"Authorization": f"Bearer {tokens[case['actor']]}"}
    options = {}
    action = case["id"]
    if action == "editar_perfil":
        options["json"] = {"displayName": "Nombre actualizado"}
    elif action == "crear_clase":
        options["json"] = {"name": "Nueva clase", "description": "", "objectives": ""}
    elif action == "editar_clase":
        previous = client.get(f"{prefix}/courses/{course}", headers=headers)
        assert previous.status_code == 200, previous.text
        options["json"] = {"name": "Clase editada", "rowVersion": previous.json()["data"]["rowVersion"]}
    elif action == "cambiar_rol":
        roster = client.get(f"{prefix}/courses/{course}/members", headers=headers)
        assert roster.status_code == 200, roster.text
        student = next(row for row in roster.json()["data"] if row["userId"] == str(users["student"]))
        options["json"] = {"role": "coordinator", "rowVersion": student["rowVersion"]}
    elif action == "cargar_documento":
        headers["Idempotency-Key"] = "rnf-s4-audit"
        options["files"] = {"files": ("audit.txt", b"Contenido sintetico para auditoria.", "text/plain")}

    postgres.exec_driver_sql("RESET ROLE")
    before = set(postgres.execute(select(audit_events.c.id)).scalars())
    _as_user(postgres, actor)
    response = client.request(case["metodo"], path, headers=headers, **options)
    assert response.status_code == case["http"], response.text

    ids = {"actor": actor, "curso": course, "estudiante": users["student"], "documento": document, "ninguna": None}
    if action == "crear_clase":
        ids["respuesta"] = UUID(response.json()["data"]["id"])
    elif action == "cargar_documento":
        item, = response.json()["data"]
        assert item["duplicate"] is False
        ids["documento_nuevo"] = UUID(item["documentId"])

    postgres.exec_driver_sql("RESET ROLE")
    emitted = postgres.execute(select(audit_events).where(audit_events.c.id.not_in(before))).mappings().all()
    if not emitted:
        raise MissingAuditEvent(f"{case['metodo']} {case['ruta']} no emitió auditoría para {action}")

    event = case["evento"]
    matches = [row for row in emitted if row.action == event["accion"]]
    assert len(matches) == 1, [(row.action, row.result) for row in emitted]
    row = matches[0]
    assert row.actor_id == actor and row.source == "user"
    assert row.entity_type == event["entidad"] and row.entity_id == ids[event["identificador"]]
    assert row.course_id == ids[event["clase"]]
    assert row.result == event["resultado"]
    assert isinstance(row.request_id, UUID) and row.created_at.tzinfo is not None
    assert row.metadata == ({"reason_code": event["motivo"]} if "motivo" in event else {})
