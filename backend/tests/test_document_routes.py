from contextlib import nullcontext
import warnings

from fastapi.testclient import TestClient
from sqlalchemy import insert

from backend import http_api
from backend.db.sessions import issue_session
from backend.routes import dependencies
from db.courses import create_course, memberships
from db.documents import DuplicateDocumentWarning
from test_courses import _as_user, _user


def test_document_upload_replay_listing_and_status(postgres, monkeypatch):
    owner, outsider = _user(postgres), _user(postgres)
    owner_token = issue_session(postgres, owner)
    outsider_token = issue_session(postgres, outsider)
    _as_user(postgres, owner)
    course = create_course(postgres, name="Documentos")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))

    client = TestClient(http_api.app)
    path = f"/api/v1/courses/{course}/documents"
    owner_headers = {"Authorization": f"Bearer {owner_token}", "Idempotency-Key": "upload"}
    outsider_headers = {"Authorization": f"Bearer {outsider_token}"}
    files = [("files", ("guide.md", b"contenido", "text/markdown"))]
    first = client.post(path, files=files, headers=owner_headers)
    assert first.status_code == 202
    (accepted,) = first.json()["data"]
    assert accepted["documentId"] and accepted["jobId"] and not accepted["duplicate"]
    assert client.post(path, files=files, headers=owner_headers).json() == first.json()
    changed = [("files", ("guide.md", b"distinto", "text/markdown"))]
    assert client.post(path, files=changed, headers=owner_headers).status_code == 409
    with warnings.catch_warnings():
        warnings.simplefilter("error", DuplicateDocumentWarning)
        duplicate = client.post(
            path, files=files, headers=owner_headers | {"Idempotency-Key": "other"}
        )
    assert duplicate.json()["data"] == [{"documentId": None, "jobId": None, "duplicate": True}]

    listed = client.get(path, headers=owner_headers).json()
    assert [item["id"] for item in listed["data"]] == [accepted["documentId"]]
    assert listed["nextCursor"] is None
    status_path = f"{path}/{accepted['documentId']}/status"
    assert client.get(status_path, headers=owner_headers).json()["data"]["status"] == "queued"
    assert client.get(status_path, headers=outsider_headers).status_code == 404
    assert client.get(path, headers=outsider_headers).status_code == 404


def test_document_detail_download_and_delete_follow_membership(postgres, monkeypatch):
    owner, student, outsider = (_user(postgres) for _ in range(3))
    tokens = {user: issue_session(postgres, user) for user in (owner, student, outsider)}
    _as_user(postgres, owner)
    course = create_course(postgres, name="Archivos")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=student, role="student"))
    _as_user(postgres, owner)
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)

    def headers(user):
        return {"Authorization": f"Bearer {tokens[user]}"}

    collection = f"/api/v1/courses/{course}/documents"
    original = "Árbol\n".encode()
    uploaded = client.post(
        collection, files=[("files", ("guía.md", original, "text/markdown"))],
        headers=headers(owner) | {"Idempotency-Key": "details"},
    )
    assert uploaded.status_code == 202
    path = f"{collection}/{uploaded.json()['data'][0]['documentId']}"
    detail = client.get(path, headers=headers(student))
    assert detail.status_code == 200
    assert detail.json()["data"]["name"] == "guía.md"
    assert detail.json()["data"]["size"] == len(original)
    download = client.get(path + "/download", headers=headers(student))
    assert download.status_code == 200
    assert download.content == original
    assert download.headers["content-disposition"] == "attachment; filename*=UTF-8''gu%C3%ADa.md"
    for endpoint in (path, path + "/download"):
        assert client.get(endpoint, headers=headers(outsider)).status_code == 404

    assert client.delete(path, headers=headers(outsider)).status_code == 404
    assert client.delete(path, headers=headers(student)).status_code == 403
    deleted = client.delete(path, headers=headers(owner))
    assert deleted.status_code == 204 and deleted.content == b""
    assert client.get(path, headers=headers(owner)).status_code == 404
    assert client.get(path + "/download", headers=headers(owner)).status_code == 404
    assert client.delete(path, headers=headers(owner)).status_code == 404
