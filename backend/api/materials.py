from hashlib import sha256
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import text

from backend.api.common import (
    ApiError,
    connection,
    course,
    current_user,
    idempotent,
    match,
)
from backend.db.connection import one, rows
from backend.services.documents import MAX_FILE_BYTES, validate_file
from backend.services.processing import prepare_run

router = APIRouter()


def material(conn, cid, mid, write=False):
    c = course(conn, cid, write=write)
    row = one(
        conn,
        "SELECT * FROM app.documents WHERE course_id=:cid AND id=:id AND status='active'",
        cid=cid,
        id=mid,
    )
    if not row:
        raise ApiError("NOT_FOUND", 404)
    if (
        write
        and row["uploaded_by"] != one(conn, "SELECT auth.uid() AS id")["id"]
        and c["my_role"] != "coordinator"
    ):
        raise ApiError("FORBIDDEN", 403)
    return row


def material_dto(conn, row):
    version = one(
        conn,
        """SELECT v.*,r.id run_id,r.state run_state,r.error_code run_error
      FROM app.document_versions v JOIN app.processing_runs r ON r.version_id=v.id
      WHERE v.document_id=:id ORDER BY v.version_no DESC,r.created_at DESC LIMIT 1""",
        id=row["id"],
    )
    return dict(
        id=row["id"],
        courseId=row["course_id"],
        name=version["original_name"],
        size=version["size_bytes"],
        addedAt=row["created_at"],
        title=row["title"],
        rowVersion=row["row_version"],
        versionId=version["id"],
        runId=version["run_id"],
        status=version["run_state"],
        activeRunId=row["active_run_id"],
        errorCode=version["run_error"],
    )


@router.get("/courses/{cid}/materials")
def list_materials(
    cid: UUID,
    limit: int = 50,
    cursor: UUID | None = None,
    conn=Depends(connection, scope="function"),
):
    course(conn, cid)
    if not 1 <= limit <= 100:
        raise ApiError("INVALID_LIMIT", 422)
    result = rows(
        conn,
        """SELECT * FROM app.documents WHERE course_id=:cid AND status='active'
      AND (CAST(:cursor AS uuid) IS NULL OR id>CAST(:cursor AS uuid)) ORDER BY id LIMIT :limit""",
        cid=cid,
        cursor=str(cursor) if cursor else None,
        limit=limit + 1,
    )
    return {
        "data": [material_dto(conn, d) for d in result[:limit]],
        "meta": {"nextCursor": str(result[limit - 1]["id"]) if len(result) > limit else None},
    }


@router.get("/courses/{cid}/materials/{mid}")
def get_material(cid: UUID, mid: UUID, conn=Depends(connection, scope="function")):
    return {"data": material_dto(conn, material(conn, cid, mid))}


def accept_file(conn, cid, uid, name, data, mid=None):
    name, mime, content, hashed = validate_file(name, data)
    conn.execute(text("SELECT auth.lock_course(:id)"), dict(id=cid))
    duplicate = one(
        conn,
        "SELECT document_id FROM app.document_versions WHERE course_id=:cid AND sha256=:hash AND status<>'purged' LIMIT 1",
        cid=cid,
        hash=hashed,
    )
    if duplicate:
        raise ApiError("DUPLICATE_DOCUMENT", 409)
    used = one(
        conn,
        "SELECT coalesce(sum(size_bytes),0) AS n FROM app.document_versions WHERE course_id=:cid AND status<>'purged'",
        cid=cid,
    )["n"]
    daily = one(
        conn,
        "SELECT coalesce(sum(size_bytes),0) AS n FROM app.document_versions WHERE uploaded_by=:uid AND created_at>=now()-interval '24 hours'",
        uid=uid,
    )["n"]
    if used + len(data) > 2_000_000_000 or daily + len(data) > 1_000_000_000:
        raise ApiError("UPLOAD_QUOTA_EXCEEDED", 429)
    space = one(
        conn,
        "SELECT active_space_id,pending_space_id FROM app.course_embedding_config WHERE course_id=:cid",
        cid=cid,
    )
    if not space:
        raise ApiError("EMBEDDING_SPACE_NOT_CONFIGURED", 503)
    if space["pending_space_id"]:
        raise ApiError("EMBEDDING_MIGRATION_IN_PROGRESS", 503)
    if mid is None:
        mid = uuid4()
        conn.execute(
            text(
                "INSERT INTO app.documents(id,course_id,uploaded_by,title) VALUES(:id,:cid,:uid,:title)"
            ),
            dict(id=mid, cid=cid, uid=uid, title=name),
        )
    else:
        conn.execute(
            text("UPDATE app.documents SET description=description WHERE id=:id"), dict(id=mid)
        )
    version = one(
        conn,
        "SELECT coalesce(max(version_no),0)+1 AS n FROM app.document_versions WHERE document_id=:id",
        id=mid,
    )["n"]
    vid = uuid4()
    conn.execute(
        text("""INSERT INTO app.document_versions(id,course_id,document_id,version_no,original_name,mime_type,size_bytes,sha256,uploaded_by)
      VALUES(:id,:cid,:mid,:n,:name,:mime,:size,:hash,:uid)"""),
        dict(
            id=vid,
            cid=cid,
            mid=mid,
            n=version,
            name=name,
            mime=mime,
            size=len(data),
            hash=hashed,
            uid=uid,
        ),
    )
    conn.execute(
        text(
            "INSERT INTO app.file_contents(version_id,course_id,document_id,bytes) VALUES(:vid,:cid,:mid,:bytes)"
        ),
        dict(vid=vid, cid=cid, mid=mid, bytes=data),
    )
    rid = prepare_run(conn, cid, mid, vid, space["active_space_id"], uid)
    return dict(materialId=str(mid), versionId=str(vid), runId=str(rid), status="queued")


