"""Public repository import; Qdrant has been replaced by pgvector."""
from backend.repositories.vector import PgVectorStore

__all__=['PgVectorStore']
