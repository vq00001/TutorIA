"""Aceptación de una propuesta del grafo inicial de una clase."""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from ...db.courses import courses, get_member_course
from ...db.learning_catalog import add_prerequisite, create_concept, list_concepts
from ...knowledge_graph.preview import GraphPreview
from .dependencies import AuthenticatedConnection, ErrorResponse, error_response

router = APIRouter(prefix="/courses/{courseId}", tags=["Learning"])


class AppliedGraph(BaseModel):
    conceptIdsByRef: dict[str, UUID]
    edgeCount: int


class ApplyResponse(BaseModel):
    data: AppliedGraph


def _valid_proposal(proposal: GraphPreview) -> bool:
    if not 1 <= len(proposal.nodes) <= 400 or len(proposal.edges) > 2000:
        return False
    refs = [node.ref for node in proposal.nodes]
    if any(not ref.strip() or not node.label.strip() for ref, node in zip(refs, proposal.nodes)):
        return False
    if len(set(refs)) != len(refs):
        return False
    edges = [(edge.source_ref, edge.target_ref) for edge in proposal.edges]
    if len(set(edges)) != len(edges):
        return False
    return all(source in refs and target in refs and source != target for source, target in edges)


@router.post(
    "/knowledge-graph/apply",
    status_code=201,
    response_model=ApplyResponse,
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422)},
)
def graph_apply(courseId: UUID, proposal: GraphPreview, connection: AuthenticatedConnection):
    course = get_member_course(connection, courseId)
    if course is None:
        return error_response(404, "COURSE_NOT_FOUND", "Clase no encontrada.")
    if course.my_role != "coordinator" or course.status != "active":
        return error_response(403, "GRAPH_APPLY_FORBIDDEN", "La clase no admite esta operación.")
    if not _valid_proposal(proposal):
        return error_response(
            422, "INVALID_GRAPH_PROPOSAL", "Revisa los nodos y relaciones de la propuesta."
        )

    try:
        with connection.begin_nested():
            # Serializa las importaciones a esta clase y evita dos grafos iniciales simultáneos.
            connection.execute(
                select(courses.c.id).where(courses.c.id == courseId).with_for_update()
            )
            if list_concepts(connection, courseId):
                return error_response(409, "GRAPH_ALREADY_EXISTS", "La clase ya tiene un grafo.")
            ids = {
                node.ref: create_concept(
                    connection, courseId, label=node.label, description=node.description
                )
                for node in proposal.nodes
            }
            for edge in proposal.edges:
                add_prerequisite(connection, courseId, ids[edge.source_ref], ids[edge.target_ref])
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None)
        primary = getattr(getattr(exc.orig, "diag", None), "message_primary", None)
        if code == "42501":
            return error_response(
                403, "GRAPH_APPLY_FORBIDDEN", "La clase no admite esta operación."
            )
        if code == "23505" or primary == "PREREQUISITE_CYCLE":
            return error_response(
                409, "GRAPH_APPLY_CONFLICT", "La propuesta entra en conflicto con el grafo."
            )
        if code in ("23503", "23514", "22001"):
            return error_response(
                422, "INVALID_GRAPH_PROPOSAL", "Revisa los nodos y relaciones de la propuesta."
            )
        raise
    return ApplyResponse(data=AppliedGraph(conceptIdsByRef=ids, edgeCount=len(proposal.edges)))
