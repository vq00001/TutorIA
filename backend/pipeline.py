"""Offline preview and authenticated HTTP ingestion; SQL writes belong to workers."""
from pathlib import Path
from uuid import uuid4

from .chunking import chunk_text
from .documents import read_document


def prepare_document(config,file:Path,root:Path|None=None):
    return chunk_text(read_document(file),str(uuid4()),config)


def ingest_document(config,file:Path,root:Path|None=None):
    raise RuntimeError('Use authenticated POST /api/v1/courses/{courseId}/materials; direct CLI writes are disabled')


def retrieve_documents(config,question,limit=None,score_threshold=None):
    raise RuntimeError('Use the authenticated course chat API; unscoped CLI search is disabled')
