"""Preview personalized prerequisite hypotheses from existing nodes and mock scores."""

import argparse
from pathlib import Path

from backend.knowledge_graph.learning_gaps import infer_gaps, load_inputs


DEFAULT_NODES = Path(__file__).resolve().parents[1] / "fixtures" / "integracion_multivariable_nodes.json"
DEFAULT_SCORES = Path(__file__).with_name("scores.json")


def main():
    parser = argparse.ArgumentParser(description="Prueba aislada de falencias previas")
    parser.add_argument("--nodes", type=Path, default=DEFAULT_NODES)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--threshold", type=float, default=0.4)
    args = parser.parse_args()

    nodes, scores = load_inputs(args.nodes, args.scores)
    result = infer_gaps(nodes, scores, args.threshold)
    names = {f"C{index}": node["label"] for index, node in enumerate(nodes, 1)}
    names.update({node["ref"]: node["label"] for node in result["new_nodes"]})
    print(f"Nodos existentes: {len(nodes)}; conceptos con resultado bajo: {len(result['weak'])}")
    for ref, score in result["weak"].items():
        print(f"  {names[ref]}: {score['correct']}/{score['total']}")
    print(f"Temas nuevos propuestos: {len(result['new_nodes'])}")
    for node in result["new_nodes"]:
        print(f"  {node['label']} — {node['description']}")
    print(f"Recomendaciones de repaso: {len(result['recommendations'])}")
    for recommendation in result["recommendations"]:
        kind = "Tema nuevo" if recommendation["kind"] == "new" else "Concepto existente"
        print(f"  Para {names[recommendation['target_ref']]}: {kind}: "
              f"{names[recommendation['node_ref']]} — {recommendation['reason']}")
    print(f"Respuestas o sugerencias descartadas: {result['discarded']}")
    print("Hipótesis no confirmadas; no se guardó ni modificó el grafo del curso.")


if __name__ == "__main__":
    main()
