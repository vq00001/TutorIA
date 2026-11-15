"""Cada endpoint responde (status, headers y cuerpo) según el contrato."""

import pytest
from tests.contract_utils import contract
from unittest.mock import patch
from contextlib import nullcontext

OPERATIONS = list(contract.operations())
IDS = [f"{m.upper()} {p}" for p, m, _ in OPERATIONS]


@pytest.mark.parametrize(("path", "method", "op"), OPERATIONS, ids=IDS)
def test_success_response_matches_contract(client, path, method, op):
    status = contract.success_status(op)
    ctx = (
        # Reemplaza expire_token por una función vacía durante la ejecución del test
        # Esto para simular que el token se invalido correctamente
        patch("app.routers.auth.expire_token", return_value=None)
        if path == "/auth/logout"
        else nullcontext()
    )

    with ctx:
        response = client.request(**contract.build_request(path, method, op))

    assert response.status_code == status, response.text

    schema = contract.response_schema(op, status)
    if schema is None:
        assert (
            response.content == b""
        ), "una respuesta sin contenido no debe traer cuerpo"
        return

    assert response.headers["content-type"].startswith("application/json")
    contract.assert_valid(
        schema, response.json(), f"{method.upper()} {path} -> {status}"
    )


PROTECTED = [(p, m, op) for p, m, op in OPERATIONS if contract.needs_auth(op)]
PUBLIC = [(p, m, op) for p, m, op in OPERATIONS if not contract.needs_auth(op)]


@pytest.mark.parametrize(
    ("path", "method", "op"),
    PROTECTED,
    ids=[f"{m.upper()} {p}" for p, m, _ in PROTECTED],
)
def test_protected_endpoints_return_401_without_token(client, path, method, op):
    response = client.request(
        **contract.build_request(path, method, op, with_auth=False)
    )

    assert response.status_code == 401, response.text
    contract.assert_valid(contract.schema_ref("ErrorResponse"), response.json(), "401")


@pytest.mark.parametrize(
    ("path", "method", "op"),
    PUBLIC,
    ids=[f"{m.upper()} {p}" for p, m, _ in PUBLIC],
)
def test_public_endpoints_do_not_require_token(client, path, method, op):
    response = client.request(
        **contract.build_request(path, method, op, with_auth=False)
    )
    assert response.status_code == contract.success_status(op), response.text


def test_public_endpoints_are_exactly_health_register_login():
    assert {(p, m) for p, m, _ in PUBLIC} == {
        ("/health", "get"),
        ("/auth/register", "post"),
        ("/auth/login", "post"),
    }
