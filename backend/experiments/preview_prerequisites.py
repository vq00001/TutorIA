"""Preview prerequisite proposals from a fixed list of concept nodes."""

import argparse
import json
from pathlib import Path

from backend.config import Config
from backend.experiments.knowledge_graph.concepts import normalized_label
from backend.experiments.knowledge_graph.consolidation import consolidate_candidates
from backend.experiments.knowledge_graph.prerequisites import propose_prerequisites


DEFAULT_NODES = Path(__file__).parent / "fixtures" / "integracion_multivariable_nodes.json"


def load_candidates(path: Path) -> dict:
    """Load labels and descriptions without extracting concepts or contacting Ollama."""
    nodes = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(nodes, list) or len(nodes) < 2:
        raise ValueError("INVALID_CONCEPT_FIXTURE: se esperan al menos dos nodos")

    candidates = {}
    for number, node in enumerate(nodes, 1):
        if not isinstance(node, dict) or set(node) != {"label", "description"}:
            raise ValueError(f"INVALID_CONCEPT_FIXTURE: nodo {number} sin label y description")
        label, description = node["label"], node["description"]
        if not isinstance(label, str) or not label.strip() or not isinstance(description, str):
            raise ValueError(f"INVALID_CONCEPT_FIXTURE: nodo {number} inválido")
        key = normalized_label(label)
        if key in candidates:
            raise ValueError(f"DUPLICATE_CONCEPT_LABEL: nodo {number}: {label}")
        candidates[key] = {"label": label.strip(), "description": description.strip()}
    return candidates


def main():
    parser = argparse.ArgumentParser(description="Propone dependencias desde nodos ya creados")
    parser.add_argument("file", nargs="?", type=Path, default=DEFAULT_NODES)
    parser.add_argument(
        "--target-concepts", type=int,
        help="objetivo aproximado de nodos tras fusionar conceptos solapados",
    )
    args = parser.parse_args()

    candidates = load_candidates(args.file)
    model = Config.from_env().model_llm
    print(f"Nodos cargados: {len(candidates)} ({args.file})", flush=True)
    if args.target_concepts is not None:
        original_candidates = candidates
        candidates, merges = consolidate_candidates(
            candidates, args.target_concepts, model, lambda: 180
        )
        print(f"Consolidación: {len(original_candidates)} → {len(candidates)} nodos; "
              f"{len(merges)} grupos fusionados")
        for merge in merges:
            originals = ", ".join(original_candidates[key]["label"] for key in merge["from_keys"])
            print(f"  {originals} → {candidates[merge['to_key']]['label']}")
    proposals = propose_prerequisites(candidates, model, lambda: 180)

    print(f"\nDependencias propuestas: {len(proposals)}")
    for number, proposal in enumerate(proposals, 1):
        source = candidates[proposal["source_key"]]["label"]
        target = candidates[proposal["target_key"]]["label"]
        print(f"\n{number}. {source} → {target}")
        print(f"   Motivo propuesto: {proposal['reason']}")
    print("\nEstas relaciones son inferencias del modelo; no se han guardado ni aprobado.")


if __name__ == "__main__":
    main()
