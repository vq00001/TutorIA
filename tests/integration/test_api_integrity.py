"""Behavioral integration checks for authentication, DTOs and SQL invariants."""

import base64
import smtplib
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from backend.api.common import ApiError
from backend.api.courses import list_courses
from backend.api.exams import AnswerInput, get_attempt, save_answer
from backend.api.main import app
from backend.db.connection import transaction
from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.requests import Request

from tests.integration.test_database import seed
from tests.integration.test_materials import configure


@pytest.fixture
def browser(database, monkeypatch, tmp_path):
    admin, url = database
    configure(monkeypatch, url)
    sid = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.embedding_spaces(id,provider,model_name,model_revision,dimension) VALUES(:id,'ollama','synthetic','v1',3)"
            ),
            dict(id=sid),
        )
    for key, value in dict(
        APP_ORIGIN="https://testserver",
        SMTP_HOST="mail-stub",
        SMTP_FROM="test@example.com",
        MODEL_LLM="synthetic",
        MODEL_LLM_REVISION="v1",
        EMBEDDING_SPACE_ID=str(sid),
        LEDGER_KEY=base64.b64encode(b"x" * 32).decode(),
        LEDGER_PATH=str(tmp_path),
    ).items():
        monkeypatch.setenv(key, value)
    sent = []
    monkeypatch.setattr(
        "backend.api.auth.send_token",
        lambda email, purpose, raw: sent.append((email, purpose, raw)),
    )
    with TestClient(app, base_url="https://testserver") as client:
        csrf = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
        headers = {"origin": "https://testserver", "x-csrf-token": csrf}
        yield admin, client, headers, sent


def register(
    client, headers, sent, email="synthetic@example.com", password="  synthetic passphrase 1234  "
):
    result = client.post(
        "/api/v1/auth/register",
        headers=headers,
        json=dict(
            email=email,
            nickname="synthetic_user",
            displayName="  Synthetic  ",
            password=password,
            acceptTerms=True,
            acknowledgeAI=True,
        ),
    )
    assert result.status_code == 201, result.text
    token = sent[-1][2]
    assert (
        client.post("/api/v1/auth/verify-email", headers=headers, json={"token": token}).status_code
        == 200
    )
    result = client.post(
        "/api/v1/auth/login", headers=headers, json={"email": email, "password": password}
    )
    assert result.status_code == 200, result.text
    return password


def test_public_text_is_trimmed_but_password_whitespace_is_preserved(browser):
    admin, client, headers, sent = browser
    password = register(client, headers, sent, email="  SYNTHETIC@example.com  ")
    profile = client.get("/api/v1/student/me").json()["data"]
    assert profile["displayName"] == "Synthetic" and profile["email"] == "synthetic@example.com"
    rejected = client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"email": "synthetic@example.com", "password": password.strip()},
    )
    assert rejected.status_code == 401
    with admin.connect() as conn:
        record = conn.execute(
            text("SELECT password_hash FROM app.users WHERE id=:id"), dict(id=UUID(profile["id"]))
        ).scalar_one()
        assert record.startswith("$argon2id$") and password not in record


def test_registration_and_recovery_do_not_disclose_existing_email(browser, monkeypatch):
    admin, client, headers, sent = browser
    register(client, headers, sent)
    duplicated = client.post(
        "/api/v1/auth/register",
        headers=headers,
        json=dict(
            email="synthetic@example.com",
            nickname="synthetic_user",
            displayName="Another",
            password="another synthetic passphrase",
            acceptTerms=True,
            acknowledgeAI=True,
        ),
    )
    assert duplicated.status_code == 201 and duplicated.json() == {"data": {"status": "pending"}}

    def unavailable(*args):
        raise smtplib.SMTPException("synthetic mail outage")

    monkeypatch.setattr("backend.api.auth.send_token", unavailable)
    known = client.post(
        "/api/v1/auth/forgot-password", headers=headers, json={"email": "synthetic@example.com"}
    )
    unknown = client.post(
        "/api/v1/auth/forgot-password", headers=headers, json={"email": "missing@example.com"}
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json() == {"data": {"status": "accepted"}}


def test_rejected_login_counts_are_durable_and_buckets_have_no_plaintext(browser):
    admin, client, headers, sent = browser
    for _ in range(10):
        assert (
            client.post(
                "/api/v1/auth/login",
                headers=headers,
                json={"email": "missing@example.com", "password": "wrong"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/v1/auth/login",
            headers=headers,
            json={"email": "missing@example.com", "password": "wrong"},
        ).status_code
        == 429
    )
    with admin.connect() as conn:
        counters = conn.execute(text("SELECT bucket,attempts FROM auth.request_limits")).all()
    assert len(counters) == 2 and all(
        len(bytes(row.bucket)) == 32 and row.attempts == 11 for row in counters
    )


def test_email_change_keeps_original_until_verification_then_revokes_sessions(browser):
    admin, client, headers, sent = browser
    password = register(client, headers, sent)
    result = client.post(
        "/api/v1/student/me/email",
        headers=headers,
        json={"email": "  new+alias@example.com  ", "currentPassword": password},
    )
    assert result.status_code == 202, result.text
    assert client.get("/api/v1/student/me").json()["data"]["email"] == "synthetic@example.com"
    address, purpose, raw = sent[-1]
    assert address == "new+alias@example.com" and purpose == "verify_email"
    assert (
        client.post("/api/v1/auth/verify-email", headers=headers, json={"token": raw}).status_code
        == 200
    )
    assert client.get("/api/v1/student/me").status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login",
            headers=headers,
            json={"email": "synthetic@example.com", "password": password},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            headers=headers,
            json={"email": "new+alias@example.com", "password": password},
        ).status_code
        == 200
    )


