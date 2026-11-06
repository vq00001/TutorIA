from uuid import UUID

from backend.db.connection import one, rows
from backend.workers.ingest import vector_text


class PgVectorStore:
    """Exact course-filtered search, grouping before limiting parents."""

    def __init__(self, connection, course_id: UUID, embedding_space_id: UUID):
        self.connection = connection
        self.course_id = course_id
        self.space_id = embedding_space_id

    def search(self, vector, limit=4, threshold=0.5):
        if not 1 <= limit <= 12 or not -1 <= threshold <= 1:
            raise ValueError("INVALID_SEARCH_PARAMETERS")
        space = one(
            self.connection,
            """SELECT s.* FROM app.course_embedding_config cfg
            JOIN app.embedding_spaces s ON s.id=cfg.active_space_id
            WHERE cfg.course_id=:cid AND cfg.active_space_id=:space""",
            cid=self.course_id,
            space=self.space_id,
        )
        if not space:
            raise ValueError("EMBEDDING_SPACE_MISMATCH")
        query = vector_text(vector, space["dimension"])
        return rows(
            self.connection,
            """WITH eligible AS MATERIALIZED (
            SELECT c.parent_id,e.embedding FROM app.documents d
            JOIN app.processing_runs r ON r.course_id=d.course_id AND r.document_id=d.id
              AND r.id=d.active_run_id AND r.state='ready'
            JOIN app.child_chunks c ON c.course_id=r.course_id AND c.run_id=r.id
            JOIN app.chunk_embeddings e ON e.course_id=c.course_id AND e.run_id=c.run_id AND e.child_id=c.id
            WHERE d.course_id=:cid AND d.status='active' AND e.embedding_space_id=:space
          ), ranked AS (SELECT parent_id,max(1-(embedding <=> CAST(:vector AS vector))) score FROM eligible GROUP BY parent_id)
          SELECT p.id,p.document_id AS doc_id,p.run_id,p.text,p.section_path AS path,p.locator,r.score
          FROM ranked r JOIN app.parent_chunks p ON p.id=r.parent_id AND p.course_id=:cid
          WHERE r.score>=:threshold ORDER BY r.score DESC,p.id LIMIT :limit""",
            cid=self.course_id,
            space=self.space_id,
            vector=query,
            threshold=threshold,
            limit=limit,
        )
