"""Real SQL jobs and correction flow; only the model provider is substituted."""

import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from starlette.requests import Request

from backend.api.exams import (
    AnswerInput,
    ExamInput,
    ManualReviewInput,
    create_exam,
    get_attempt,
    manual_review,
    request_regrade,
    save_answer,
    start,
    submit,
)
from backend.db.connection import one, rows, transaction
from backend.workers.exams import generate_exam, grade_attempt
from backend.workers.main import Lease
from tests.integration.test_materials import configure, prepare, publish


def test_failed_exam_keeps_input_lineage_and_source_removal_redacts_its_title(
    database, monkeypatch, tmp_path
):
    from tests.integration.test_purge import run_purge

    admin, url, uid, cid, published, concept = published_exam(
        database, monkeypatch, count=1
    )
    with transaction(uid) as conn:
        result = create_exam(
            cid,
            ExamInput(
                title="SOURCE_SENTINEL_failed_exam",
                questionCount=1,
                conceptIds=[concept],
                questionKinds=["alternatives"],
            ),
            request("POST", f"/courses/{cid}/exams"),
            uid,
            conn,
        )
    eid = UUID(result["data"]["id"])
    with admin.connect() as conn:
        gid = one(conn, "SELECT generation_id FROM app.exams WHERE id=:id", id=eid)[
            "generation_id"
        ]

    def provider_fails(*args):
        with admin.connect() as conn:
            traced = rows(
                conn,
                "SELECT parent_id FROM app.generation_sources WHERE generation_id=:id",
                id=gid,
            )
        assert {str(row["parent_id"]) for row in traced} == {
            str(s["sourceId"]) for s in args[2]["sources"]
        }
        raise RuntimeError("SYNTHETIC_PROVIDER_FAILURE")

    monkeypatch.setattr("backend.workers.exams.model_json", provider_fails)
    job = claim(monkeypatch, url, "generate_exam")
    with pytest.raises(RuntimeError, match="SYNTHETIC_PROVIDER_FAILURE"):
        generate_exam(job, Lease(job))
    with admin.begin() as conn:
        # The main worker records terminal failure after the provider raises.
        conn.execute(
            text(
                "UPDATE app.jobs SET state='failed',lease_token=NULL,lease_until=NULL WHERE id=:id"
            ),
            dict(id=job["id"]),
        )
        conn.execute(
            text("UPDATE app.generation_runs SET status='failed' WHERE id=:id"),
            dict(id=gid),
        )
        conn.execute(
            text("UPDATE app.exams SET status='failed' WHERE id=:id"), dict(id=eid)
        )
        document = one(
            conn,
            """SELECT p.document_id FROM app.generation_sources s
            JOIN app.parent_chunks p ON p.id=s.parent_id WHERE s.generation_id=:id LIMIT 1""",
            id=gid,
        )["document_id"]
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM app.questions WHERE exam_id=:id",
                id=eid,
            )["n"]
            == 0
        )
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        one(
            conn,
            "SELECT auth.request_document_delete(:cid,:did,:rid) AS id",
            cid=cid,
            did=document,
            rid=uuid4(),
        )
        failed = one(conn, "SELECT status,title FROM app.exams WHERE id=:id", id=eid)
        assert failed["status"] == "failed"
        assert failed["title"] == "Evaluación retirada"
        assert (
            one(conn, "SELECT status FROM app.exams WHERE id=:id", id=published)[
                "status"
            ]
            == "invalidated"
        )
    run_purge(url, monkeypatch, tmp_path)
    with admin.connect() as conn:
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM app.generation_sources WHERE generation_id=:id",
                id=gid,
            )["n"]
            == 0
        )
        generation = one(
            conn,
            "SELECT status,parameters FROM app.generation_runs WHERE id=:id",
            id=gid,
        )
        assert generation["status"] == "failed" and generation["parameters"] == {}
        assert (
            one(conn, "SELECT title FROM app.exams WHERE id=:id", id=eid)["title"]
            == "Evaluación retirada"
        )


def request(method, path, version=None):
    headers = [(b"idempotency-key", str(uuid4()).encode())]
    if version is not None:
        headers.append((b"if-match", str(version).encode()))
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/v1" + path,
            "headers": headers,
            "scheme": "https",
            "server": ("testserver", 443),
        }
    )


