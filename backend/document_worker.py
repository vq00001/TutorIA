"""Consume trabajos de ingestión y publica fragmentos con una reserva vigente."""

import json
import os
import re
import time
from hashlib import sha256
from threading import Event, Thread
from uuid import UUID

import requests
from sqlalchemy import URL, cast, create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB

from .chunking import chunk_text, count_tokens
from .config import Config
from .db.connection import set_user_context
from .db.jobs import claim_job, fail_job, recover_jobs, renew_job_lease
from .db.processing import get_processing_course_name, get_processing_source, vector_text
from .embeddings import embed
from .file_storage import store_converted_markdown

_PARSER_VERSION = "utf8-pdf-md-v1"


def worker_engine():
    return create_engine(URL.create(
        "postgresql+psycopg", host=os.environ["POSTGRES_HOST"],
        database=os.environ["POSTGRES_DB"], username="tutoria_worker",
        password=os.environ["WORKER_DB_PASSWORD"],
    ), pool_pre_ping=True, hide_parameters=True)


def model_revision(config: Config) -> str:
    response = requests.get(f"{config.ollama_url}/api/tags", timeout=20)
    response.raise_for_status()
    models = response.json().get("models", [])
    for model in models:
        names = (model.get("name"), model.get("model"))
        if any(name == config.model_embed or name == f"{config.model_embed}:latest" for name in names):
            revision = model.get("digest")
            if isinstance(revision, str) and revision:
                return revision
    raise ValueError("EMBEDDING_MODEL_NOT_FOUND")


def chunks_for_publication(text: str, title: str, config: Config):
    parents, children = chunk_text(text, title, config)
    if not parents or not children:
        raise ValueError("EMPTY_DOCUMENT_CHUNKS")
    parent_payload = [{
        "text": parent.text, "section_path": parent.path,
        "locator": {"title": title, "section": " > ".join(parent.path), "index": parent.index},
        "token_estimate": max(1, count_tokens(parent.text, config.token_estimate_ratio)),
    } for parent in parents]
    parent_indices = {parent.id: index for index, parent in enumerate(parents)}
    spans = []
    for child in children:
        pattern = r"\s+".join(re.escape(part) for part in child.text.split())
        match = re.search(pattern, text) if pattern else None
        if match is None:
            spans = []
            break
        spans.append((match.start(), match.end(), match.group()))
    if not spans:
        # El fragmentador puede recomponer párrafos o títulos. El original sigue
        # en file_contents; esta representación normalizada da offsets exactos.
        text = "\n\n".join(child.text for child in children)
        position = 0
        for child in children:
            spans.append((position, position + len(child.text), child.text))
            position += len(child.text) + 2
    vectors = embed(config, [child.embed_text for child in children], batch_size=8)
    child_payload = []
    for child, vector, (start, end, exact_text) in zip(children, vectors, spans, strict=True):
        child_payload.append({
            "parent_index": parent_indices[child.parent_id], "text": exact_text,
            "embedding_text": child.embed_text, "start_offset": start,
            "end_offset": end,
            "token_estimate": max(1, count_tokens(child.text, config.token_estimate_ratio)),
            "embedding": vector_text(vector),
        })
    return text, parent_payload, child_payload


def extract_document_text(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        from .utilities.pdf_to_md.pdf_to_md import pdf_bytes_to_markdown

        return pdf_bytes_to_markdown(data)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("INVALID_DOCUMENT_ENCODING") from exc


def renew_while_processing(engine, job_id: UUID, token: UUID, stop: Event) -> None:
    while not stop.wait(30):
        try:
            with engine.begin() as connection:
                if not renew_job_lease(connection, job_id, token):
                    return
        except Exception:
            return


def process_job(engine, job, config: Config, space_id: UUID) -> None:
    parameters = {"child_target_tokens": config.child_target_tokens,
                  "parent_max_tokens": config.parent_max_tokens}
    digest = sha256(json.dumps(
        [str(space_id), _PARSER_VERSION, "chunk-v1", parameters],
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).digest()
    with engine.begin() as connection:
        run_id = connection.scalar(select(func.app.start_ingestion_job(
            job.id, job.lease_token, space_id, digest, _PARSER_VERSION, "chunk-v1",
            cast(parameters, JSONB),
        )))
    if run_id is None:
        return
    with engine.begin() as connection:
        set_user_context(connection, job.requested_by)
        course_name = get_processing_course_name(connection, run_id)
        source = get_processing_source(connection, run_id)
    if source is None or source.bytes is None:
        raise ValueError("DOCUMENT_SOURCE_MISSING")
    if course_name is None:
        raise ValueError("COURSE_NOT_FOUND")
    text = extract_document_text(source.original_name, bytes(source.bytes))
    if source.original_name.lower().endswith(".pdf"):
        store_converted_markdown(
            course_name=course_name,
            course_id=job.course_id,
            document_id=job.target_id,
            original_filename=source.original_name,
            content=text.encode("utf-8"),
        )
    extracted_text, parents, children = chunks_for_publication(text, source.original_name, config)
    with engine.begin() as connection:
        result = connection.scalar(select(func.app.publish_ingestion_job(
            job.id, job.lease_token, run_id, extracted_text,
            cast(parents, JSONB), cast(children, JSONB),
        )))
        if result != run_id:
            raise ValueError("INGESTION_PUBLICATION_REJECTED")


def main() -> None:
    config = Config.from_env()
    engine = worker_engine()
    try:
        while True:
            try:
                revision = model_revision(config)
                dimension = len(embed(config, ["TutorIA embedding probe"])[0])
                with engine.begin() as connection:
                    space_id = connection.scalar(select(func.app.register_worker_embedding_space(
                        config.model_embed, revision, dimension,
                    )))
                break
            except (requests.RequestException, ValueError) as exc:
                print(f"Worker: esperando modelo de embeddings ({type(exc).__name__})", flush=True)
                time.sleep(5)
        while True:
            with engine.begin() as connection:
                recover_jobs(connection)
                job = claim_job(connection, "document-worker", kinds=["ingest_document"])
            if job is None:
                time.sleep(2)
                continue
            stop = Event()
            heartbeat = Thread(target=renew_while_processing, args=(engine, job.id, job.lease_token, stop), daemon=True)
            heartbeat.start()
            try:
                process_job(engine, job, config, space_id)
                print(f"Documento procesado: {job.target_id}", flush=True)
            except Exception as exc:
                print(f"Error al procesar {job.target_id}: {type(exc).__name__}: {exc}", flush=True)
                with engine.begin() as connection:
                    fail_job(connection, job.id, job.lease_token,
                             error_code="INGESTION_FAILED", retryable=False)
            finally:
                stop.set()
                heartbeat.join(timeout=2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
