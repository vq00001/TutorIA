from datetime import timedelta

import pytest
import test_exams
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from test_exams import _generation, _role

from db.documents import DuplicateDocumentWarning, delete_document, list_documents, register_upload
from db.generations import generation_runs, stop_generation
from db.quotas import quota_limits, upload_usage

quota_context = test_exams.exam_context


def _set_upload_limits(conn, actor, *, course_bytes, user_daily_bytes):
    conn.exec_driver_sql("RESET ROLE")
    conn.execute(
        update(quota_limits)
        .where(quota_limits.c.version == "v1")
        .values(course_bytes=course_bytes, user_daily_bytes=user_daily_bytes)
    )
    _role(conn, actor)


def _upload(conn, course, data):
    return register_upload(
        conn, course_id=course, filename="test.md", data=data, mime_type="text/markdown"
    )


def test_course_quota_discards_duplicates_and_releases_space_on_delete(postgres, quota_context):
    owner, peer, course, _ = quota_context
    _set_upload_limits(postgres, owner, course_bytes=5, user_daily_bytes=20)
    doc = _upload(postgres, course, b"abcde")
    with pytest.warns(DuplicateDocumentWarning):
        assert _upload(postgres, course, b"abcde") is None
    _role(postgres, peer)
    with pytest.raises(IntegrityError, match="UPLOAD_QUOTA_EXCEEDED"), postgres.begin_nested():
        _upload(postgres, course, b"f")
    assert [row["id"] for row in list_documents(postgres, course, limit=5)] == [doc]
    _role(postgres, owner)
    assert delete_document(postgres, course, doc)
    _role(postgres, peer)
    assert _upload(postgres, course, b"f")
    with pytest.raises(ProgrammingError, match="permission denied"), postgres.begin_nested():
        postgres.execute(select(upload_usage))
    postgres.exec_driver_sql("RESET ROLE")
    assert (
        postgres.scalar(
            select(func.count())
            .select_from(upload_usage)
            .where(upload_usage.c.user_id.in_((owner, peer)))
        )
        == 2
    )


def test_daily_upload_quota_spans_classes_and_outlives_deleted_files(postgres, quota_context):
    owner, _, course, other = quota_context
    _set_upload_limits(postgres, owner, course_bytes=100, user_daily_bytes=5)
    doc = _upload(postgres, course, b"abcde")
    assert delete_document(postgres, course, doc)
    with pytest.raises(IntegrityError, match="UPLOAD_QUOTA_EXCEEDED"), postgres.begin_nested():
        _upload(postgres, other, b"f")
    assert list_documents(postgres, other, limit=5) == []
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        update(upload_usage)
        .where(upload_usage.c.user_id == owner)
        .values(accepted_at=func.now() - timedelta(hours=25))
    )
    _role(postgres, owner)
    assert _upload(postgres, other, b"abcde")


def test_generation_quota_counts_queued_and_running_across_courses(postgres, quota_context):
    owner, peer, course, other = quota_context
    running = _generation(postgres, course, record=False)
    queued = _generation(postgres, other, record=False)
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        update(generation_runs).where(generation_runs.c.id == queued).values(status="queued")
    )
    postgres.execute(
        update(generation_runs).where(generation_runs.c.id == queued).values(status="running")
    )
    postgres.execute(
        update(generation_runs).where(generation_runs.c.id == queued).values(status="queued")
    )
    _role(postgres, owner)
    with pytest.raises(IntegrityError, match="GENERATION_QUOTA_EXCEEDED"), postgres.begin_nested():
        _generation(postgres, other, record=False)

    _role(postgres, peer)
    assert not stop_generation(postgres, running, status="cancelled")
    assert _generation(postgres, course, record=False)
    _role(postgres, owner)
    assert stop_generation(postgres, queued, status="cancelled")
    assert not stop_generation(postgres, queued, status="failed")
    assert _generation(postgres, other, record=False)
    assert stop_generation(postgres, running, status="failed")
    assert _generation(postgres, course, record=False)
