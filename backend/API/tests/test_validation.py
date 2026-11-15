"""Las peticiones inválidas se rechazan con 422 y un ``ErrorResponse``."""

import pytest
from tests.contract_utils import SAMPLE_UUID as U
from tests.contract_utils import contract


def assert_error(response, status):
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/json")
    contract.assert_valid(
        contract.schema_ref("ErrorResponse"), response.json(), f"error {status}"
    )


VALID_USER = {
    "email": "a@example.com",
    "password": "Passw0rd!123",
    "displayName": "Ana",
}

INVALID_BODIES = {
    "register: email inválido": (
        "POST",
        "/auth/register",
        {**VALID_USER, "email": "nope"},
    ),
    "register: password corta": (
        "POST",
        "/auth/register",
        {**VALID_USER, "password": "short"},
    ),
    "register: displayName vacío": (
        "POST",
        "/auth/register",
        {**VALID_USER, "displayName": ""},
    ),
    "register: displayName > 100": (
        "POST",
        "/auth/register",
        {**VALID_USER, "displayName": "x" * 101},
    ),
    "register: sin campos": ("POST", "/auth/register", {}),
    "login: sin password": ("POST", "/auth/login", {"email": "a@example.com"}),
    "users/me PATCH: vacío": ("PATCH", "/users/me", {}),
    "users/me PATCH: password corta": ("PATCH", "/users/me", {"password": "short"}),
    "courses POST: faltan campos": ("POST", "/courses", {"name": "x"}),
    "courses POST: name vacío": (
        "POST",
        "/courses",
        {"name": "", "description": "d", "objectives": "o"},
    ),
    "courses PATCH: vacío": ("PATCH", f"/courses/{U}", {}),
    "members PATCH: rol owner no asignable": (
        "PATCH",
        f"/courses/{U}/members/{U}",
        {"role": "owner"},
    ),
    "invitations POST: email inválido": (
        "POST",
        f"/courses/{U}/invitations",
        {"email": "nope"},
    ),
    "invitations PATCH: decisión inválida": (
        "PATCH",
        f"/invitations/{U}",
        {"decision": "maybe"},
    ),
    "conversations POST: pregunta vacía": (
        "POST",
        f"/courses/{U}/conversations",
        {"question": ""},
    ),
    "messages POST: sin pregunta": (
        "POST",
        f"/courses/{U}/conversations/{U}/messages",
        {},
    ),
    "exams POST: questionCount 0": (
        "POST",
        f"/courses/{U}/exams",
        {"topic": "t", "questionCount": 0},
    ),
    "exams POST: questionCount 51": (
        "POST",
        f"/courses/{U}/exams",
        {"topic": "t", "questionCount": 51},
    ),
    "exams POST: durationMinutes 0": (
        "POST",
        f"/courses/{U}/exams",
        {"topic": "t", "questionCount": 5, "durationMinutes": 0},
    ),
    "exams POST: tipo de pregunta inválido": (
        "POST",
        f"/courses/{U}/exams",
        {"topic": "t", "questionCount": 5, "questionTypes": ["essay"]},
    ),
    "submissions POST: questionId no uuid": (
        "POST",
        f"/courses/{U}/exams/{U}/submissions",
        {"answers": [{"questionId": "nope", "answer": "x"}]},
    ),
    "submissions POST: sin answers": (
        "POST",
        f"/courses/{U}/exams/{U}/submissions",
        {},
    ),
}


@pytest.mark.parametrize(
    ("method", "path", "body"), INVALID_BODIES.values(), ids=INVALID_BODIES
)
def test_invalid_body_returns_422(client, auth_headers, method, path, body):
    response = client.request(method, "/api/v1" + path, json=body, headers=auth_headers)
    assert_error(response, 422)


INVALID_QUERIES = {
    "courses: page=0": "/courses?page=0",
    "courses: pageSize=0": "/courses?pageSize=0",
    "courses: pageSize=101": "/courses?pageSize=101",
    "courses: page no numérico": "/courses?page=abc",
    "activity: page=0": "/activity?page=0",
    "activity: pageSize=101": "/activity?pageSize=101",
    "activity: type desconocido": "/activity?type=unknown",
}


@pytest.mark.parametrize("path", INVALID_QUERIES.values(), ids=INVALID_QUERIES)
def test_invalid_query_returns_422(client, auth_headers, path):
    assert_error(client.get("/api/v1" + path, headers=auth_headers), 422)


@pytest.mark.parametrize("value", ["1", "100"])
def test_page_size_limits_are_inclusive(client, auth_headers, value):
    assert (
        client.get(
            f"/api/v1/courses?pageSize={value}", headers=auth_headers
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "activity_type",
    [
        "login",
        "logout",
        "course",
        "invitation",
        "document",
        "llm_query",
        "exam",
        "profile",
    ],
)
def test_activity_accepts_every_contract_type(client, auth_headers, activity_type):
    response = client.get(
        f"/api/v1/activity?type={activity_type}", headers=auth_headers
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "path",
    [
        "/courses/not-a-uuid",
        f"/courses/{U}/documents/not-a-uuid",
        f"/courses/{U}/exams/not-a-uuid",
        "/invitations/not-a-uuid",
    ],
)
def test_malformed_uuid_path_param_returns_422(client, auth_headers, path):
    method = "PATCH" if path.startswith("/invitations") else "GET"
    kwargs = {"json": {"decision": "accept"}} if method == "PATCH" else {}
    assert_error(
        client.request(method, "/api/v1" + path, headers=auth_headers, **kwargs), 422
    )


def test_upload_without_files_returns_422(client, auth_headers):
    assert_error(
        client.post(f"/api/v1/courses/{U}/documents", headers=auth_headers), 422
    )


def test_upload_accepts_up_to_20_files_and_rejects_more(client, auth_headers):
    def files(n):
        return [("files", (f"{i}.md", b"# x", "text/markdown")) for i in range(n)]

    ok = client.post(
        f"/api/v1/courses/{U}/documents", files=files(20), headers=auth_headers
    )
    assert ok.status_code == 202, ok.text
    assert_error(
        client.post(
            f"/api/v1/courses/{U}/documents", files=files(21), headers=auth_headers
        ),
        422,
    )


def test_unknown_route_returns_error_response(client, auth_headers):
    assert_error(client.get("/api/v1/does-not-exist", headers=auth_headers), 404)


def test_malformed_authorization_header_returns_401(client):
    response = client.get("/api/v1/users/me", headers={"Authorization": "Basic abc"})
    assert_error(response, 401)


def test_validation_error_does_not_leak_before_auth(client):
    """Sin token, una petición inválida debe dar 401 y no 422."""
    assert_error(client.post("/api/v1/courses", json={}), 401)


def test_health_reports_status(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] in {"ok", "degraded"}
    assert body["services"]
