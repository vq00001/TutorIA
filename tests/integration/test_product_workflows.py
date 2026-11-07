from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.api.artifacts import ArtifactInput, artifacts, create_artifact
from backend.api.chat import (
    ConversationInput,
    MessageInput,
    create_conversation,
    delete_conversation,
    messages,
    send,
)
from backend.api.common import ApiError
from backend.api.community import (
    InvitationDecision,
    InviteMember,
    activity,
    invitations,
    invite_member,
    mark_read,
    notifications,
    respond,
)
from backend.api.courses import leave, members
from backend.db.connection import one, transaction
from backend.workers.chat import generate_chat
from backend.workers.main import Lease
from tests.integration.test_database import seed
from tests.integration.test_grading_integrity import request
from tests.integration.test_materials import configure, prepare, publish


def people(database, monkeypatch):
    admin, url = database
    coordinator, cid = seed(admin)
    student, _ = seed(admin)
    outsider, _ = seed(admin)
    configure(monkeypatch, url)
    return admin, url, coordinator, cid, student, outsider


def invitation(admin, coordinator, cid, recipient):
    with admin.begin() as conn:
        email = one(
            conn,
            "UPDATE app.users SET email=:email WHERE id=:id RETURNING email",
            id=recipient,
            email=recipient.hex + "@example.com",
        )["email"]
    with transaction(coordinator) as conn:
        invite_member(
            cid,
            InviteMember(email=email),
            request("POST", f"/courses/{cid}/invitations"),
            conn,
        )
    with transaction(recipient) as conn:
        return invitations(conn)["data"][0]["id"]


def test_direct_invitation_is_private_can_be_declined_then_accepted_and_leaving_hides_course(
    database, monkeypatch
):
    admin, url, coord, cid, student, outsider = people(database, monkeypatch)
    iid = invitation(admin, coord, cid, student)
    with transaction(outsider) as conn:
        assert invitations(conn)["data"] == []
        with pytest.raises(ApiError):
            respond(
                iid,
                InvitationDecision(accept=True),
                request("POST", f"/invitations/{iid}/respond"),
                conn,
            )
    with transaction(student) as conn:
        respond(
            iid,
            InvitationDecision(accept=False),
            request("POST", f"/invitations/{iid}/respond"),
            conn,
        )
        assert not one(conn, "SELECT id FROM app.courses WHERE id=:id", id=cid)
    iid = invitation(admin, coord, cid, student)
    with transaction(student) as conn:
        respond(
            iid,
            InvitationDecision(accept=True),
            request("POST", f"/invitations/{iid}/respond"),
            conn,
        )
        public = members(cid, conn)["data"]
        assert {m["id"] for m in public} == {coord, student}
        assert all("email" not in m and "password_hash" not in m for m in public)
        with pytest.raises(ApiError) as denied:
            invite_member(
                cid,
                InviteMember(email="absent@example.com"),
                request("POST", f"/courses/{cid}/invitations"),
                conn,
            )
        assert denied.value.status == 403
        assert one(conn, "SELECT id FROM app.courses WHERE id=:id", id=cid)
        leave(cid, conn)
        assert not one(conn, "SELECT id FROM app.courses WHERE id=:id", id=cid)


def test_last_coordinator_cannot_leave_and_student_cannot_invite(database, monkeypatch):
    admin, url, coord, cid, student, _ = people(database, monkeypatch)
    with pytest.raises(IntegrityError, match="LAST_COORDINATOR"):
        with transaction(coord) as conn:
            leave(cid, conn)
    with transaction(student) as conn:
        with pytest.raises(ApiError):
            invite_member(
                cid,
                InviteMember(email="absent@example.com"),
                request("POST", f"/courses/{cid}/invitations"),
                conn,
            )


def test_event_feeds_are_owned_read_state_is_private_and_payload_has_no_content(
    database, monkeypatch
):
    admin, url, coord, cid, student, outsider = people(database, monkeypatch)
    invitation(admin, coord, cid, student)
    with transaction(student) as conn:
        notice = next(
            n
            for n in notifications(conn=conn)["data"]
            if n["action"] == "invitation.received"
        )
        assert notice["readAt"] is None
        assert mark_read(notice["id"], conn)["data"]["readAt"]
        assert activity(conn=conn)["data"]
        assert "email" not in str(notice) and "password" not in str(notice)
    with transaction(outsider) as conn:
        with pytest.raises(ApiError):
            mark_read(notice["id"], conn)
        assert all(n["id"] != notice["id"] for n in notifications(conn=conn)["data"])