def claim(monkeypatch, url, kind):
    configure(monkeypatch, url.replace("tutoria_api:", "tutoria_worker:"))
    with transaction() as conn:
        job = one(conn, "SELECT * FROM auth.claim_job('grading-fixture')")
        assert job and job["kind"] == kind
    return job


def published_exam(database, monkeypatch, kind="alternatives", count=3):
    admin, url = database
    uid, cid, sid = prepare(admin, url, monkeypatch)
    accepted, _ = publish(admin, url, monkeypatch, uid, cid)
    concept = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE app.jobs SET state='cancelled' WHERE kind='extract_concepts'")
        )
        source = one(
            conn,
            "SELECT id,run_id FROM app.parent_chunks WHERE document_id=:id LIMIT 1",
            id=UUID(accepted["materialId"]),
        )
        conn.execute(
            text(
                "INSERT INTO app.concepts(id,course_id,canonical_key,label,status) VALUES(:id,:cid,:key,'Synthetic concept','approved')"
            ),
            dict(id=concept, cid=cid, key=str(concept)),
        )
        conn.execute(
            text(
                "INSERT INTO app.concept_sources(course_id,concept_id,parent_id,run_id,origin) VALUES(:cid,:concept,:parent,:run,'manual')"
            ),
            dict(cid=cid, concept=concept, parent=source["id"], run=source["run_id"]),
        )
    monkeypatch.setenv("MODEL_LLM", "synthetic")
    monkeypatch.setenv("MODEL_LLM_REVISION", "v1")
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        response = create_exam(
            cid,
            ExamInput(questionCount=count, conceptIds=[concept], questionKinds=[kind]),
            request("POST", f"/courses/{cid}/exams"),
            uid,
            conn,
        )
    eid = UUID(response["data"]["id"])
    question = dict(
        conceptId=str(concept),
        sourceIds=[str(source["id"])],
        kind=kind,
        prompt="Synthetic question",
        explanation="Synthetic explanation",
    )
    if kind == "development":
        question.update(
            options=[],
            correct=[],
            expectedAnswer="Synthetic reference",
            rubric={
                "version": "1",
                "criteria": [{"name": "Criterion", "maxPoints": 1}],
            },
        )
    else:
        question.update(
            options=["Correct", "Incorrect"],
            correct=[0],
            expectedAnswer=None,
            rubric=None,
        )
    monkeypatch.setattr(
        "backend.workers.exams.model_json",
        lambda *args: {"questions": [dict(question) for _ in range(count)]},
    )
    job = claim(monkeypatch, url, "generate_exam")
    generate_exam(job, Lease(job))
    configure(monkeypatch, url)
    return admin, url, uid, cid, eid, concept


def answer_and_submit(url, monkeypatch, uid, cid, eid, development=False):
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        attempt = start(
            cid, eid, request("POST", f"/courses/{cid}/exams/{eid}/attempts"), uid, conn
        )["data"]
        aid = attempt["id"]
        assert all(
            "review" not in q and q["grade"] is None for q in attempt["questions"]
        )
        for q in attempt["questions"]:
            body = AnswerInput(
                textAnswer="Synthetic response" if development else None,
                optionIds=[] if development else [q["options"][0]["id"]],
            )
            save_answer(
                aid,
                q["id"],
                body,
                request("PUT", f"/attempts/{aid}/answers/{q['id']}", 0),
                conn,
            )
        submit(aid, request("POST", f"/attempts/{aid}/submit"), conn)
    return aid


