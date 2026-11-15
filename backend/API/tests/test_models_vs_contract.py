"""Compara los modelos Pydantic y los stubs con el contrato.

No requiere FastAPI: solo ``pydantic``, ``pyyaml`` y ``jsonschema``.
"""

import inspect

import pytest
from app import schemas, stubs
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError
from tests.contract_utils import contract, flat, to_json_schema

CONSTRAINT_KEYS = (
    "enum",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "format",
)


def _model(name: str) -> type[BaseModel] | None:
    obj = getattr(schemas, name, None)
    return obj if inspect.isclass(obj) and issubclass(obj, BaseModel) else None


MODEL_NAMES = [n for n in contract.schemas if _model(n)]
REQUEST_NAMES = [n for n in MODEL_NAMES if n.endswith("Request")]


def test_contract_schemas_are_valid_json_schema():
    for name, schema in contract.schemas.items():
        Draft202012Validator.check_schema(to_json_schema(schema))


def test_contract_refs_resolve():
    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                contract.deref(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(contract.doc)


def test_every_contract_schema_has_a_model_or_is_a_response_envelope():
    """Un schema nuevo en el contrato sin modelo hace fallar este test."""
    missing = [
        n for n in contract.schemas if not _model(n) and not n.endswith("Response")
    ]
    assert not missing, f"Faltan modelos Pydantic para: {missing}"


@pytest.mark.parametrize("name", MODEL_NAMES)
def test_model_matches_contract_schema(name):
    expected = contract.schemas[name]
    actual = _model(name).model_json_schema(mode="validation")

    assert set(actual.get("properties", {})) == set(
        expected.get("properties", {})
    ), "propiedades"
    assert set(actual.get("required", [])) == set(
        expected.get("required", [])
    ), "required"

    for prop_name, exp_prop in expected.get("properties", {}).items():
        act_prop = flat(actual["properties"][prop_name])
        where = f"{name}.{prop_name}"

        if exp_prop.get("nullable"):
            assert act_prop["_nullable"], f"{where} debe aceptar null"
        if "type" in exp_prop:
            assert act_prop["_types"] == {exp_prop["type"]}, f"{where}: type"
        for key in CONSTRAINT_KEYS:
            if key in exp_prop:
                assert act_prop.get(key) == exp_prop[key], f"{where}: {key}"
        if "default" in exp_prop:
            assert (
                act_prop.get("default", actual["properties"][prop_name].get("default"))
                == exp_prop["default"]
            ), f"{where}: default"


@pytest.mark.parametrize("name", REQUEST_NAMES)
def test_request_models_accept_contract_example(name):
    _model(name).model_validate(contract.example(contract.schema_ref(name)))


def test_patch_models_require_at_least_one_field():
    for model in (schemas.UpdateUserRequest, schemas.UpdateCourseRequest):
        with pytest.raises(ValidationError):
            model.model_validate({})


# Cada función pública de ``app.stubs`` → schema del contrato que debe cumplir.
STUB_SCHEMAS = {
    "health": "HealthResponse",
    "auth_response": "AuthResponse",
    "user_response": "UserResponse",
    "dashboard_summary": "DashboardSummaryResponse",
    "course_list": "CourseListResponse",
    "course_response": "CourseResponse",
    "member_list": "MemberListResponse",
    "member_response": "MemberResponse",
    "invitation_response": "InvitationResponse",
    "invitation_list": "InvitationListResponse",
    "document_list": "DocumentListResponse",
    "document_response": "DocumentResponse",
    "document_status": "DocumentStatusResponse",
    "conversation_list": "ConversationListResponse",
    "conversation_response": "ConversationResponse",
    "message_response": "MessageResponse",
    "exam_summary_list": "ExamSummaryListResponse",
    "exam_response": "ExamResponse",
    "exam_result_response": "ExamResultResponse",
    "progress_response": "CourseProgressResponse",
    "knowledge_graph_response": "KnowledgeGraphResponse",
    "materials_list": "GeneratedMaterialListResponse",
    "notification_list": "NotificationListResponse",
    "activity_list": "ActivityListResponse",
}


def test_all_stubs_are_mapped():
    public = {
        n
        for n, f in inspect.getmembers(stubs, inspect.isfunction)
        if f.__module__ == stubs.__name__ and not n.startswith("_")
    }
    assert public == set(STUB_SCHEMAS)


@pytest.mark.parametrize(("func", "schema_name"), STUB_SCHEMAS.items())
def test_stubs_match_contract(func, schema_name):
    payload = getattr(stubs, func)().model_dump(mode="json")
    contract.assert_valid(contract.schema_ref(schema_name), payload, f"stubs.{func}")
