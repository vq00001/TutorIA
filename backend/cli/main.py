"""CLI del pipeline RAG: vista previa del procesamiento de documentos."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.config import Config
from backend.rag.chunking import count_tokens
from backend.rag.documents import iter_documents
from backend.rag.pipeline import prepare_document


def main() -> None:
    """Ejecuta la CLI de preview, ingestión o recuperación con respuesta."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser("preview", help="muestra chunks sin usar servicios")
    preview.add_argument("path", type=Path)

    args = parser.parse_args()
    config = Config.from_env()

    match args.command:
        case "preview":
            root = args.path if args.path.is_dir() else None
            for file in iter_documents(args.path):
                parents, children = prepare_document(config, file, root)
                print(f"\n--- {file.name}: {len(parents)} parents, {len(children)} children ---")
                for parent in parents:
                    print(
                        f"\n[PARENT {parent.index}] "
                        f"{' > '.join(parent.path) or '(sin título)'} "
                        f"(~{count_tokens(parent.text, config.token_estimate_ratio)} tok)"
                    )
                    for child in (item for item in children if item.parent_id == parent.id):
                        print(
                            f"   - child {child.index} "
                            f"(~{count_tokens(child.embed_text, config.token_estimate_ratio)} tok): "
                            f"{child.text[:90]!r}..."
                        )

        case _:
            raise ValueError(f"Comando desconocido: {args.command}")


if __name__ == "__main__":
    main()