def test_only_first_graded_attempt_contributes_and_replay_does_not_bump_revision(
    database, monkeypatch
):
    admin, url, uid, cid, eid, concept = published_exam(database, monkeypatch)
    first = answer_and_submit(url, monkeypatch, uid, cid, eid)
    job = claim(monkeypatch, url, "grade_attempt")
    grade_attempt(job, Lease(job))
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        result = get_attempt(first, conn)["data"]
        assert result["status"] == "graded" and result["score_percent"] == 100
        assert all("review" in q for q in result["questions"])
        before = one(
            conn,
            "SELECT mastery_revision FROM app.learning_profiles WHERE course_id=:cid AND user_id=:uid",
            cid=cid,
            uid=uid,
        )["mastery_revision"]
        mastery = one(
            conn,
            "SELECT * FROM app.concept_mastery WHERE course_id=:cid AND user_id=:uid AND concept_id=:concept",
            cid=cid,
            uid=uid,
            concept=concept,
        )
        assert (
            mastery["level"] == "mastered"
            and mastery["evidence_count"] == 3
            and mastery["mastery_revision"] == before
        )
    second = answer_and_submit(url, monkeypatch, uid, cid, eid)
    job = claim(monkeypatch, url, "grade_attempt")
    grade_attempt(job, Lease(job))
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        assert get_attempt(second, conn)["data"]["score_percent"] == 100
        assert (
            one(
                conn,
                "SELECT count(*) n FROM app.mastery_events WHERE course_id=:cid AND valid",
                cid=cid,
            )["n"]
            == 3
        )
        assert (
            one(
                conn,
                "SELECT mastery_revision FROM app.learning_profiles WHERE course_id=:cid AND user_id=:uid",
                cid=cid,
                uid=uid,
            )["mastery_revision"]
            == before
        )
        assert (
            one(conn, "SELECT evidence_attempt_id FROM app.exams WHERE id=:id", id=eid)[
                "evidence_attempt_id"
            ]
            == first
        )
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.jobs(id,course_id,requested_by,kind,target_id,idempotency_key) VALUES(:id,:cid,:uid,'grade_attempt',:aid,:key)"
            ),
            dict(id=uuid4(), cid=cid, uid=uid, aid=first, key=str(uuid4())),
        )
    replay = claim(monkeypatch, url, "grade_attempt")
    grade_attempt(replay, Lease(replay))
    with transaction(uid) as conn:
        assert (
            one(
                conn,
                "SELECT mastery_revision FROM app.learning_profiles WHERE course_id=:cid AND user_id=:uid",
                cid=cid,
                uid=uid,
            )["mastery_revision"]
            == before
        )
        assert (
            one(
                conn,
                "SELECT count(*) n FROM app.grades WHERE answer_id IN(SELECT id FROM app.answers WHERE attempt_id=:aid)",
                aid=first,
            )["n"]
            == 3
        )


def test_invalid_llm_grade_retries_append_revision_and_manual_self_removes_evidence(
    database, monkeypatch
):
    admin, url, uid, cid, eid, concept = published_exam(
        database, monkeypatch, kind="development", count=1
    )
    aid = answer_and_submit(url, monkeypatch, uid, cid, eid, development=True)
    monkeypatch.setattr(
        "backend.workers.exams.model_json",
        lambda *args: {"points": [99], "feedback": "Invalid synthetic grade"},
    )
    job = claim(monkeypatch, url, "grade_attempt")
    grade_attempt(job, Lease(job))
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        pending = get_attempt(aid, conn)["data"]
        assert pending["status"] == "needs_review" and pending["score_percent"] is None
        assert pending["questions"][0]["grade"]["points"] is None
        assert not rows(
            conn, "SELECT id FROM app.mastery_events WHERE course_id=:cid", cid=cid
        )
        assert (
            one(
                conn,
                "SELECT mastery_revision FROM app.learning_profiles WHERE course_id=:cid AND user_id=:uid",
                cid=cid,
                uid=uid,
            )["mastery_revision"]
            == 0
        )
        request_regrade(aid, request("POST", f"/attempts/{aid}/regrade"), conn)
    monkeypatch.setattr(
        "backend.workers.exams.model_json",
        lambda *args: {"points": [1], "feedback": "Valid synthetic grade"},
    )
    revised = claim(monkeypatch, url, "grade_attempt")
    grade_attempt(revised, Lease(revised))
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        graded = get_attempt(aid, conn)["data"]
        assert graded["status"] == "graded" and graded["score_percent"] == 100
        answerid = graded["questions"][0]["answer"]["id"]
        evidence = one(
            conn,
            "SELECT value,weight FROM app.mastery_events WHERE course_id=:cid AND valid",
            cid=cid,
        )
        assert evidence["value"] == 1 and evidence["weight"] == Decimal(".5")
        manual_review(
            aid,
            answerid,
            ManualReviewInput(points=0, feedback="Self review"),
            request("POST", f"/attempts/{aid}/answers/{answerid}/review"),
            conn,
        )
        reviewed = get_attempt(aid, conn)["data"]
        assert reviewed["status"] == "graded" and reviewed["score_percent"] == 0
        assert (
            one(
                conn,
                "SELECT count(*) n FROM app.mastery_events WHERE course_id=:cid AND valid",
                cid=cid,
            )["n"]
            == 0
        )
        assert not rows(
            conn,
            "SELECT concept_id FROM app.concept_mastery WHERE course_id=:cid",
            cid=cid,
        )
    with admin.connect() as conn:
        history = rows(
            conn,
            "SELECT revision,status,points,grader_version,supersedes_id FROM app.grades WHERE answer_id=:id ORDER BY revision",
            id=answerid,
        )
        assert [g["revision"] for g in history] == [1, 2, 3]
        assert history[0]["status"] == "needs_review" and history[0]["points"] is None
        assert (
            history[2]["grader_version"] == "manual_self"
            and history[2]["supersedes_id"] is not None
        )


