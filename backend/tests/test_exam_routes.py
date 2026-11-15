from contextlib import nullcontext
from uuid import UUID

from fastapi.testclient import TestClient

from backend import http_api
from backend.db.sessions import issue_session
from backend.routes import dependencies
from db.courses import create_course
from db.exams import publish_exam
from db.generations import record_generation_request
from db.jobs import claim_job
from .test_courses import _as_user, _user
from .test_exams import MULTIPLE, REQUEST, _role


def test_exam_generation_replay_and_private_detail(postgres, monkeypatch):
    owner, peer = _user(postgres), _user(postgres)
    tokens = {user: issue_session(postgres, user) for user in (owner, peer)}
    _as_user(postgres, owner)
    course = create_course(postgres, name="Evaluaciones")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)
    path = f"/api/v1/courses/{course}/exams"
    headers = {"Authorization": f"Bearer {tokens[owner]}", "Idempotency-Key": "exam"}
    payload = {"topic": "Álgebra", "questionCount": 1, "questionTypes": ["multiple"]}

    first = client.post(path, json=payload, headers=headers)
    assert first.status_code == 202
    assert client.post(path, json=payload, headers=headers).json() == first.json()
    assert client.post(path, json=payload | {"topic": "Otro"}, headers=headers).status_code == 409
    assert client.post(path, json=payload | {"questionCount": 21}, headers=headers).status_code == 422
    assert client.get(path, headers=headers).json() == {"data": []}

    accepted = first.json()["data"]
    _role(postgres, None, worker=True)
    job = claim_job(postgres, "exam", kinds=["generate_exam"])
    assert str(job.id) == accepted["jobId"]
    assert str(job.target_id) == accepted["generationId"]
    assert job.payload == {
        "topic": "Álgebra", "question_count": 1, "duration_seconds": 1800,
        "question_types": ["multiple"],
    }

    _as_user(postgres, owner)
    generation = UUID(accepted["generationId"])
    request_id = record_generation_request(postgres, generation, REQUEST, [])
    exam = publish_exam(
        postgres, course, generation, title="Álgebra", kind="practice", duration_seconds=1800,
        items=[MULTIPLE], request_id=request_id,
    )
    listed = client.get(path, headers=headers).json()["data"]
    assert [row["id"] for row in listed] == [str(exam)]
    assert listed[0]["questionCount"] == 1
    detail = client.get(f"{path}/{exam}", headers=headers).json()["data"]
    (question,) = detail["questions"]
    assert set(question) == {"id", "position", "kind", "prompt", "maxPoints", "options"}
    assert question["options"]
    outsider = {"Authorization": f"Bearer {tokens[peer]}"}
    assert client.get(f"{path}/{exam}", headers=outsider).status_code == 404
