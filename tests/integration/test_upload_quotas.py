import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import UploadFile
from sqlalchemy import text

from backend.api.common import ApiError
from backend.api.materials import accept_file, upload
from backend.db.connection import one, transaction
from tests.integration.test_grading_integrity import request
from tests.integration.test_materials import configure, prepare
from tests.integration.test_purge import run_purge


def limits(admin, course=100, daily=100):
    with admin.begin() as conn:
        conn.execute(
            text("""INSERT INTO auth.upload_limits VALUES(
            'synthetic_limits',clock_timestamp()-interval '1 second',:course,:daily)"""),
            dict(course=course, daily=daily),
        )


def another_course(admin, uid, space):
    cid = uuid4()
    with admin.begin() as conn:
        conn.execute(text("SELECT set_config('app.user_id',:uid,true)"), dict(uid=str(uid)))
        conn.execute(
            text("SELECT auth.create_course(:cid,'Other synthetic course','','')"), dict(cid=cid)
        )
        conn.execute(
            text(
                "INSERT INTO app.course_embedding_config(course_id,active_space_id) VALUES(:cid,:space)"
            ),
            dict(cid=cid, space=space),
        )
    return cid


def test_mixed_batch_keeps_valid_files_closes_streams_and_replays_without_new_usage(
    database, monkeypatch
):
    admin, url = database
    uid, cid, _ = prepare(admin, url, monkeypatch)
    monkeypatch.setattr("backend.api.materials.MAX_FILE_BYTES", 8)
    req = request("POST", f"/courses/{cid}/materials")

    def send():
        files = [
            UploadFile(BytesIO(body), filename=name)
            for name, body in (
                ("large.txt", b"012345678"),
                ("good.md", b"# ok"),
                ("bad.pdf", b"a"),
                ("empty.txt", b""),
            )
        ]
        result = asyncio.run(upload(cid, req, files, uid))
        assert all(file.file.closed for file in files)
        return result

    first = send()
    assert send() == first
    result = first["data"]["results"]
    assert result[0]["error"]["code"] == "FILE_TOO_LARGE"
    assert result[1]["data"]["status"] == "queued"
    assert result[2]["error"]["code"] == "UNSUPPORTED_FORMAT"
    assert result[3]["error"]["code"] == "EMPTY_DOCUMENT"
    with admin.connect() as conn:
        assert one(conn, "SELECT count(*) n FROM app.documents")["n"] == 1
        assert one(conn, "SELECT count(*) n FROM auth.upload_usage")["n"] == 1


def test_course_quota_counts_withdrawn_originals_hidden_by_rls(database, monkeypatch):
    admin, url = database
    uid, cid, _ = prepare(admin, url, monkeypatch)
    limits(admin, course=10)
    with transaction(uid) as conn:
        first = accept_file(conn, cid, uid, "first.txt", b"abcdef")
        one(
            conn,
            "SELECT auth.request_document_delete(:cid,:did,:rid) AS id",
            cid=cid,
            did=UUID(first["materialId"]),
            rid=uuid4(),
        )
    with transaction(uid) as conn:
        assert one(conn, "SELECT count(*) n FROM app.document_versions")["n"] == 0
    with pytest.raises(ApiError) as error:
        with transaction(uid) as conn:
            accept_file(conn, cid, uid, "second.txt", b"ghijkl")
    assert error.value.code == "UPLOAD_QUOTA_EXCEEDED"
    with admin.connect() as conn:
        assert one(conn, "SELECT count(*) n FROM auth.upload_usage")["n"] == 1


def test_daily_quota_survives_purge_counts_other_courses_and_expires(
    database, monkeypatch, tmp_path
):
    admin, url = database
    uid, cid, space = prepare(admin, url, monkeypatch)
    limits(admin, daily=12)
    other = another_course(admin, uid, space)
    with transaction(uid) as conn:
        first = accept_file(conn, cid, uid, "first.txt", b"abcdef")
        one(
            conn,
            "SELECT auth.request_document_delete(:cid,:did,:rid) AS id",
            cid=cid,
            did=UUID(first["materialId"]),
            rid=uuid4(),
        )
    run_purge(url, monkeypatch, tmp_path)
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        accept_file(conn, other, uid, "second.txt", b"ghijkl")
    with pytest.raises(ApiError) as error:
        with transaction(uid) as conn:
            accept_file(conn, cid, uid, "third.txt", b"m")
    assert error.value.code == "UPLOAD_QUOTA_EXCEEDED"
    with admin.begin() as conn:
        assert (
            one(conn, "SELECT count(*) n FROM app.document_versions WHERE course_id=:cid", cid=cid)[
                "n"
            ]
            == 0
        )
        conn.execute(
            text(
                "UPDATE auth.upload_usage SET accepted_at=clock_timestamp()-interval '25 hours' WHERE version_id=:id"
            ),
            dict(id=UUID(first["versionId"])),
        )
    with transaction(uid) as conn:
        accept_file(conn, cid, uid, "third.txt", b"mnopqr")


def test_simultaneous_uploads_to_different_courses_cannot_exceed_user_quota(database, monkeypatch):
    admin, url = database
    uid, cid, space = prepare(admin, url, monkeypatch)
    limits(admin, daily=10)
    other = another_course(admin, uid, space)
    barrier = Barrier(2)
    with transaction(uid) as conn:
        one(conn, "SELECT 1 AS ready")  # Initialize the shared pool before the race.

    def submit(course, body):
        barrier.wait(timeout=10)
        try:
            with transaction(uid) as conn:
                accept_file(conn, course, uid, "source.txt", body)
            return "accepted"
        except ApiError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(submit, cid, b"abcdef")
        second = executor.submit(submit, other, b"ghijkl")
        assert sorted([first.result(timeout=20), second.result(timeout=20)]) == [
            "UPLOAD_QUOTA_EXCEEDED",
            "accepted",
        ]
    with admin.connect() as conn:
        assert (
            one(
                conn, "SELECT sum(size_bytes) n FROM auth.upload_usage WHERE user_id=:uid", uid=uid
            )["n"]
            == 6
        )
        assert one(conn, "SELECT count(*) n FROM app.document_versions")["n"] == 1
