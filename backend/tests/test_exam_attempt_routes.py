from contextlib import nullcontext
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.api import app as http_api
from backend.api.routes import dependencies
from backend.db.sessions import issue_session
from db.courses import create_course
from db.exams import list_exam_questions

from .test_courses import _as_user, _user
from .test_exams import DEVELOPMENT, MULTIPLE, _generation, _publish


def test_attempt_routes_start_submit_and_return_private_result(postgres, monkeypatch):
    owner, outsider = _user(postgres), _user(postgres)
    owner_token, outsider_token = [issue_session(postgres, user) for user in (owner, outsider)]
    _as_user(postgres, owner)
    course = create_course(postgres, name="Intentos")
    exam = _publish(postgres, course, _generation(postgres, course), [MULTIPLE, DEVELOPMENT])
    first, second = list_exam_questions(postgres, course, exam)
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)
    headers = {"Authorization": f"Bearer {owner_token}"}
    collection = f"/api/v1/courses/{course}/exams/{exam}/attempts"

    opened = client.post(collection, headers=headers)
    assert opened.status_code == 200
    attempt = opened.json()["data"]
    assert attempt["status"] == "in_progress"
    assert client.post(collection, headers=headers).json() == opened.json()
    path = f"{collection}/{attempt['id']}"

    invalid = {"answers": [{"questionId": str(uuid4())}]}
    assert client.post(path + "/submissions", json=invalid, headers=headers).status_code == 422
    payload = {"answers": [
        {"questionId": str(first.id),
         "selectedOptionIds": [first.options[0]["id"], first.options[2]["id"]]},
        {"questionId": str(second.id), "textAnswer": "Mi explicación"},
    ]}
    submitted = client.post(path + "/submissions", json=payload, headers=headers)
    assert submitted.status_code == 200
    result = submitted.json()["data"]
    assert result["status"] == "needs_review"
    assert result["scorePercent"] is None
    assert result["answers"][0]["gradeStatus"] == "accepted"
    assert result["answers"][1]["textAnswer"] == "Mi explicación"
    assert client.get(path, headers=headers).json() == submitted.json()
    assert client.post(path + "/submissions", json=payload, headers=headers).json() == submitted.json()

    private = {"Authorization": f"Bearer {outsider_token}"}
    assert client.get(path, headers=private).status_code == 404
    assert client.post(path + "/submissions", json=payload, headers=private).status_code == 404
    wrong_exam = path.replace(str(exam), str(uuid4()))
    assert client.get(wrong_exam, headers=headers).status_code == 404

    feedback = {"points": "0.65", "feedback": "Explica el paso principal."}
    review = f"{path}/answers/{result['answers'][1]['answerId']}/review"
    choice_review = f"{path}/answers/{result['answers'][0]['answerId']}/review"
    assert client.put(review, json=feedback, headers=private).status_code == 404
    assert client.put(choice_review, json=feedback, headers=headers).status_code == 422
    assert client.put(review, json=feedback | {"points": 999}, headers=headers).status_code == 422
    assert client.put(review, json=feedback | {"feedback": " "}, headers=headers).status_code == 422

    reviewed = client.put(review, json=feedback, headers=headers)
    assert reviewed.status_code == 200
    updated = reviewed.json()["data"]
    assert updated["status"] == "graded"
    assert Decimal(updated["scorePercent"]) == Decimal(90)
    answer = updated["answers"][1]
    assert Decimal(answer["points"]) == Decimal("0.65")
    assert answer["feedback"] == feedback["feedback"]
    assert (answer["gradeStatus"], answer["gradeMethod"]) == ("accepted", "manual")
    assert client.get(path, headers=headers).json() == reviewed.json()
    assert client.put(review, json=feedback, headers=headers).json() == reviewed.json()
    changed = client.put(review, json=feedback | {"points": "1"}, headers=headers)
    assert changed.status_code == 409
    assert changed.json()["error"] == "ANSWER_NOT_PENDING_REVIEW"
