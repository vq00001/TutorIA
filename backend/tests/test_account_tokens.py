from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import ProgrammingError

from db.account_tokens import account_tokens, issue_account_token, reset_password, verify_email
from db.sessions import get_session_user, issue_session
from db.users import users

password_hasher = PasswordHasher()


def _seed_pending(conn):
    identifier = uuid4()
    email = identifier.hex + "@example.com"
    conn.execute(
        insert(users).values(
            id=identifier,
            nickname="u_" + identifier.hex,
            display_name="Synthetic",
            email=email,
            password_hash=password_hasher.hash("original-password"),
        )
    )
    return identifier, email


def test_reissued_verification_activates_account_once(postgres):
    # Adapted from the previous verification test, which rejected replay.
    identifier, email = _seed_pending(postgres)
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    previous = issue_account_token(postgres, email, "verify_email")
    latest = issue_account_token(postgres, email.upper(), "verify_email")

    assert previous and latest and previous != latest
    assert not verify_email(postgres, previous)
    assert verify_email(postgres, latest)
    assert not verify_email(postgres, latest)
    assert issue_account_token(postgres, email, "verify_email") is None

    postgres.exec_driver_sql("RESET ROLE")
    account = postgres.execute(select(users).where(users.c.id == identifier)).mappings().one()
    assert account["status"] == "active"
    assert account["email_verified_at"] is not None
    record = (
        postgres.execute(
            select(account_tokens).where(
                account_tokens.c.token_hash == sha256(latest.encode()).digest(),
            )
        )
        .mappings()
        .one()
    )
    assert record["expires_at"] - record["created_at"] == timedelta(hours=24)
    assert record["consumed_at"] == account["email_verified_at"]


def test_reset_changes_password_and_revokes_every_session_of_that_account(postgres):
    identifier, email = _seed_pending(postgres)
    other_id, _ = _seed_pending(postgres)
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    session_tokens = [issue_session(postgres, identifier), issue_session(postgres, identifier)]
    other_session = issue_session(postgres, other_id)
    recovery_token = issue_account_token(postgres, email, "reset_password")
    new_hash = password_hasher.hash("replacement-password")

    assert recovery_token
    assert not verify_email(postgres, recovery_token)
    assert not reset_password(postgres, recovery_token, "plaintext")
    assert reset_password(postgres, recovery_token, new_hash)
    assert not reset_password(postgres, recovery_token, new_hash)
    assert all(get_session_user(postgres, token) is None for token in session_tokens)
    assert get_session_user(postgres, other_session) == other_id

    postgres.exec_driver_sql("RESET ROLE")
    account = postgres.execute(select(users).where(users.c.id == identifier)).mappings().one()
    assert password_hasher.verify(account["password_hash"], "replacement-password")
    assert account["status"] == "pending"
    record = (
        postgres.execute(
            select(account_tokens).where(
                account_tokens.c.token_hash == sha256(recovery_token.encode()).digest(),
            )
        )
        .mappings()
        .one()
    )
    assert record["expires_at"] - record["created_at"] == timedelta(minutes=30)


def test_expired_token_cannot_change_the_account(postgres):
    identifier, email = _seed_pending(postgres)
    token = issue_account_token(postgres, email, "verify_email")
    postgres.execute(
        update(account_tokens)
        .where(account_tokens.c.user_id == identifier)
        .values(
            created_at=func.now() - timedelta(days=2),
            expires_at=func.now() - timedelta(days=1),
        )
    )
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    assert not verify_email(postgres, token)
    postgres.exec_driver_sql("RESET ROLE")
    assert postgres.scalar(select(users.c.status).where(users.c.id == identifier)) == "pending"


def test_disabled_and_unknown_accounts_cannot_request_tokens(postgres):
    identifier, email = _seed_pending(postgres)
    postgres.execute(update(users).where(users.c.id == identifier).values(status="suspended"))
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    assert issue_account_token(postgres, email, "verify_email") is None
    assert issue_account_token(postgres, email, "reset_password") is None
    assert issue_account_token(postgres, "absent@example.com", "reset_password") is None


def test_token_storage_is_private_and_worker_cannot_apply_tokens(postgres):
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    with pytest.raises(ProgrammingError), postgres.begin_nested():
        postgres.execute(select(account_tokens.c.token_hash))
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_worker")
    with pytest.raises(ProgrammingError), postgres.begin_nested():
        verify_email(postgres, "a" * 43)
