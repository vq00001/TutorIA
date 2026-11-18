"""Comprobaciones profundas reutilizadas por los healthchecks de Docker."""

from __future__ import annotations

import argparse
import os
from urllib.error import HTTPError, URLError

import requests
from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from .core.config import Config
from .integrations.embeddings import embed
from .integrations.llm import chat_completion


def check_postgres(username: str = "tutoria_worker") -> None:
    password = os.environ["WORKER_DB_PASSWORD"]
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            host=os.environ["POSTGRES_HOST"],
            database=os.environ["POSTGRES_DB"],
            username=username,
            password=password,
        ),
        pool_pre_ping=True,
    )
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT 1")).scalar_one() != 1:
                raise RuntimeError("PostgreSQL no respondió a SELECT 1")
            vector_installed = connection.execute(
                text("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'vector')")
            ).scalar_one()
            if not vector_installed:
                raise RuntimeError("La extensión vector no está instalada")
    finally:
        engine.dispose()


def check_gateway_embedding(config: Config) -> None:
    vectors = embed(config, ["TutorIA healthcheck"], batch_size=1)
    if len(vectors) != 1 or not vectors[0]:
        raise RuntimeError("El gateway no respondió un embedding válido")


def check_worker(kind: str) -> None:
    check_postgres()
    config = Config.from_env()
    check_gateway_embedding(config)
    if kind == "chat":
        response = chat_completion(
            config,
            "Responde exactamente: OK",
            temperature=0,
            max_tokens=8,
            timeout=10,
        )
        if not response:
            raise RuntimeError("El modelo no respondió una prueba de chat")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["document-worker", "chat-worker"])
    args = parser.parse_args()

    match args.target:
        case "document-worker":
            check_worker("document")
        case "chat-worker":
            check_worker("chat")
        case _:
            raise ValueError("Target no válido")


if __name__ == "__main__":
    try:
        main()
    except (
        HTTPError,
        URLError,
        KeyError,
        RuntimeError,
        OSError,
        ValueError,
        SQLAlchemyError,
        requests.RequestException,
    ) as error:
        raise SystemExit(f"healthcheck failed: {error}")
