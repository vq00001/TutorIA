import os
import smtplib
from secrets import token_urlsafe
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.api.common import ApiError, connection, current_user, match
from backend.db.connection import one, transaction
from backend.security.password_dummy import DUMMY_HASH
from backend.security.rate_limits import enforce
from backend.security.tokens import digest, passwords, token, verify_password
from backend.services.mail import send_token

router = APIRouter()


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def trim_public_text(cls, value, info):
        if isinstance(value, str) and info.field_name not in {
            "password",
            "currentPassword",
            "newPassword",
            "textAnswer",
            "content",
        }:
            return value.strip()
        return value


class Register(Input):
    email: EmailStr
    nickname: str = Field(pattern=r"^[a-zA-Z0-9_]{3,50}$")
    displayName: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=1024)
    acceptTerms: bool
    acknowledgeAI: bool


class Login(Input):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class TokenInput(Input):
    token: str = Field(min_length=20, max_length=128)


class EmailInput(Input):
    email: EmailStr


class Reset(TokenInput):
    password: str = Field(min_length=12, max_length=1024)


class EmailChange(EmailInput):
    currentPassword: str = Field(min_length=1, max_length=1024)


class PasswordChange(Input):
    currentPassword: str = Field(min_length=1, max_length=1024)
    newPassword: str = Field(min_length=12, max_length=1024)


class Profile(Input):
    displayName: str = Field(min_length=1, max_length=100)
    timezone: str = Field(max_length=64)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


def issue_account_token(conn, uid, purpose):
    raw, hashed = token()
    conn.execute(
        text("""UPDATE app.account_tokens SET consumed_at=now()
       WHERE user_id=:uid AND purpose=:purpose AND consumed_at IS NULL"""),
        dict(uid=uid, purpose=purpose),
    )
    conn.execute(
        text("""INSERT INTO app.account_tokens(id,user_id,purpose,token_hash,expires_at)
       VALUES(:id,:uid,:purpose,:hash,now()+make_interval(secs=>:seconds))"""),
        dict(
            id=uuid4(),
            uid=uid,
            purpose=purpose,
            hash=hashed,
            seconds=86400 if purpose == "verify_email" else 1800,
        ),
    )
    return raw


def audit(conn, action, entity_id):
    conn.execute(
        text("""INSERT INTO app.audit_events(id,actor_id,action,entity_type,entity_id,request_id,result)
      VALUES(:id,auth.uid(),:action,'account',:entity,:request,'success')"""),
        dict(id=uuid4(), action=action, entity=entity_id, request=uuid4()),
    )


def deliver(email, purpose, raw, *, report_failure=True):
    try:
        send_token(email, purpose, raw)
    except (OSError, smtplib.SMTPException) as exc:
        if report_failure:
            raise ApiError("MAIL_UNAVAILABLE", 503) from exc
        return False
    return True


@router.get("/auth/csrf")
def csrf_token(response: Response):
    raw = token_urlsafe(32)
    response.set_cookie(
        "tutoria_csrf",
        raw,
        secure=os.environ["APP_ORIGIN"].startswith("https://"),
        samesite="lax",
        max_age=604800,
    )
    return {"data": {"csrfToken": raw}}


@router.post("/auth/register", status_code=201)
def register(body: Register, request: Request):
    enforce(request, "register", str(body.email))
    if not body.acceptTerms or not body.acknowledgeAI:
        raise ApiError("POLICY_ACCEPTANCE_REQUIRED", 422)
    uid = uuid4()
    encoded = passwords.hash(body.password)
    try:
        with transaction() as conn:
            conn.execute(
                text("SELECT auth.register(:id,:nick,:name,:email,:hash)"),
                dict(
                    id=uid,
                    nick=body.nickname,
                    name=body.displayName,
                    email=str(body.email).lower(),
                    hash=encoded,
                ),
            )
            conn.execute(text("SELECT set_config('app.user_id',:uid,true)"), dict(uid=str(uid)))
            for purpose in ("service_terms", "ai_notice"):
                conn.execute(
                    text("""INSERT INTO app.policy_acceptances(id,user_id,purpose,policy_version,action)
                  VALUES(:id,:uid,:purpose,'1.1','accepted')"""),
                    dict(id=uuid4(), uid=uid, purpose=purpose),
                )
            raw = issue_account_token(conn, uid, "verify_email")
            audit(conn, "account.register", uid)
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23505":
            raise
        return {"data": {"status": "pending"}}
    deliver(str(body.email), "verify_email", raw, report_failure=False)
    return {"data": {"status": "pending"}}


