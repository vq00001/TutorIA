from contextlib import nullcontext

from fastapi.testclient import TestClient
from sqlalchemy import update

from backend.api import app as http_api
from backend.api.routes import dependencies
from backend.db.sessions import issue_session
from db.courses import create_course
from db.users import users

from .test_courses import _as_user, _user


def test_profile_membership_and_invitation_workflow(postgres, monkeypatch):
    owner, invitee = _user(postgres), _user(postgres)
    email = f"{invitee.hex}@example.com"
    postgres.execute(update(users).where(users.c.id == invitee).values(email=email))
    tokens = {user: issue_session(postgres, user) for user in (owner, invitee)}
    _as_user(postgres, owner)
    course = create_course(postgres, name="Comunidad")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)
    own = {"Authorization": f"Bearer {tokens[owner]}"}
    peer = {"Authorization": f"Bearer {tokens[invitee]}"}
    base = f"/api/v1/courses/{course}"
    profile = "/api/v1/users/me"

    changed = client.patch(profile, json={"displayName": " Ana "}, headers=peer)
    assert changed.status_code == 200
    assert changed.json()["data"]["displayName"] == "Ana"
    assert client.get(profile, headers=peer).json() == changed.json()
    assert (
        client.patch(profile, json={"displayName": "Ana", "email": email}, headers=peer).status_code
        == 422
    )

    assert client.post(base + "/leave", headers=own).status_code == 409
    invite = base + "/invitations"
    known = client.post(invite, json={"email": email}, headers=own)
    unknown = client.post(invite, json={"email": "absent@example.com"}, headers=own)
    assert known.status_code == unknown.status_code == 202
    assert known.content == unknown.content == b""
    assert client.get("/api/v1/invitations", headers=own).json() == {"data": []}
    (item,) = client.get("/api/v1/invitations", headers=peer).json()["data"]
    assert item["courseId"] == str(course)
    decision = f"/api/v1/invitations/{item['id']}"
    assert client.patch(decision, json={"decision": "accept"}, headers=own).status_code == 404
    accepted = client.patch(decision, json={"decision": "accept"}, headers=peer)
    assert accepted.status_code == 200
    assert accepted.json()["data"] == {"courseId": str(course), "status": "accepted"}
    assert (
        client.patch(decision, json={"decision": "accept"}, headers=peer).json() == accepted.json()
    )

    members = client.get(base + "/members", headers=own).json()["data"]
    member = next(row for row in members if row["userId"] == str(invitee))
    member_path = f"{base}/members/{invitee}"
    payload = {"role": "coordinator", "rowVersion": member["rowVersion"]}
    assert client.patch(member_path, json=payload, headers=peer).status_code == 403
    assert client.patch(member_path, json=payload, headers=own).status_code == 204
    assert client.patch(member_path, json=payload, headers=own).status_code == 409
    assert client.post(base + "/leave", headers=own).status_code == 204
    assert client.get(base + "/members", headers=own).status_code == 404