def test_suspended_account_cannot_consume_pending_verification_token(browser):
    admin, client, headers, sent = browser
    result = client.post(
        "/api/v1/auth/register",
        headers=headers,
        json=dict(
            email="pending@example.com",
            nickname="pending_user",
            displayName="Pending",
            password="pending synthetic passphrase",
            acceptTerms=True,
            acknowledgeAI=True,
        ),
    )
    assert result.status_code == 201
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE app.users SET status='suspended' WHERE email='pending@example.com'")
        )
    assert (
        client.post(
            "/api/v1/auth/verify-email", headers=headers, json={"token": sent[-1][2]}
        ).status_code
        == 422
    )
    with admin.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT consumed_at FROM app.account_tokens WHERE user_id=(SELECT id FROM app.users WHERE email='pending@example.com')"
                )
            ).scalar_one()
            is None
        )


def create_attempt(admin):
    uid, cid = seed(admin)
    gid, eid, concept, qid, aid, answer = tuple(uuid4() for _ in range(6))
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.generation_runs(id,course_id,user_id,kind,provider,model_name,model_revision,prompt_version,parameters,corpus_revision,graph_revision,request_hash) VALUES(:id,:cid,:uid,'exam','ollama','synthetic','v1','v1','{}',0,0,:hash)"
            ),
            dict(id=gid, cid=cid, uid=uid, hash=b"x" * 32),
        )
        conn.execute(
            text(
                "INSERT INTO app.exams(id,course_id,user_id,generation_id,title,kind,duration_seconds) VALUES(:id,:cid,:uid,:gid,'Synthetic','practice',600)"
            ),
            dict(id=eid, cid=cid, uid=uid, gid=gid),
        )
        conn.execute(
            text(
                "INSERT INTO app.concepts(id,course_id,canonical_key,label,status) VALUES(:id,:cid,:key,'Synthetic','approved')"
            ),
            dict(id=concept, cid=cid, key=str(concept)),
        )
        conn.execute(
            text(
                "INSERT INTO app.questions(id,course_id,user_id,exam_id,concept_id,position,kind,prompt) VALUES(:id,:cid,:uid,:eid,:concept,1,'multiple','private question marker')"
            ),
            dict(id=qid, cid=cid, uid=uid, eid=eid, concept=concept),
        )
        conn.execute(
            text(
                "INSERT INTO app.exam_attempts(id,course_id,user_id,exam_id,attempt_no,deadline_at) VALUES(:id,:cid,:uid,:eid,1,clock_timestamp()+interval '10 minutes')"
            ),
            dict(id=aid, cid=cid, uid=uid, eid=eid),
        )
        conn.execute(
            text(
                "INSERT INTO app.answers(id,course_id,user_id,exam_id,attempt_id,question_id) VALUES(:id,:cid,:uid,:eid,:aid,:qid)"
            ),
            dict(id=answer, cid=cid, uid=uid, eid=eid, aid=aid, qid=qid),
        )
    return dict(uid=uid, cid=cid, gid=gid, eid=eid, qid=qid, aid=aid, answer=answer)


