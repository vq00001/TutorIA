import argparse
from pathlib import Path

from backend.chunking import count_tokens
from backend.config import Config
from backend.documents import SUPPORTED_EXTENSIONS
from backend.knowledge_graph.concepts import extract_candidates
from backend.knowledge_graph.consolidation import consolidate_candidates
from backend.knowledge_graph.prerequisites import propose_prerequisites
from backend.pipeline import prepare_document


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument(
        "--target-concepts", type=int,
        help="objetivo aproximado de nodos tras fusionar conceptos solapados",
    )
    parser.add_argument(
        "--with-prerequisites",
        action="store_true",
        help="propone dependencias entre los conceptos extraídos, sin guardarlas",
    )
    args = parser.parse_args()

    config = Config.from_env()
    if args.file.suffix.lower() not in SUPPORTED_EXTENSIONS:
        parser.error("se admiten archivos .md, .markdown y .txt")
    parents, _ = prepare_document(config, args.file)
    sources = [
        {
            "id": parent.id,
            "text": parent.text,
            "locator": {"section": " > ".join(parent.path), "index": parent.index},
            "token_estimate": max(1, count_tokens(parent.text, config.token_estimate_ratio)),
        }
        for parent in parents
    ]

    print(f"Fragmentos enviados: {len(sources)}", flush=True)
    candidates = extract_candidates(
        sources,
        config.model_llm,
        lambda: 180,
    )

    if args.target_concepts is not None:
        original_count = len(candidates)
        original_candidates = candidates
        candidates, merges = consolidate_candidates(
            candidates, args.target_concepts, config.model_llm, lambda: 180
        )
        print(f"Consolidación: {original_count} → {len(candidates)} nodos; "
              f"{len(merges)} grupos fusionados")
        for merge in merges:
            originals = ", ".join(original_candidates[key]["label"] for key in merge["from_keys"])
            print(f"  {originals} → {candidates[merge['to_key']]['label']}")

    print(f"\nConceptos encontrados: {len(candidates)}")
    for number, concept in enumerate(candidates.values(), 1):
        print(f"\n{number}. {concept['label']}")
        print(f"   {concept['description']}")
        for source_id in sorted(concept["source_ids"], key=str):
            source = next(s for s in sources if s["id"] == source_id)
            locator = source["locator"]
            section = locator["section"] or "sin sección"
            print(f"   Fuente: {section}, fragmento {locator['index'] + 1} ({source_id})")

    if args.with_prerequisites:
        proposals = propose_prerequisites(candidates, config.model_llm, lambda: 180)
        print(f"\nDependencias propuestas: {len(proposals)}")
        for number, proposal in enumerate(proposals, 1):
            source = candidates[proposal["source_key"]]["label"]
            target = candidates[proposal["target_key"]]["label"]
            print(f"\n{number}. {source} → {target}")
            print(f"   Motivo propuesto: {proposal['reason']}")
        print("\nEstas relaciones son inferencias del modelo; no se han guardado ni aprobado.")


if __name__ == "__main__":
    main()