@router.post("/auth/login")
def login(body: Login, response: Response, request: Request):
    enforce(request, "login", str(body.email))
    with transaction() as conn:
        user = one(conn, "SELECT * FROM auth.login_record(:email)", email=str(body.email).lower())
    # Same expensive hash path for unknown accounts.
    encoded = user["password_hash"] if user else DUMMY_HASH
    valid = verify_password(encoded, body.password)
    if not user or not valid or user["status"] not in ("active", "pending"):
        raise ApiError("INVALID_CREDENTIALS", 401)
    raw, hashed = token()
    with transaction(user["id"]) as conn:
        conn.execute(
            text("""INSERT INTO app.auth_sessions(id,user_id,token_hash,last_seen_at,expires_at)
          VALUES(:id,:uid,:hash,now(),now()+interval '7 days')"""),
            dict(id=uuid4(), uid=user["id"], hash=hashed),
        )
    response.set_cookie(
        "tutoria_session",
        raw,
        httponly=True,
        secure=os.environ["APP_ORIGIN"].startswith("https://"),
        samesite="lax",
        max_age=604800,
    )
    return {"data": {"id": str(user["id"]), "status": user["status"]}}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, conn=Depends(connection, scope="function")):
    conn.execute(
        text("UPDATE app.auth_sessions SET revoked_at=now() WHERE token_hash=:hash"),
        dict(hash=digest(request.cookies.get("tutoria_session", ""))),
    )
    response.delete_cookie("tutoria_session")


@router.post("/auth/verify-email")
def verify_email(body: TokenInput, request: Request):
    enforce(request, "verify-token")
    with transaction() as conn:
        result = one(
            conn, "SELECT auth.consume_token(:hash,'verify_email') AS id", hash=digest(body.token)
        )
        if not result["id"]:
            raise ApiError("INVALID_TOKEN", 422)
        conn.execute(text("SELECT set_config('app.user_id',:id,true)"), dict(id=str(result["id"])))
        change = one(
            conn,
            """SELECT e.new_email FROM app.account_email_changes e JOIN app.account_tokens t ON t.id=e.token_id
          WHERE e.user_id=:id AND t.token_hash=:hash FOR UPDATE OF e""",
            id=result["id"],
            hash=digest(body.token),
        )
        if change:
            conn.execute(
                text("UPDATE app.users SET email=:email,email_verified_at=now() WHERE id=:id"),
                dict(id=result["id"], email=change["new_email"]),
            )
            conn.execute(
                text("UPDATE app.auth_sessions SET revoked_at=now() WHERE user_id=:id"),
                dict(id=result["id"]),
            )
            conn.execute(
                text("DELETE FROM app.account_email_changes WHERE user_id=:id"),
                dict(id=result["id"]),
            )
            audit(conn, "account.email_changed", result["id"])
        conn.execute(
            text(
                "UPDATE app.users SET status='active',email_verified_at=now() WHERE id=:id AND status='pending'"
            ),
            dict(id=result["id"]),
        )
    return {"data": {"status": "verified"}}


@router.post("/auth/forgot-password", status_code=202)
def forgot_password(body: EmailInput, request: Request):
    enforce(request, "recover", str(body.email))
    with transaction() as conn:
        user = one(conn, "SELECT auth.token_account(:email) AS id", email=str(body.email).lower())
    if user["id"]:
        with transaction(user["id"]) as conn:
            raw = issue_account_token(conn, user["id"], "reset_password")
        deliver(str(body.email), "reset_password", raw, report_failure=False)
    return {"data": {"status": "accepted"}}


