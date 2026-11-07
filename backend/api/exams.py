import os
from decimal import Decimal
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import text

from backend.api.auth import Input
from backend.api.common import (
    ApiError,
    connection,
    course,
    current_user,
    enqueue,
    idempotent,
    json_value,
    match,
    now,
)
from backend.db.connection import one, rows

router = APIRouter()


class ExamInput(Input):
    title: str = Field(default="Práctica personal", min_length=1, max_length=255)
    kind: str = Field(default="practice", pattern="^(diagnostic|practice)$")
    questionCount: int = Field(default=5, ge=1, le=20)
    durationSeconds: int = Field(default=1200, ge=60, le=7200)
    conceptIds: list[UUID] = Field(default_factory=list, max_length=20)
    questionKinds: list[Literal["alternatives", "multiple", "true_false", "development"]] = Field(
        default=["alternatives"], min_length=1, max_length=4
    )


class AnswerInput(Input):
    optionIds: list[UUID] = Field(default_factory=list, max_length=6)
    textAnswer: str | None = Field(default=None, max_length=20000)
    flagged: bool = False


def exam(conn, cid, eid):
    course(conn, cid)
    e = one(
        conn,
        "SELECT * FROM app.exams WHERE id=:id AND course_id=:cid AND user_id=auth.uid()",
        id=eid,
        cid=cid,
    )
    if not e:
        raise ApiError("NOT_FOUND", 404)
    return e


def attempt(conn, aid):
    a = one(conn, "SELECT * FROM app.exam_attempts WHERE id=:id AND user_id=auth.uid()", id=aid)
    if not a:
        raise ApiError("NOT_FOUND", 404)
    course(conn, a["course_id"])
    return a


@router.get("/courses/{cid}/exams")
def list_exams(cid: UUID, conn=Depends(connection, scope="function")):
    course(conn, cid)
    return {
        "data": rows(
            conn,
            """SELECT e.*,(SELECT count(*) FROM app.questions q WHERE q.exam_id=e.id) question_count,
        latest.id latest_attempt_id,latest.status latest_attempt_status,latest.score_percent latest_score
      FROM app.exams e LEFT JOIN LATERAL(SELECT a.id,a.status,a.score_percent FROM app.exam_attempts a
        WHERE a.exam_id=e.id AND a.course_id=e.course_id AND a.user_id=e.user_id
        ORDER BY a.attempt_no DESC,a.id DESC LIMIT 1) latest ON true
      WHERE e.course_id=:cid ORDER BY e.created_at DESC,e.id LIMIT 100""",
            cid=cid,
        )
    }


@router.post("/courses/{cid}/exams", status_code=202)
def create_exam(
    cid: UUID,
    body: ExamInput,
    request: Request,
    uid=Depends(current_user),
    conn=Depends(connection, scope="function"),
):
    course(conn, cid, write=True)

    def create():
        conn.execute(text("SELECT auth.lock_course(:id)"), dict(id=cid))
        c = one(conn, "SELECT * FROM app.courses WHERE id=:id", id=cid)
        gid, eid = uuid4(), uuid4()
        conn.execute(
            text("""INSERT INTO app.generation_runs(id,course_id,user_id,kind,provider,model_name,model_revision,prompt_version,
          parameters,corpus_revision,graph_revision,request_hash) VALUES(:id,:cid,auth.uid(),'exam','ollama',:model,:revision,'exam_v1',
          CAST(:params AS jsonb),:cr,:gr,:hash)"""),
            dict(
                id=gid,
                cid=cid,
                model=os.environ["MODEL_LLM"],
                revision=os.environ["MODEL_LLM_REVISION"],
                params=json_value(
                    dict(
                        questionCount=body.questionCount,
                        conceptIds=body.conceptIds,
                        questionKinds=body.questionKinds,
                    )
                ),
                cr=c["corpus_revision"],
                gr=c["graph_revision"],
                hash=sha256(json_value(body.model_dump()).encode()).digest(),
            ),
        )
        conn.execute(
            text("""INSERT INTO app.exams(id,course_id,user_id,generation_id,title,kind,duration_seconds)
          VALUES(:id,:cid,auth.uid(),:gid,:title,:kind,:duration)"""),
            dict(
                id=eid,
                cid=cid,
                gid=gid,
                title=body.title,
                kind=body.kind,
                duration=body.durationSeconds,
            ),
        )
        enqueue(conn, "generate_exam", eid, cid, uid, 115)
        return {"id": str(eid), "status": "generating"}

    result = idempotent(conn, request, body.model_dump(), create)
    exam(conn, cid, UUID(result["id"]))
    return {"data": result}


