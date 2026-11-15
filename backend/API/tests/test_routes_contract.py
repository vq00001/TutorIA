"""La estructura del OpenAPI generado por FastAPI coincide con el contrato."""

import pytest
from app.main import app
from tests.contract_utils import API_PREFIX, HTTP_METHODS, contract, flat

GENERATED = app.openapi()

APP_OPS = {
    (path.removeprefix(API_PREFIX), method): op
    for path, item in GENERATED["paths"].items()
    for method, op in item.items()
    if method in HTTP_METHODS
}
CONTRACT_OPS = {(path, method): op for path, method, op in contract.operations()}
OP_KEYS = sorted(CONTRACT_OPS)
OP_IDS = [f"{m.upper()} {p}" for p, m in OP_KEYS]

PARAM_KEYS = ("type", "minimum", "maximum", "default", "enum", "format")


def test_all_contract_operations_are_implemented():
    missing = sorted(set(CONTRACT_OPS) - set(APP_OPS))
    assert not missing, f"Endpoints del contrato sin implementar: {missing}"


def test_no_operations_outside_the_contract():
    extra = sorted(set(APP_OPS) - set(CONTRACT_OPS))
    assert not extra, f"Endpoints que no están en el contrato: {extra}"


@pytest.mark.parametrize(("path", "method"), OP_KEYS, ids=OP_IDS)
class TestOperation:
    def test_success_status_is_declared(self, path, method):
        status = str(contract.success_status(CONTRACT_OPS[(path, method)]))
        assert status in APP_OPS[(path, method)]["responses"]

    def test_contract_error_statuses_are_declared(self, path, method):
        expected = set(CONTRACT_OPS[(path, method)]["responses"])
        assert expected <= set(APP_OPS[(path, method)]["responses"])

    def test_tags(self, path, method):
        assert APP_OPS[(path, method)]["tags"] == CONTRACT_OPS[(path, method)]["tags"]

    def test_security(self, path, method):
        needs_auth = contract.needs_auth(CONTRACT_OPS[(path, method)])
        assert bool(APP_OPS[(path, method)].get("security")) is needs_auth

    def test_operation_id_when_contract_defines_one(self, path, method):
        expected = CONTRACT_OPS[(path, method)].get("operationId")
        if expected:
            assert APP_OPS[(path, method)]["operationId"] == expected

    def test_parameters(self, path, method):
        expected = {(p["in"], p["name"]): p for p in contract.parameters(path, method)}
        actual = {
            (p["in"], p["name"]): p
            for p in APP_OPS[(path, method)].get("parameters", [])
        }
        assert set(actual) == set(expected)
        for key, exp in expected.items():
            assert actual[key].get("required", False) == exp.get("required", False), key
            act_schema = flat(actual[key]["schema"])
            for constraint in PARAM_KEYS:
                if constraint in exp["schema"]:
                    assert (
                        act_schema.get(
                            constraint, actual[key]["schema"].get(constraint)
                        )
                        == exp["schema"][constraint]
                    ), f"{key}: {constraint}"

    def test_request_body(self, path, method):
        expected = CONTRACT_OPS[(path, method)].get("requestBody")
        actual = APP_OPS[(path, method)].get("requestBody")
        if expected is None:
            assert actual is None
            return
        assert actual is not None
        assert actual.get("required", False) == expected.get("required", False)
        assert set(actual["content"]) == set(expected["content"])
