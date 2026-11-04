"""Compara límites de recuperación con el benchmark existente."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.experiments.retrieval.output import resolve_output_path
from backend.experiments.retrieval.repository import MODEL_EMBED, qdrant
from backend.experiments.retrieval.run import (
    DEFAULT_COLLECTION,
    DEFAULT_DIR,
    evaluate,
    index_documents,
    load_json,
)


def main() -> None:
    """Indexa el benchmark y evalúa cada valor de `limit`."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--documents",
        type=Path,
        default=DEFAULT_DIR / "data" / "documents" / "documents.json",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_DIR / "data" / "cases" / "test_cases.json",
    )
    parser.add_argument("--collection", default=f"{DEFAULT_COLLECTION}_limit")
    parser.add_argument("--model", default=MODEL_EMBED)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--limits", nargs="+", type=int, default=list(range(1, 9)))
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "--output",
        nargs="?",
        const="",
        metavar="NOMBRE",
        help="Guarda el reporte en results/; opcionalmente indica el nombre del JSON",
    )
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1:
        raise ValueError("--threshold debe estar entre 0 y 1")
    if not args.limits or any(limit <= 0 for limit in args.limits):
        raise ValueError("Todos los valores de --limits deben ser mayores que cero")

    limits = sorted(set(args.limits))
    if args.reset and qdrant.collection_exists(args.collection):
        qdrant.delete_collection(args.collection)
    index_documents(load_json(args.documents), args.collection, args.model)
    rows: list[dict[str, Any]] = []
    for limit in limits:
        metrics = evaluate(
            load_json(args.cases),
            args.collection,
            args.model,
            args.threshold,
            limit,
        )
        row = {"limit": limit, **metrics}
        rows.append(row)
        print(
            f"limit={limit:>2} selection={metrics['selection_score']:.4f} "
            f"recall={metrics['recall']:.4f} precision={metrics['precision']:.4f} "
            f"mrr={metrics['mrr']:.4f} "
            f"ood_rejection={metrics['out_of_domain_rejection_rate']:.4f}"
        )
    recommendation = max(
        rows,
        key=lambda row: (row["selection_score"], -row["limit"]),
    )
    print(
        f"\nSugerencia: usar LIMIT_QUERY={recommendation['limit']} "
        f"(selection_score={recommendation['selection_score']:.4f})."
    )
    print(
        "Criterio: maximiza selection_score y, si hay empate, "
        "elige el límite más bajo para reducir contexto y costo."
    )
    output_path = resolve_output_path(args.output, "limit.json")
    if output_path:
        output_path.write_text(
            json.dumps(
                {
                    "collection": args.collection,
                    "model": args.model,
                    "threshold": args.threshold,
                    "recommendation": {
                        "limit": recommendation["limit"],
                        "selection_score": recommendation["selection_score"],
                        "reason": (
                            "Máximo selection_score; en empate, menor límite "
                            "para reducir contexto y costo."
                        ),
                    },
                    "results": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