@router.post("/courses/{cid}/exams/{eid}/attempts", status_code=201)
def start(
    cid: UUID,
    eid: UUID,
    request: Request,
    uid=Depends(current_user),
    conn=Depends(connection, scope="function"),
):
    e = exam(conn, cid, eid)
    if e["status"] != "ready":
        raise ApiError("EXAM_NOT_READY")

    def create():
        conn.execute(text("SELECT auth.lock_course(:id)"), dict(id=cid))
        locked = one(conn, "SELECT status FROM app.exams WHERE id=:id FOR UPDATE", id=eid)
        if not locked or locked["status"] != "ready":
            raise ApiError("EXAM_NOT_READY")
        existing = one(
            conn,
            "SELECT id FROM app.exam_attempts WHERE exam_id=:id AND status='in_progress'",
            id=eid,
        )
        if existing:
            return {"id": str(existing["id"])}
        n = one(
            conn,
            "SELECT coalesce(max(attempt_no),0)+1 AS n FROM app.exam_attempts WHERE exam_id=:id",
            id=eid,
        )["n"]
        aid = uuid4()
        conn.execute(
            text("""INSERT INTO app.exam_attempts(id,course_id,user_id,exam_id,attempt_no,deadline_at)
          VALUES(:id,:cid,auth.uid(),:eid,:n,clock_timestamp()+make_interval(secs=>:duration))"""),
            dict(id=aid, cid=cid, eid=eid, n=n, duration=e["duration_seconds"]),
        )
        return {"id": str(aid)}

    resource = idempotent(conn, request, {}, create)
    return get_attempt(UUID(resource["id"]), conn)


@router.get("/attempts/{aid}")
def get_attempt(aid: UUID, conn=Depends(connection, scope="function")):
    a = attempt(conn, aid)
    if a["status"] == "invalidated":
        return {
            "data": {
                **a,
                "score_percent": None,
                "serverNow": now(),
                "questions": [],
                "reason": "SOURCE_REMOVED",
            }
        }
    questions = rows(
        conn,
        """SELECT q.id,q.position,q.kind,q.prompt,q.max_points,c.label topic FROM app.questions q
      JOIN app.concepts c ON c.id=q.concept_id AND c.course_id=q.course_id WHERE q.exam_id=:id ORDER BY q.position""",
        id=a["exam_id"],
    )
    public = []
    keys = (
        {k["question_id"]: k for k in rows(conn, "SELECT * FROM auth.review_keys(:id)", id=aid)}
        if a["status"] in ("graded", "needs_review")
        else {}
    )
    for q in questions:
        options = rows(
            conn,
            "SELECT id,text FROM app.question_options WHERE question_id=:id ORDER BY position",
            id=q["id"],
        )
        answer = one(
            conn,
            "SELECT id,text_answer,flagged,row_version FROM app.answers WHERE attempt_id=:aid AND question_id=:qid",
            aid=aid,
            qid=q["id"],
        )
        selections = (
            rows(
                conn,
                "SELECT option_id FROM app.answer_selections WHERE answer_id=:id",
                id=answer["id"],
            )
            if answer
            else []
        )
        grade = (
            one(
                conn,
                "SELECT points,status,feedback,method,grader_version FROM app.grades WHERE answer_id=:id ORDER BY revision DESC LIMIT 1",
                id=answer["id"],
            )
            if answer and keys
            else None
        )
        public.append(
            {
                **q,
                "options": options,
                "answer": {**answer, "optionIds": [r["option_id"] for r in selections]}
                if answer
                else None,
                "grade": grade,
                "usedDocuments": rows(
                    conn,
                    '''SELECT DISTINCT d.id,v.original_name AS name,
                  '/api/v1/courses/'||d.course_id||'/materials/'||d.id||'/versions/'||v.id||'/content' AS "contentUrl"
                  FROM app.question_sources s JOIN app.parent_chunks p ON p.id=s.parent_id AND p.course_id=s.course_id
                  JOIN app.processing_runs r ON r.id=p.run_id JOIN app.document_versions v ON v.id=r.version_id
                  JOIN app.documents d ON d.id=v.document_id WHERE s.question_id=:id AND d.status='active'
                  ORDER BY d.id,v.original_name,"contentUrl"''',
                    id=q["id"],
                )
                if keys
                else [],
                **({"review": keys[q["id"]]} if q["id"] in keys else {}),
            }
        )
    for question in public:
        question["usesDocuments"] = bool(question["usedDocuments"])
    return {"data": {**a, "serverNow": now(), "questions": public}}


