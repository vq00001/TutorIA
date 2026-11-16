"""Consume trabajos de chat, recupera chunks vectoriales y publica respuestas citadas."""

from __future__ import annotations

import json
import os
import time
from threading import Event, Thread
from uuid import UUID

import requests
from sqlalchemy import URL, cast, create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError

from .config import Config
from .db.connection import set_user_context
from .db.jobs import claim_job, fail_job, recover_jobs, renew_job_lease
from .db.vector_search import search_course_chunks
from .embeddings import embed
from .llm import chat_completion


def worker_engine():
    return create_engine(URL.create(
        "postgresql+psycopg", host=os.environ["POSTGRES_HOST"],
        database=os.environ["POSTGRES_DB"], username="tutoria_worker",
        password=os.environ["WORKER_DB_PASSWORD"],
    ), pool_pre_ping=True, hide_parameters=True)


def renew_while_processing(engine, job_id: UUID, token: UUID, stop: Event) -> None:
    while not stop.wait(30):
        try:
            with engine.begin() as connection:
                if not renew_job_lease(connection, job_id, token):
                    return
        except (OSError, RuntimeError, SQLAlchemyError):
            return


def _embedding_space(engine, config: Config) -> UUID | None:
    response = requests.get(f"{config.ollama_url}/api/tags", timeout=20)
    response.raise_for_status()
    models = response.json().get("models", [])
    revision = next(
        (
            model.get("digest")
            for model in models
            if config.model_embed in (model.get("name"), model.get("model"))
            or f"{config.model_embed}:latest" in (model.get("name"), model.get("model"))
        ),
        None,
    )
    if not isinstance(revision, str) or not revision:
        raise ValueError("EMBEDDING_MODEL_NOT_FOUND")
    dimension = len(embed(config, ["TutorIA embedding probe"])[0])
    with engine.begin() as connection:
        return connection.scalar(select(func.app.register_worker_embedding_space(
            config.model_embed, revision, dimension,
        )))


def process_job(engine, job, config: Config, space_id: UUID) -> None:
    payload = job.payload
    conversation_id = UUID(payload["conversation_id"])
    reply_id = UUID(payload["reply_id"])
    include_documents = bool(payload.get("include_documents", True))

    with engine.begin() as connection:
        set_user_context(connection, job.requested_by)
        context = connection.scalar(select(func.app.read_chat_context(
            job.course_id, conversation_id, reply_id,
        )))
    question = context.get("question") if isinstance(context, dict) else None
    history_rows = (
        [(turn.get("role"), [{"type": "text", "text": turn.get("content")}])
         for turn in context.get("history", [])
         if isinstance(turn, dict)]
        if isinstance(context, dict) else []
    )
    if not isinstance(question, str) or not question.strip():
        raise ValueError("CHAT_QUESTION_MISSING")

    hits = []
    if include_documents:
        query_vector = embed(config, [question])[0]
        with engine.begin() as connection:
            set_user_context(connection, job.requested_by)
            hits = search_course_chunks(
                connection, job.course_id, space_id, query_vector,
                limit=config.retrieval_limit, threshold=config.similarity_threshold,
            )

    names = {
        hit["document_id"]: (hit["locator"] or {}).get("title", str(hit["document_id"]))
        for hit in hits
    }
    context = "\n\n".join(
        f"[{index}] {names[hit['document_id']]} "
        f"({' > '.join(hit['section_path'])})\n{hit['parent_text']}"
        for index, hit in enumerate(hits, 1)
    )
    prompt = (
        "Responde en el idioma de la pregunta. Puedes usar conocimiento general, pero "
        "prioriza el contexto documental y no inventes hechos atribuidos a él. "
        "Si el contexto no alcanza, dilo claramente. No agregues una lista de referencias: "
        "el sistema las adjuntará por separado.\n\n"
        f"Conversación previa:\n{_history_text(history_rows) or '(Es el primer mensaje.)'}\n\n"
        f"Pregunta: {question}\n\n"
        f"Contexto documental:\n{context or '(No se encontraron chunks relevantes.)'}"
    )
    answer = chat_completion(config, prompt, temperature=0, max_tokens=500, timeout=180)
    body = json.dumps({"messages": [{"role": "user", "content": prompt}]}, ensure_ascii=False).encode()
    sources = [
        {
            "document_id": str(hit["document_id"]), "run_id": str(hit["run_id"]),
            "parent_id": str(hit["parent_id"]), "child_id": str(hit["child_id"]),
            "embedding_space_id": str(hit["embedding_space_id"]),
            "start_offset": hit["start_offset"], "end_offset": hit["end_offset"],
            "score": float(hit["score"]),
        }
        for hit in hits
    ]
    content = [{"type": "text", "text": answer}]
    citations = [
        {
            "source_rank": index,
            "label":             f"{names[hit['document_id']]}: "
                     f"{' > '.join(hit['section_path']) or 'Sección sin título'}",
        }
        for index, hit in enumerate(hits, 1)
    ]
    with engine.begin() as connection:
        set_user_context(connection, job.requested_by)
        request_id = connection.scalar(select(func.app.record_job_request(
            job.id, job.lease_token, body, cast(sources, JSONB),
        )))
        connection.scalar(select(func.app.publish_chat_job(
            job.id, job.lease_token, conversation_id, cast(content, JSONB),
            reply_id, request_id, cast(citations, JSONB),
        )))


def _history_text(rows) -> str:
    turns = []
    for role, parts in rows:
        text = next(
            (part.get("text") for part in parts or []
             if isinstance(part, dict) and part.get("type") == "text"),
            None,
        )
        if isinstance(text, str) and text.strip():
            turns.append(f"{role}: {text}")
    return "\n".join(turns)


def main() -> None:
    config = Config.from_env()
    engine = worker_engine()
    try:
        while True:
            try:
                space_id = _embedding_space(engine, config)
                if space_id is not None:
                    break
            except (requests.RequestException, ValueError, SQLAlchemyError):
                pass
            print("Chat worker: esperando el espacio de embeddings", flush=True)
            time.sleep(5)

        while True:
            with engine.begin() as connection:
                recover_jobs(connection)
                job = claim_job(connection, "chat-worker", kinds=["generate_chat"])
            if job is None:
                time.sleep(2)
                continue
            stop = Event()
            heartbeat = Thread(
                target=renew_while_processing,
                args=(engine, job.id, job.lease_token, stop),
                daemon=True,
            )
            heartbeat.start()
            try:
                process_job(engine, job, config, space_id)
                print(f"Chat procesado: {job.id}", flush=True)
            except (KeyError, TypeError, ValueError, requests.RequestException, SQLAlchemyError) as exc:
                print(f"Error al procesar {job.id}: {type(exc).__name__}: {exc}", flush=True)
                with engine.begin() as connection:
                    fail_job(connection, job.id, job.lease_token,
                             error_code="CHAT_GENERATION_FAILED", retryable=False)
            finally:
                stop.set()
                heartbeat.join(timeout=2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
