import argparse
import os
from pathlib import Path

from backend.config import Config
from backend.services.documents import validate_file
from backend.workers.concepts import extract_candidates
from backend.workers.ingest import source_chunks
from backend.workers.prerequisites import propose_prerequisites


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument(
        "--with-prerequisites",
        action="store_true",
        help="propone dependencias entre los conceptos extraídos, sin guardarlas",
    )
    args = parser.parse_args()

    config = Config.from_env()
    _, _, content, _ = validate_file(args.file.name, args.file.read_bytes())

    chunk_config = {
        "parent_max_tokens": config.parent_max_tokens,
        "child_target_tokens": config.child_target_tokens,
        "token_estimate_ratio": config.token_estimate_ratio,
        "overlap_ratio": config.overlap_ratio,
    }
    parents = source_chunks(content, chunk_config)
    sources = [
        {
            "id": parent["id"],
            "text": parent["text"],
            "locator": parent["locator"],
            "token_estimate": max(1, int(len(parent["text"].split()) * 1.3)),
        }
        for parent in parents
    ]

    print(f"Fragmentos enviados: {len(sources)}", flush=True)
    candidates = extract_candidates(
        sources,
        os.environ["MODEL_LLM"],
        lambda: 180,
    )

    print(f"\nConceptos encontrados: {len(candidates)}")
    for number, concept in enumerate(candidates.values(), 1):
        print(f"\n{number}. {concept['label']}")
        print(f"   {concept['description']}")
        for source_id in sorted(concept["source_ids"], key=str):
            source = next(s for s in sources if s["id"] == source_id)
            locator = source["locator"]
            print(
                f"   Fuente: líneas {locator['lineStart']}"
                f"–{locator['lineEnd']} ({source_id})"
            )

    if args.with_prerequisites:
        proposals = propose_prerequisites(candidates, os.environ["MODEL_LLM"], lambda: 180)
        print(f"\nDependencias propuestas: {len(proposals)}")
        for number, proposal in enumerate(proposals, 1):
            source = candidates[proposal["source_key"]]["label"]
            target = candidates[proposal["target_key"]]["label"]
            print(f"\n{number}. {source} → {target}")
            print(f"   Motivo propuesto: {proposal['reason']}")
        print("\nEstas relaciones son inferencias del modelo; no se han guardado ni aprobado.")


if __name__ == "__main__":
    main()
