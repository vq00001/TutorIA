"""RNF-S-1: la API persistente rechaza solicitudes sin credenciales válidas."""

import json
from pathlib import Path
import re
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from backend import http_api
from backend.db import connection


with (Path(__file__).parent / "fixtures" / "rnf_s1.json").open(encoding="utf-8") as source:
    MATRIX = json.load(source)

METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"}
PARAMETER = re.compile(r"\{[^}/]+\}")
SYNTHETIC_UUID = "00000000-0000-0000-0000-000000000001"


def matrix_operations(group):
    prefix = MATRIX["prefijo"]
    return [(method, prefix + path) for method, paths in MATRIX[group].items() for path in paths]


PROTECTED = matrix_operations("protegidas")
PUBLIC = matrix_operations("publicas")


def test_rnf_s1_matriz_cubre_api_persistente():
    assert len(set(PROTECTED)) == len(PROTECTED)
    assert len(set(PUBLIC)) == len(PUBLIC)
    assert set(PROTECTED).isdisjoint(PUBLIC)

    documented = {
        (method.upper(), path): operation
        for path, item in http_api.app.openapi()["paths"].items()
        for method, operation in item.items()
        if method.upper() in METHODS
    }
    assert set(documented) == set(PROTECTED) | set(PUBLIC)
    protected_in_contract = {
        key
        for key, operation in documented.items()
        if any("bearerAuth" in security for security in operation.get("security", []))
    }
    assert protected_in_contract == set(PROTECTED)


@pytest.mark.parametrize(
    "method,path", [pytest.param(method, path, id=f"{method} {path}") for method, path in PROTECTED],
)
@pytest.mark.parametrize("authorization", [
    pytest.param(None, id="sin-token"),
    pytest.param("Bearer token-invalido", id="bearer-malformado"),
])
def test_rnf_s1_rechaza_credencial_invalida(monkeypatch, method, path, authorization):
    # Con estos dos tipos de credenciales, la API debe rechazar antes de abrir la BD.
    engine = Mock(side_effect=AssertionError("Una solicitud sin bearer válido intentó abrir PostgreSQL"))
    monkeypatch.setattr(connection, "engine", engine)
    headers = {"Idempotency-Key": "rnf-s1"}
    if authorization:
        headers["Authorization"] = authorization

    options = {}
    if method == "POST" and path.endswith("/documents"):
        options["files"] = {"files": ("prueba.txt", b"alfa", "text/plain")}
    elif method in {"POST", "PUT", "PATCH"}:
        options["json"] = {}

    with TestClient(http_api.app) as client:
        response = client.request(method, PARAMETER.sub(SYNTHETIC_UUID, path), headers=headers, **options)

    assert response.status_code == 401, response.text
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"] == "INVALID_SESSION"
    engine.assert_not_called()