@router.post("/auth/reset-password")
def reset_password(body: Reset, request: Request):
    enforce(request, "reset-token")
    with transaction() as conn:
        result = one(
            conn, "SELECT auth.consume_token(:hash,'reset_password') AS id", hash=digest(body.token)
        )
        if not result["id"]:
            raise ApiError("INVALID_TOKEN", 422)
        conn.execute(text("SELECT set_config('app.user_id',:id,true)"), dict(id=str(result["id"])))
        conn.execute(
            text("UPDATE app.users SET password_hash=:hash WHERE id=:id"),
            dict(hash=passwords.hash(body.password), id=result["id"]),
        )
        conn.execute(
            text("UPDATE app.auth_sessions SET revoked_at=now() WHERE user_id=:id"),
            dict(id=result["id"]),
        )
        audit(conn, "account.password_reset", result["id"])
    return {"data": {"status": "changed"}}


@router.post("/auth/resend-verification", status_code=202)
def resend(body: EmailInput, request: Request):
    enforce(request, "verify", str(body.email))
    with transaction() as conn:
        user = one(conn, "SELECT * FROM auth.login_record(:email)", email=str(body.email).lower())
    if user and user["status"] == "pending":
        with transaction(user["id"]) as conn:
            raw = issue_account_token(conn, user["id"], "verify_email")
        deliver(str(body.email), "verify_email", raw, report_failure=False)
    return {"data": {"status": "accepted"}}


@router.post("/student/me/email", status_code=202)
def change_email(body: EmailChange, request: Request, uid=Depends(current_user)):
    enforce(request, "email-change", str(body.email))
    with transaction(uid) as conn:
        user = one(
            conn, "SELECT id,password_hash,status FROM app.users WHERE id=auth.uid() FOR UPDATE"
        )
        if (
            not user
            or user["status"] != "active"
            or not verify_password(user["password_hash"], body.currentPassword)
        ):
            raise ApiError("INVALID_CREDENTIALS", 401)
        raw = issue_account_token(conn, user["id"], "verify_email")
        token_row = one(
            conn, "SELECT id FROM app.account_tokens WHERE token_hash=:hash", hash=digest(raw)
        )
        conn.execute(
            text("""INSERT INTO app.account_email_changes(user_id,new_email,token_id) VALUES(:uid,:email,:token)
          ON CONFLICT(user_id) DO UPDATE SET new_email=excluded.new_email,token_id=excluded.token_id,created_at=now()"""),
            dict(uid=user["id"], email=str(body.email).lower(), token=token_row["id"]),
        )
    deliver(str(body.email), "verify_email", raw)
    return {"data": {"status": "verification_pending"}}


@router.post("/student/me/password")
def change_password(
    body: PasswordChange, request: Request, conn=Depends(connection, scope="function")
):
    enforce(request, "password-change")
    user = one(conn, "SELECT id,password_hash FROM app.users WHERE id=auth.uid() FOR UPDATE")
    if not user or not verify_password(user["password_hash"], body.currentPassword):
        raise ApiError("INVALID_CREDENTIALS", 401)
    conn.execute(
        text("UPDATE app.users SET password_hash=:hash WHERE id=:id"),
        dict(id=user["id"], hash=passwords.hash(body.newPassword)),
    )
    conn.execute(
        text("UPDATE app.auth_sessions SET revoked_at=now() WHERE user_id=:id"), dict(id=user["id"])
    )
    audit(conn, "account.password_changed", user["id"])
    return {"data": {"status": "changed"}}


@router.get("/student/me")
def me(conn=Depends(connection, scope="function")):
    user = one(
        conn,
        "SELECT id,nickname,display_name,email,status,timezone,row_version FROM app.users WHERE id=auth.uid()",
    )
    return {
        "data": {
            "id": user["id"],
            "nickname": user["nickname"],
            "displayName": user["display_name"],
            "email": user["email"],
            "status": user["status"],
            "timezone": user["timezone"],
            "rowVersion": user["row_version"],
            "role": "Estudiante",
        }
    }


@router.patch("/student/me")
def edit_profile(body: Profile, request: Request, conn=Depends(connection, scope="function")):
    user = one(conn, "SELECT row_version FROM app.users WHERE id=auth.uid() FOR UPDATE")
    match(request, user)
    conn.execute(
        text("UPDATE app.users SET display_name=:name,timezone=:zone WHERE id=auth.uid()"),
        dict(name=body.displayName, zone=body.timezone),
    )
    return me(conn)
