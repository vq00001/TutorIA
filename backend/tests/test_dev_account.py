"""La cuenta local de prueba se puede preparar repetidas veces."""

from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from backend.api.routes.auth import LoginRequest
from backend.db.dev_account import seed_dev_account
from backend.db.users import users


def test_default_dev_credentials_pass_login_validation():
    assert LoginRequest(email="dev@example.com", password="TutorIA-dev-123").email == "dev@example.com"


def test_seed_dev_account_is_idempotent_and_resets_password(postgres):
    email = f"{uuid4().hex}@example.test"
    seed_dev_account(postgres, email, "first-password")
    first = postgres.execute(select(users).where(users.c.email == email)).mappings().one()
    assert first["status"] == "active"
    assert first["email_verified_at"] is not None
    assert PasswordHasher().verify(first["password_hash"], "first-password")

    seed_dev_account(postgres, email, "second-password")
    rows = postgres.execute(select(users).where(users.c.email == email)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["id"] == first["id"]
    assert PasswordHasher().verify(rows[0]["password_hash"], "second-password")


def test_seed_dev_account_does_not_take_an_existing_email(postgres):
    from sqlalchemy import insert

    email = f"{uuid4().hex}@example.test"
    postgres.execute(insert(users).values(
        id=uuid4(), nickname=f"u_{uuid4().hex}", display_name="Existing",
        email=email, password_hash="existing-password", status="active",
    ))
    with pytest.raises(ValueError, match="DEV_ACCOUNT_EMAIL_ALREADY_USED"):
        seed_dev_account(postgres, email, "new-password")
    assert postgres.scalar(select(users.c.password_hash).where(users.c.email == email)) == "existing-password"
