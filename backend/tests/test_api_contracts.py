import base64
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.api.auth import PasswordChange, Register
from backend.api.common import ApiError
from backend.api.cursors import decode_cursor, encode_cursor


def test_public_registration_fields_trim_but_password_does_not():
    value = Register(
        email="  A+alias@example.com  ",
        nickname="  synthetic_user  ",
        displayName="  Alias  ",
        password="  synthetic password  ",
        acceptTerms=True,
        acknowledgeAI=True,
    )
    assert str(value.email) == "A+alias@example.com"
    assert value.nickname == "synthetic_user" and value.displayName == "Alias"
    assert value.password == "  synthetic password  "
    changed = PasswordChange(currentPassword=" old ", newPassword="  new synthetic password  ")
    assert (
        changed.currentPassword == " old " and changed.newPassword == "  new synthetic password  "
    )


@given(
    st.datetimes(
        min_value=datetime(2000, 1, 1),
        max_value=datetime(2030, 1, 1),
        timezones=st.just(timezone.utc),
    ),
    st.uuids(),
)
@settings(max_examples=100)
def test_stable_cursor_round_trip(created, identifier):
    assert decode_cursor(encode_cursor(created, identifier)) == (created, identifier)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"at": 12, "id": str(uuid4())},
        {"at": "2026-01-01T00:00:00", "id": str(uuid4())},
        {"at": "2026-01-01T00:00:00Z", "id": "not-uuid"},
        {"at": "2026-01-01T00:00:00Z", "id": str(uuid4()), "extra": True},
    ],
)
def test_untrusted_cursor_shape_rejected(payload):
    cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(ApiError, match="INVALID_CURSOR") as failure:
        decode_cursor(cursor)
    assert failure.value.status == 422


def test_missing_cursor_is_initial_page_and_size_is_bounded():
    assert decode_cursor(None) == (None, None)
    with pytest.raises(ApiError, match="INVALID_CURSOR"):
        decode_cursor("a" * 513)


def test_database_generation_quota_maps_to_public_429_without_database_details():
    from types import SimpleNamespace

    from sqlalchemy.exc import IntegrityError
    from starlette.requests import Request

    from backend.api.main import integrity_error

    original = Exception("PRIVATE_DATABASE_DETAILS")
    original.diag = SimpleNamespace(message_primary="GENERATION_QUOTA_EXCEEDED")
    error = IntegrityError("PRIVATE_SQL", {"token": "PRIVATE_TOKEN"}, original)
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/courses/synthetic/exams"})
    coroutine = integrity_error(request, error)
    try:
        coroutine.send(None)
    except StopIteration as result:
        response = result.value
    else:
        coroutine.close()
        raise AssertionError("Error response unexpectedly waited for I/O")
    assert response.status_code == 429
    assert json.loads(response.body)["error"]["code"] == "GENERATION_QUOTA_EXCEEDED"
    assert b"PRIVATE" not in response.body
