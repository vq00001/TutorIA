from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from backend.api.auth import Input
from backend.api.common import ApiError, connection, course, current_user, idempotent
from backend.db.connection import one

router = APIRouter()


class DeleteConfirmation(Input):
    confirmation: str = Field(min_length=1, max_length=100)


def _withdraw(conn, request, scope, subject, confirmation):
    def perform():
        result = one(
            conn,
            "SELECT auth.request_scope_delete(:scope,:subject,:id,:confirmation) AS id",
            scope=scope,
            subject=subject,
            id=uuid4(),
            confirmation=confirmation,
        )
        return {"deletionRequestId": str(result["id"]), "status": "pending"}

    result = idempotent(conn, request, {"confirmation": confirmation}, perform)
    # A replay only exposes an opaque request id owned by this caller.
    if not one(
        conn,
        "SELECT id FROM app.deletion_requests WHERE id=:id",
        id=UUID(result["deletionRequestId"]),
    ):
        raise ApiError("NOT_FOUND", 404)
    return {"data": result}


@router.delete("/courses/{cid}", status_code=202)
def delete_course(
    cid: UUID,
    body: DeleteConfirmation,
    request: Request,
    conn=Depends(connection, scope="function"),
):
    course(conn, cid, coordinator=True)
    return _withdraw(conn, request, "course", cid, body.confirmation)


@router.delete("/student/me", status_code=202)
def delete_account(
    body: DeleteConfirmation,
    request: Request,
    uid=Depends(current_user),
    conn=Depends(connection, scope="function"),
):
    return _withdraw(conn, request, "account", uid, body.confirmation)