def test_delete_conversation_removes_messages_and_cancels_inflight_lease(
    database, monkeypatch
):
    admin, url, coord, cid, student, _ = people(database, monkeypatch)
    monkeypatch.setenv("MODEL_LLM", "synthetic")
    monkeypatch.setenv("MODEL_LLM_REVISION", "fixture-v1")
    with transaction(coord) as conn:
        tid = UUID(
            create_conversation(
                cid,
                ConversationInput(title="Private prompt"),
                request("POST", f"/courses/{cid}/conversations"),
                conn,
            )["data"]["id"]
        )
        queued = send(
            cid,
            tid,
            MessageInput(content="Private text"),
            request("POST", f"/courses/{cid}/conversations/{tid}/messages"),
            coord,
            conn,
        )["data"]
    with transaction(student) as conn:
        with pytest.raises(ApiError):
            delete_conversation(cid, tid, conn)
    with transaction(coord) as conn:
        delete_conversation(cid, tid, conn)
        assert not one(
            conn, "SELECT id FROM app.messages WHERE conversation_id=:id", id=tid
        )
        assert (
            one(
                conn,
                "SELECT status FROM app.generation_runs WHERE id=:id",
                id=UUID(queued["generationId"]),
            )["status"]
            == "cancelled"
        )
    with admin.connect() as conn:
        assert (
            one(
                conn,
                "SELECT state FROM app.jobs WHERE target_id=:id",
                id=UUID(queued["generationId"]),
            )["state"]
            == "cancelled"
        )


def test_artifact_publication_has_sources_is_private_and_withdrawal_redacts_it(
    database, monkeypatch
):
    monkeypatch.setenv("OLLAMA_URL", "http://synthetic.invalid")
    admin, url = database
    uid, cid, _ = prepare(admin, url, monkeypatch)
    accepted, _ = publish(admin, url, monkeypatch, uid, cid)
    monkeypatch.setenv("MODEL_LLM", "synthetic")
    monkeypatch.setenv("MODEL_LLM_REVISION", "fixture-v1")

    # Reuse the synthetic model protocol rather than contacting a live provider.
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "message": {
                    "content": '{"answer":"Resumen sintético","citations":["'
                    + self.source
                    + '"]}'
                }
            }

    def model(*args, **kwargs):
        import json

        assert args == ("http://synthetic.invalid/api/chat",)
        payload = json.loads(kwargs["json"]["messages"][1]["content"])
        response = Response()
        response.source = payload["sources"][0]["id"]
        return response

    monkeypatch.setattr("backend.workers.chat.requests.post", model)
    monkeypatch.setattr(
        "backend.workers.chat.embeddings",
        lambda space, texts, timeout: ["[1,0,0]" for _ in texts],
    )
    configure(monkeypatch, url)
    req = request("POST", f"/courses/{cid}/artifacts")
    with transaction(uid) as conn:
        created = create_artifact(
            cid, ArtifactInput(kind="summary", topic="derivada"), req, uid, conn
        )["data"]
        assert (
            create_artifact(
                cid, ArtifactInput(kind="summary", topic="derivada"), req, uid, conn
            )["data"]
            == created
        )
    with admin.begin() as conn:
        job = one(
            conn,
            "UPDATE app.jobs SET state='running',lease_token=gen_random_uuid(),lease_until=now()+interval '120 seconds' WHERE target_id=:id RETURNING *",
            id=UUID(created["generationId"]),
        )
    configure(monkeypatch, url.replace("tutoria_api:", "tutoria_worker:"))
    generate_chat(job, Lease(job))
    configure(monkeypatch, url)
    other, _ = seed(admin)
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.memberships(course_id,user_id,role) VALUES(:cid,:uid,'student')"
            ),
            dict(cid=cid, uid=other),
        )
    with transaction(other) as conn:
        assert artifacts(cid, conn)["data"] == []
    with transaction(uid) as conn:
        result = artifacts(cid, conn)["data"]
        assert result[0]["content"]["markdown"] == "Resumen sintético"
        assert result[0]["usedDocuments"]
        tid = UUID(created["conversationId"])
        reply = messages(cid, tid, conn=conn)["data"][-1]
        assert reply["usesDocuments"] and reply["usedDocuments"][0]["documentName"]
        conn.execute(
            text("SELECT auth.request_document_delete(:cid,:did,:rid)"),
            dict(cid=cid, did=UUID(accepted["materialId"]), rid=uuid4()),
        )
        assert artifacts(cid, conn)["data"] == []


def test_course_purge_cascades_new_event_and_invitation_rows(database, monkeypatch):
    admin, url, coord, cid, student, _ = people(database, monkeypatch)
    invitation(admin, coord, cid, student)
    with transaction(coord) as conn:
        rid = one(
            conn,
            "SELECT auth.request_scope_delete('course',:cid,:rid,'Synthetic course') AS id",
            cid=cid,
            rid=uuid4(),
        )["id"]
    with admin.begin() as conn:
        conn.execute(text("SELECT auth.purge_scope(:id)"), dict(id=rid))
        assert not one(
            conn, "SELECT id FROM app.direct_invites WHERE course_id=:cid", cid=cid
        )
        assert not one(
            conn, "SELECT id FROM app.user_events WHERE course_id=:cid", cid=cid
        )
        assert one(conn, "SELECT id FROM app.users WHERE id=:id", id=student)
