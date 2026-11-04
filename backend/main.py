"""CLI del pipeline RAG: preview, ingestión de documentos y consulta con respuesta."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "backend"

from .chunking import chunk_text, count_tokens
from .config import Config
from .documents import document_id, iter_documents, read_document
from .llm import generate_answer
from .pipeline import ingest_document, retrieve_documents


def main() -> None:
    """Ejecuta la CLI de preview, ingestión o recuperación con respuesta."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser("preview", help="muestra chunks sin usar servicios")
    preview.add_argument("path", type=Path)

    ingest = subparsers.add_parser("ingest", help="chunkea, embebe y guarda documentos")
    ingest.add_argument("path", type=Path)
    ingest.add_argument(
        "--context", action="store_true", help="añade contexto generado por el LLM"
    )

    search = subparsers.add_parser("search", help="recupera documentos relacionados")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=None)
    search.add_argument(
        "--answer",
        action="store_true",
        help="genera una respuesta usando el contexto recuperado",
    )

    args = parser.parse_args()
    config = Config.from_env()

    match args.command:
        case "preview":
            root = args.path if args.path.is_dir() else None
            for file in iter_documents(args.path):
                parents, children = chunk_text(
                    read_document(file), document_id(file, root), config
                )
                print(
                    f"\n--- {file.name}: {len(parents)} parents, {len(children)} children ---"
                )
                for parent in parents:
                    print(
                        f"\n[PARENT {parent.index}] "
                        f"{' > '.join(parent.path) or '(sin título)'} "
                        f"(~{count_tokens(parent.text, config.token_estimate_ratio)} tok)"
                    )
                    for child in (
                        item for item in children if item.parent_id == parent.id
                    ):
                        print(
                            f"   - child {child.index} "
                            f"(~{count_tokens(child.embed_text, config.token_estimate_ratio)} tok): "
                            f"{child.text[:90]!r}..."
                        )

        case "ingest":
            config.use_context = args.context
            root = args.path if args.path.is_dir() else None
            for file in iter_documents(args.path):
                parents_count, children_count = ingest_document(config, file, root)
                print(
                    f"- {document_id(file, root)}: {parents_count} parents, {children_count} children"
                )

        case "search":
            documents = retrieve_documents(config, args.query, args.k)
            if args.answer:
                print(generate_answer(config, args.query, documents))
            else:
                for document in documents:
                    print(
                        f"\n[{document['score']:.3f}] {document['doc_id']} :: {' > '.join(document.get('path', []))}\n{document['text'][:500]}"
                    )

        case _:
            raise ValueError(f"Comando desconocido: {args.command}")


if __name__ == "__main__":
    main()