@pytest.mark.parametrize(
    "points,status", [(None, "accepted"), (-1, "accepted"), (2, "accepted"), (0, "needs_review")]
)
def test_grade_range_and_review_null_invariant(database, points, status):
    admin, url = database
    case = create_attempt(admin)
    with pytest.raises(Exception, match="INVALID_GRADE"):
        with admin.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app.grades(id,course_id,user_id,answer_id,revision,points,status,method,feedback,grader_version) VALUES(:id,:cid,:uid,:answer,1,:points,:status,'deterministic','','v1')"
                ),
                dict(
                    id=uuid4(),
                    cid=case["cid"],
                    uid=case["uid"],
                    answer=case["answer"],
                    points=points,
                    status=status,
                ),
            )


def test_terminal_state_cannot_be_reopened_and_invalidated_dto_is_redacted(database, monkeypatch):
    admin, url = database
    case = create_attempt(admin)
    with pytest.raises(Exception, match="INVALID_TRANSITION"):
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE app.generation_runs SET status='completed' WHERE id=:id"),
                dict(id=case["gid"]),
            )
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE app.exam_attempts SET status='invalidated' WHERE id=:id"),
            dict(id=case["aid"]),
        )
    configure(monkeypatch, url)
    with transaction(case["uid"]) as conn:
        result = get_attempt(case["aid"], conn)
        assert result["data"]["questions"] == [] and result["data"]["score_percent"] is None
        assert "private question marker" not in str(result)


def test_duplicate_foreign_options_and_closed_attempt_cannot_save(database, monkeypatch):
    admin, url = database
    case = create_attempt(admin)
    valid, foreign, other_question = uuid4(), uuid4(), uuid4()
    with admin.begin() as conn:
        concept = conn.execute(
            text("SELECT concept_id FROM app.questions WHERE id=:id"), dict(id=case["qid"])
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO app.questions(id,course_id,user_id,exam_id,concept_id,position,kind,prompt) VALUES(:id,:cid,:uid,:eid,:concept,2,'multiple','Other')"
            ),
            dict(
                id=other_question,
                cid=case["cid"],
                uid=case["uid"],
                eid=case["eid"],
                concept=concept,
            ),
        )
        for oid, qid in ((valid, case["qid"]), (foreign, other_question)):
            conn.execute(
                text(
                    "INSERT INTO app.question_options(id,course_id,user_id,exam_id,question_id,position,text) VALUES(:id,:cid,:uid,:eid,:qid,1,'Synthetic')"
                ),
                dict(id=oid, cid=case["cid"], uid=case["uid"], eid=case["eid"], qid=qid),
            )
    configure(monkeypatch, url)
    request = Request(
        {
            "type": "http",
            "method": "PUT",
            "path": f"/api/v1/attempts/{case['aid']}/answers/{case['qid']}",
            "headers": [(b"if-match", b"1"), (b"idempotency-key", str(uuid4()).encode())],
            "scheme": "https",
            "server": ("testserver", 443),
        }
    )
    with transaction(case["uid"]) as conn:
        for selections in ([valid, valid], [foreign]):
            with pytest.raises(ApiError, match="INVALID_ANSWER"):
                save_answer(
                    case["aid"], case["qid"], AnswerInput(optionIds=selections), request, conn
                )
        conn.execute(text("SELECT auth.submit_attempt(:id)"), dict(id=case["aid"]))
        with pytest.raises(ApiError, match="ATTEMPT_CLOSED"):
            save_answer(case["aid"], case["qid"], AnswerInput(optionIds=[valid]), request, conn)


def test_course_pagination_uses_creation_order_and_derived_progress(database, monkeypatch):
    admin, url = database
    uid, cid = seed(admin)
    with admin.begin() as conn:
        conn.execute(text("SELECT set_config('app.user_id',:id,true)"), dict(id=str(uid)))
        for day in (1, 2):
            other = uuid4()
            conn.execute(text("SELECT auth.create_course(:id,'Earlier','','')"), dict(id=other))
            conn.execute(
                text("UPDATE app.courses SET created_at=:at WHERE id=:id"),
                dict(id=other, at=datetime(2026, 1, day, tzinfo=timezone.utc)),
            )
        expected = (
            conn.execute(text("SELECT id FROM app.courses ORDER BY created_at,id")).scalars().all()
        )
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        actual = []
        cursor = None
        while True:
            page = list_courses(limit=1, cursor=cursor, conn=conn)
            actual.extend(row["id"] for row in page["data"])
            assert all(row["progress"] == 0 and row["lastActivity"] is None for row in page["data"])
            cursor = page["meta"]["nextCursor"]
            if cursor is None:
                break
        assert actual == expected and len(actual) == len(set(actual)) == 3
        with pytest.raises(ApiError, match="INVALID_CURSOR"):
            list_courses(cursor="malformed", conn=conn)