@router.put("/attempts/{aid}/answers/{qid}")
def save_answer(
    aid: UUID,
    qid: UUID,
    body: AnswerInput,
    request: Request,
    conn=Depends(connection, scope="function"),
):
    visible = attempt(conn, aid)
    conn.execute(text("SELECT auth.lock_course(:id)"), dict(id=visible["course_id"]))
    a = one(
        conn,
        "SELECT * FROM app.exam_attempts WHERE id=:id AND user_id=auth.uid() FOR UPDATE",
        id=aid,
    )
    if a["status"] != "in_progress" or a["deadline_at"] <= now():
        raise ApiError("ATTEMPT_CLOSED")
    q = one(
        conn, "SELECT * FROM app.questions WHERE id=:id AND exam_id=:eid", id=qid, eid=a["exam_id"]
    )
    if not q:
        raise ApiError("NOT_FOUND", 404)
    if (
        q["kind"] == "development"
        and body.optionIds
        or q["kind"] != "development"
        and body.textAnswer
    ):
        raise ApiError("INVALID_ANSWER", 422)
    if q["kind"] in ("alternatives", "true_false") and len(body.optionIds) > 1:
        raise ApiError("INVALID_ANSWER", 422)
    if len(set(body.optionIds)) != len(body.optionIds):
        raise ApiError("INVALID_ANSWER", 422)
    if body.optionIds:
        valid = {
            r["id"]
            for r in rows(conn, "SELECT id FROM app.question_options WHERE question_id=:id", id=qid)
        }
        if not set(body.optionIds) <= valid:
            raise ApiError("INVALID_ANSWER", 422)

    def save():
        old = one(
            conn,
            "SELECT * FROM app.answers WHERE attempt_id=:aid AND question_id=:qid",
            aid=aid,
            qid=qid,
        )
        if old:
            match(request, old)
            answer_id = old["id"]
            conn.execute(
                text("UPDATE app.answers SET text_answer=:answer,flagged=:flag WHERE id=:id"),
                dict(id=answer_id, answer=body.textAnswer, flag=body.flagged),
            )
        else:
            if request.headers.get("if-match") not in ("0", '"0"'):
                raise ApiError("VERSION_CONFLICT")
            answer_id = uuid4()
            conn.execute(
                text("""INSERT INTO app.answers(id,course_id,user_id,exam_id,attempt_id,question_id,text_answer,flagged)
              VALUES(:id,:cid,auth.uid(),:eid,:aid,:qid,:answer,:flag)"""),
                dict(
                    id=answer_id,
                    cid=a["course_id"],
                    eid=a["exam_id"],
                    aid=aid,
                    qid=qid,
                    answer=body.textAnswer,
                    flag=body.flagged,
                ),
            )
        conn.execute(
            text("DELETE FROM app.answer_selections WHERE answer_id=:id"), dict(id=answer_id)
        )
        for oid in set(body.optionIds):
            conn.execute(
                text("""INSERT INTO app.answer_selections(course_id,user_id,exam_id,question_id,answer_id,option_id)
              VALUES(:cid,auth.uid(),:eid,:qid,:aid,:oid)"""),
                dict(cid=a["course_id"], eid=a["exam_id"], qid=qid, aid=answer_id, oid=oid),
            )
        return {"id": str(answer_id), "rowVersion": old["row_version"] + 1 if old else 1}

    return {"data": idempotent(conn, request, body.model_dump(), save)}


@router.post("/attempts/{aid}/submit", status_code=202)
def submit(aid: UUID, request: Request, conn=Depends(connection, scope="function")):
    attempt(conn, aid)

    def close():
        one(conn, "SELECT auth.submit_attempt(:id) AS id", id=aid)
        return {"id": str(aid)}

    idempotent(conn, request, {}, close)
    return {"data": {"id": aid, "status": attempt(conn, aid)["status"]}}


class ManualReviewInput(Input):
    points: Decimal = Field(ge=0, max_digits=8, decimal_places=5)
    feedback: str = Field(default="", max_length=10000)


@router.post("/attempts/{aid}/answers/{answerid}/review", status_code=201)
def manual_review(
    aid: UUID,
    answerid: UUID,
    body: ManualReviewInput,
    request: Request,
    conn=Depends(connection, scope="function"),
):
    visible = attempt(conn, aid)
    if visible["status"] not in ("graded", "needs_review"):
        raise ApiError("ATTEMPT_CLOSED")

    def revise():
        gid = uuid4()
        one(
            conn,
            "SELECT auth.manual_review(:aid,:answer,:points,:feedback,:gid,:rid) AS id",
            aid=aid,
            answer=answerid,
            points=body.points,
            feedback=body.feedback,
            gid=gid,
            rid=uuid4(),
        )
        return {"id": str(gid), "attemptId": str(aid), "method": "manual_self"}

    return {"data": idempotent(conn, request, body.model_dump(), revise)}


@router.post("/attempts/{aid}/regrade", status_code=202)
def request_regrade(aid: UUID, request: Request, conn=Depends(connection, scope="function")):
    attempt(conn, aid)

    def queue():
        jid = uuid4()
        one(
            conn, "SELECT auth.request_regrade(:aid,:jid,:rid) AS id", aid=aid, jid=jid, rid=uuid4()
        )
        return {"id": str(jid), "attemptId": str(aid), "status": "queued"}

    result = idempotent(conn, request, {}, queue)
    current = one(conn, "SELECT state FROM app.jobs WHERE id=:id", id=UUID(result["id"]))
    return {"data": {**result, "status": current["state"] if current else "unavailable"}}
