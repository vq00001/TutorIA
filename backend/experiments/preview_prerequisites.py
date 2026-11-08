"""Preview prerequisite proposals from a fixed list of concept nodes."""

import argparse
import json
import os
from pathlib import Path

from backend.workers.concepts import normalized_label
from backend.workers.prerequisites import propose_prerequisites


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
    args = parser.parse_args()

    candidates = load_candidates(args.file)
    print(f"Nodos cargados: {len(candidates)} ({args.file})", flush=True)
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
