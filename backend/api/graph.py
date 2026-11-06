from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import text

from backend.api.auth import Input
from backend.api.common import ApiError, connection, course, match
from backend.db.connection import one, rows

router = APIRouter()


@router.get("/courses/{cid}/concepts/drafts")
def drafts(cid: UUID, conn=Depends(connection, scope="function")):
    course(conn, cid, coordinator=True)
    return {
        "data": rows(
            conn,
            "SELECT id,label,description,row_version FROM app.concepts WHERE course_id=:cid AND status='draft' ORDER BY created_at,id LIMIT 100",
            cid=cid,
        )
    }


class ConceptInput(Input):
    label: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=10000)
    estimatedMinutes: int = Field(default=20, ge=1, le=240)


class EdgeInput(Input):
    sourceId: UUID
    targetId: UUID
    relation: str = Field(pattern="^(prerequisite|part_of|related)$")


@router.get("/courses/{cid}/knowledge-graph")
def graph(cid: UUID, conn=Depends(connection, scope="function")):
    c = course(conn, cid)
    concepts = rows(
        conn,
        """SELECT c.*,m.level,m.alpha,m.beta,m.evidence_count FROM app.concepts c LEFT JOIN app.concept_mastery m
     ON m.course_id=c.course_id AND m.concept_id=c.id AND m.user_id=auth.uid()
     WHERE c.course_id=:cid AND c.status='approved' ORDER BY c.id""",
        cid=cid,
    )
    edges = rows(
        conn,
        "SELECT * FROM app.concept_edges WHERE course_id=:cid AND status='approved' ORDER BY id",
        cid=cid,
    )
    return {
        "data": dict(
            graphRevision=c["graph_revision"],
            nodes=[
                {
                    "key": str(c["id"]),
                    "attributes": {
                        "label": c["label"],
                        "description": c["description"],
                        "masteryLevel": c["level"] or "unknown",
                        "masteryScore": float(c["alpha"] / (c["alpha"] + c["beta"]))
                        if c["evidence_count"]
                        else None,
                        "progress": float(100 * c["alpha"] / (c["alpha"] + c["beta"]))
                        if c["evidence_count"]
                        else 0,
                    },
                }
                for c in concepts
            ],
            edges=[
                dict(
                    key=str(e["id"]),
                    source=str(e["source_id"]),
                    target=str(e["target_id"]),
                    attributes={"relation": e["relation"], "type": "arrow"},
                )
                for e in edges
            ],
        )
    }


@router.post("/courses/{cid}/concepts", status_code=201)
def create_concept(cid: UUID, body: ConceptInput, conn=Depends(connection, scope="function")):
    course(conn, cid, coordinator=True, write=True)
    key = uuid4()
    conn.execute(
        text("""INSERT INTO app.concepts(id,course_id,canonical_key,label,description,estimated_minutes,created_by)
      VALUES(:id,:cid,:key,:label,:description,:minutes,auth.uid())"""),
        dict(
            id=key,
            cid=cid,
            key=str(key),
            label=body.label,
            description=body.description,
            minutes=body.estimatedMinutes,
        ),
    )
    return {"data": {"id": key, "status": "draft", "rowVersion": 1}}


@router.post("/courses/{cid}/edges", status_code=201)
def create_edge(cid: UUID, body: EdgeInput, conn=Depends(connection, scope="function")):
    course(conn, cid, coordinator=True, write=True)
    source, target = body.sourceId, body.targetId
    if body.relation == "related" and source > target:
        source, target = target, source
    eid = uuid4()
    conn.execute(
        text("""INSERT INTO app.concept_edges(id,course_id,source_id,target_id,relation)
      VALUES(:id,:cid,:source,:target,:relation)"""),
        dict(id=eid, cid=cid, source=source, target=target, relation=body.relation),
    )
    return {"data": {"id": eid, "status": "draft", "rowVersion": 1}}


@router.post("/courses/{cid}/concepts/{item}/approve")
def approve_concept(
    cid: UUID, item: UUID, request: Request, conn=Depends(connection, scope="function")
):
    return approve(conn, cid, item, request, "concepts")


@router.post("/courses/{cid}/edges/{item}/approve")
def approve_edge(
    cid: UUID, item: UUID, request: Request, conn=Depends(connection, scope="function")
):
    return approve(conn, cid, item, request, "concept_edges")


def approve(conn, cid, item, request, table):
    course(conn, cid, coordinator=True, write=True)
    conn.execute(text("SELECT id FROM app.courses WHERE id=:id FOR UPDATE"), dict(id=cid))
    # table is a static handler constant, never supplied by a request.
    row = one(
        conn,
        f"SELECT * FROM app.{table} WHERE id=:id AND course_id=:cid FOR UPDATE",
        id=item,
        cid=cid,
    )
    if not row:
        raise ApiError("NOT_FOUND", 404)
    match(request, row)
    conn.execute(text(f"UPDATE app.{table} SET status='approved' WHERE id=:id"), dict(id=item))
    return {"data": {"id": item, "status": "approved", "rowVersion": row["row_version"] + 1}}