@router.post("/courses/{cid}/materials", status_code=202)
async def upload(
    cid: UUID, request: Request, files: list[UploadFile] = File(), uid=Depends(current_user)
):
    # Stream each file with a hard bound before retaining accepted bytes.
    from backend.db.connection import transaction

    if not 1 <= len(files) <= 10:
        raise ApiError("INVALID_FILE_COUNT", 422)
    values = []
    for f in files:
        data = bytearray()
        while part := await f.read(65536):
            data.extend(part)
            if len(data) > MAX_FILE_BYTES:
                raise ApiError("FILE_TOO_LARGE", 413)
        values.append((f.filename or "", bytes(data)))
    with transaction(uid) as conn:
        course(conn, cid, write=True)

        def accept():
            results = []
            for name, data in values:
                try:
                    with conn.begin_nested():
                        result = accept_file(conn, cid, uid, name, data)
                    results.append({"name": name, "data": result})
                except (ApiError, ValueError) as exc:
                    results.append(
                        {"name": name, "error": {"code": getattr(exc, "code", "INVALID_DOCUMENT")}}
                    )
            return {"results": results}

        result = idempotent(
            conn, request, [(name, sha256(data).hexdigest()) for name, data in values], accept
        )
        # Recheck every existing resource on replay; never serve cached titles/content.
        for item in result["results"]:
            if "data" in item:
                material(conn, cid, UUID(item["data"]["materialId"]))
        return {"data": result}


@router.post("/courses/{cid}/materials/{mid}/versions", status_code=202)
async def replace(
    cid: UUID, mid: UUID, request: Request, file: UploadFile = File(), uid=Depends(current_user)
):
    from backend.db.connection import transaction

    data = await file.read(MAX_FILE_BYTES + 1)
    with transaction(uid) as conn:
        material(conn, cid, mid, write=True)
        conn.execute(text("SELECT auth.lock_course(:id)"), dict(id=cid))
        row = one(conn, "SELECT * FROM app.documents WHERE id=:id FOR UPDATE", id=mid)

        def accept_replacement():
            match(request, row)
            return accept_file(conn, cid, uid, file.filename or "", data, mid)

        result = idempotent(
            conn,
            request,
            dict(hash=sha256(data).hexdigest(), name=file.filename),
            accept_replacement,
        )
        return {"data": result}


@router.get("/courses/{cid}/materials/{mid}/versions/{vid}/content")
def download(cid: UUID, mid: UUID, vid: UUID, conn=Depends(connection, scope="function")):
    material(conn, cid, mid)
    row = one(
        conn,
        """SELECT v.original_name,v.mime_type,v.status,b.bytes FROM app.document_versions v
      JOIN app.file_contents b ON b.version_id=v.id WHERE v.course_id=:cid AND v.document_id=:mid AND v.id=:vid""",
        cid=cid,
        mid=mid,
        vid=vid,
    )
    if not row or row["status"] != "ready":
        raise ApiError("NOT_FOUND", 404)
    return Response(
        bytes(row["bytes"]),
        media_type=row["mime_type"],
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(row["original_name"], safe=""),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/courses/{cid}/materials/{mid}", status_code=202)
def delete_material(
    cid: UUID, mid: UUID, request: Request, conn=Depends(connection, scope="function")
):
    material(conn, cid, mid, write=True)

    def withdraw():
        result = one(
            conn,
            "SELECT auth.request_document_delete(:cid,:mid,:rid) AS id",
            cid=cid,
            mid=mid,
            rid=uuid4(),
        )
        return {"deletionRequestId": str(result["id"]), "status": "pending"}

    return {"data": idempotent(conn, request, {}, withdraw)}


@router.get("/deletion-requests/{rid}")
def deletion_status(rid: UUID, conn=Depends(connection, scope="function")):
    result = one(
        conn,
        "SELECT id,state,requested_at,deadline_at,completed_at,error_code FROM app.deletion_requests WHERE id=:id",
        id=rid,
    )
    if not result:
        raise ApiError("NOT_FOUND", 404)
    return {"data": result}