def test_source_withdrawal_cancels_active_generations_and_redacts_every_derived_message(
    database, monkeypatch
):
    admin, url, uid, cid, eid, concept = published_exam(database, monkeypatch, count=1)
    generations = {}
    with admin.begin() as conn:
        source = one(
            conn,
            "SELECT id,run_id,document_id FROM app.parent_chunks WHERE course_id=:cid LIMIT 1",
            cid=cid,
        )
        for status, message_status in [
            ("queued", "pending"),
            ("running", "streaming"),
            ("completed", "completed"),
            ("failed", "failed"),
            ("cancelled", "cancelled"),
        ]:
            gid, tid, mid = uuid4(), uuid4(), uuid4()
            generations[status] = gid
            conn.execute(
                text(
                    "INSERT INTO app.generation_runs(id,course_id,user_id,kind,status,provider,model_name,model_revision,prompt_version,parameters,corpus_revision,graph_revision,request_hash) VALUES(:id,:cid,:uid,'chat',:status,'ollama','synthetic','v1','v1','{}',0,0,:hash)"
                ),
                dict(id=gid, cid=cid, uid=uid, status=status, hash=b"x" * 32),
            )
            conn.execute(
                text(
                    "INSERT INTO app.conversations(id,course_id,user_id,title) VALUES(:id,:cid,:uid,'Synthetic conversation')"
                ),
                dict(id=tid, cid=cid, uid=uid),
            )
            conn.execute(
                text(
                    "INSERT INTO app.messages(id,course_id,user_id,conversation_id,sequence_no,role,status,parts,generation_id) VALUES(:id,:cid,:uid,:tid,1,'assistant',:status,CAST(:parts AS jsonb),:gid)"
                ),
                dict(
                    id=mid,
                    cid=cid,
                    uid=uid,
                    tid=tid,
                    status=message_status,
                    parts=json.dumps(
                        [
                            {
                                "type": "markdown",
                                "content": "synthetic withdrawn-derived-marker",
                            }
                        ]
                    ),
                    gid=gid,
                ),
            )
            conn.execute(
                text(
                    "INSERT INTO app.generation_sources(course_id,generation_id,parent_id,run_id,rank,score) VALUES(:cid,:gid,:parent,:run,1,.9)"
                ),
                dict(cid=cid, gid=gid, parent=source["id"], run=source["run_id"]),
            )
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        one(
            conn,
            "SELECT auth.request_document_delete(:cid,:did,:rid) AS id",
            cid=cid,
            did=source["document_id"],
            rid=uuid4(),
        )
    with admin.connect() as conn:
        for previous, gid in generations.items():
            current = one(
                conn, "SELECT status FROM app.generation_runs WHERE id=:id", id=gid
            )["status"]
            assert current == (
                "cancelled"
                if previous in ("queued", "running")
                else "invalidated"
                if previous == "completed"
                else previous
            )
            message = one(
                conn,
                "SELECT status,parts FROM app.messages WHERE generation_id=:id",
                id=gid,
            )
            assert message["status"] == "redacted" and message["parts"] == []
