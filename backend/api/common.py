import json
import os
from datetime import datetime, timezone
from hashlib import sha256
from secrets import compare_digest
from uuid import uuid4

from fastapi import Depends, Request
from sqlalchemy import text

from backend.db.connection import one, transaction
from backend.security.tokens import digest


class ApiError(Exception):
    def __init__(self, code, status=409, message=None):
        self.code, self.status, self.message = code, status, message or code


def now():
    return datetime.now(timezone.utc)


def json_value(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def csrf(request: Request):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.headers.get("origin") != os.environ["APP_ORIGIN"].rstrip("/"):
        raise ApiError("INVALID_ORIGIN", 403)
    cookie = request.cookies.get("tutoria_csrf", "")
    header = request.headers.get("x-csrf-token", "")
    if not cookie or not compare_digest(cookie, header):
        raise ApiError("INVALID_CSRF", 403)


def current_user(request: Request):
    raw = request.cookies.get("tutoria_session")
    if not raw:
        raise ApiError("AUTHENTICATION_REQUIRED", 401)
    with transaction() as conn:
        result = one(conn, "SELECT auth.session_owner(:hash) AS id", hash=digest(raw))
    if not result or not result["id"]:
        raise ApiError("AUTHENTICATION_REQUIRED", 401)
    return result["id"]


def connection(uid=Depends(current_user)):
    with transaction(uid) as conn:
        yield conn


def course(conn, cid, coordinator=False, write=False):
    result = one(
        conn,
        """SELECT c.*,m.role AS my_role FROM app.courses c
        JOIN app.memberships m ON m.course_id=c.id AND m.user_id=auth.uid()
        WHERE c.id=:cid AND m.status='active' """,
        cid=cid,
    )
    if not result:
        raise ApiError("NOT_FOUND", 404)
    if coordinator and result["my_role"] != "coordinator":
        raise ApiError("FORBIDDEN", 403)
    if write and result["status"] != "active":
        raise ApiError("COURSE_ARCHIVED")
    return result


def match(request, row):
    value = request.headers.get("if-match", "").strip('"')
    if not value:
        raise ApiError("IF_MATCH_REQUIRED", 428)
    if value != str(row["row_version"]):
        raise ApiError("VERSION_CONFLICT")


def idempotent(conn, request, payload, operation):
    key = request.headers.get("idempotency-key", "")
    if not 1 <= len(key) <= 128:
        raise ApiError("IDEMPOTENCY_KEY_REQUIRED", 422)
    scope = request.method + " " + request.url.path
    hashed = sha256(json_value(payload).encode()).digest()
    conn.execute(
        text("""INSERT INTO app.idempotency_requests
        (id,user_id,scope,key,request_hash,expires_at) VALUES(:id,auth.uid(),:scope,:key,:hash,now()+interval '24 hours')
        ON CONFLICT(user_id,scope,key) DO NOTHING"""),
        dict(id=uuid4(), scope=scope, key=key, hash=hashed),
    )
    entry = one(
        conn,
        """SELECT * FROM app.idempotency_requests WHERE user_id=auth.uid()
                        AND scope=:scope AND key=:key FOR UPDATE""",
        scope=scope,
        key=key,
    )
    if bytes(entry["request_hash"]) != hashed:
        raise ApiError("IDEMPOTENCY_CONFLICT")
    if entry["state"] == "completed":
        return entry["response_body"]
    result = operation()
    conn.execute(
        text("""UPDATE app.idempotency_requests SET state='completed',response_status=200,
        response_body=CAST(:body AS jsonb) WHERE id=:id"""),
        dict(id=entry["id"], body=json_value(result)),
    )
    return result


def enqueue(conn, kind, target, cid, uid, seconds=None):
    jid = uuid4()
    conn.execute(
        text("""INSERT INTO app.jobs(id,course_id,requested_by,kind,target_id,idempotency_key,deadline_at)
        VALUES(:id,:cid,:uid,:kind,:target,:key,CASE WHEN CAST(:secs AS double precision) IS NULL THEN NULL ELSE now()+make_interval(secs=>CAST(:secs AS double precision)) END)"""),
        dict(id=jid, cid=cid, uid=uid, kind=kind, target=target, key=str(target), secs=seconds),
    )
    return jid
