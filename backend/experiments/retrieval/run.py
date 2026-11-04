"""Indexa datos controlados y evalúa thresholds de recuperación.

Ejemplo:
    python -m backend.experiments.retrieval.run --reset --model all-minilm
"""

from __future__ import annotations

import argparse
import json
import pprint
import sys
from pathlib import Path
from typing import Any, Iterable

project_root = str(Path(__file__).resolve().parents[3])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.experiments.retrieval.output import resolve_output_path
from backend.experiments.retrieval.repository import (
    COLLECTION,
    MODEL_EMBED,
    qdrant,
    retrieve,
    save_chunk,
)

DEFAULT_DIR = Path(__file__).resolve().parent
DATA_DIR = DEFAULT_DIR / "data"
DEFAULT_COLLECTION = f"{COLLECTION}_evaluation"
DEFAULT_THRESHOLDS = tuple(round(index / 100, 2) for index in range(40, 96, 5))


def load_json(path: Path) -> list[dict[str, Any]]:
    """Carga y valida un dataset JSON compuesto por una lista de objetos."""
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{path} debe contener una lista de objetos JSON")
    return value


def index_documents(
    documents: Iterable[dict[str, Any]], collection: str, model: str
) -> None:
    """Indexa todos los chunks declarados por el dataset de evaluación."""
    for document in documents:
        save_chunk(
            document_id=str(document["document_id"]),
            filename=str(document["filename"]),
            page_start=int(document["page_start"]),
            page_end=int(document["page_end"]),
            chunk_index=int(document["chunk_index"]),
            text=str(document["text"]),
            collection_name=collection,
            model=model,
        )


def precision_retrieved(retrieved: set[str], relevant: set[str]) -> float:
    """Calcula precisión usando nombres de archivo recuperados y esperados."""
    if not retrieved:
        return 0.0
    return len(retrieved & relevant) / len(retrieved)


def score_case(
    case: dict[str, Any], results: list[dict[str, Any]], limit: int
) -> dict[str, float]:
    """Calcula hit, precisión, recall y MRR para una consulta."""
    expected = set(case["relevant_files"])
    retrieved = [str(result["filename"]) for result in results[:limit]]
    retrieved_set = set(retrieved)
    hits = retrieved_set & expected
    reciprocal_rank = next(
        (
            1 / (index + 1)
            for index, filename in enumerate(retrieved)
            if filename in expected
        ),
        0.0,
    )
    if expected:
        recall = len(hits) / len(expected)
        precision = precision_retrieved(retrieved_set, expected)
        return {
            "in_domain": 1.0,
            "hit": float(bool(hits)),
            "precision": precision,
            "recall": recall,
            "mrr": reciprocal_rank,
            "rejection": 0.0,
        }
    return {
        "in_domain": 0.0,
        "hit": float(not retrieved),
        "precision": float(not retrieved),
        "recall": float(not retrieved),
        "mrr": 0.0,
        "rejection": float(not retrieved),
    }


def evaluate(
    cases: Iterable[dict[str, Any]],
    collection: str,
    model: str,
    threshold: float,
    limit: int,
) -> dict[str, float]:
    """Evalúa un threshold y combina métricas en un score de selección."""
    case_scores = []
    for case in cases:
        results = retrieve(
            str(case["question"]),
            limit=limit,
            score_threshold=threshold,
            collection_name=collection,
            model=model,
        )
        case_scores.append(score_case(case, results, limit))
    if not case_scores:
        raise ValueError("El dataset de consultas no puede estar vacío")
    count = len(case_scores)
    in_domain_scores = [score for score in case_scores if score["in_domain"]]
    out_of_domain_scores = [score for score in case_scores if not score["in_domain"]]

    def average(scores: list[dict[str, float]], metric: str) -> float:
        if not scores:
            return 0.0
        return round(sum(score[metric] for score in scores) / len(scores), 4)

    metrics = {
        "hit": average(case_scores, "hit"),
        "precision": average(in_domain_scores, "precision"),
        "recall": average(in_domain_scores, "recall"),
        "mrr": average(in_domain_scores, "mrr"),
        "in_domain_mrr": average(in_domain_scores, "mrr"),
        "out_of_domain_rejection_rate": average(out_of_domain_scores, "rejection"),
        "out_of_domain_false_positive_rate": (
            round(1 - average(out_of_domain_scores, "rejection"), 4)
            if out_of_domain_scores
            else 0.0
        ),
        "in_domain_queries": float(len(in_domain_scores)),
        "out_of_domain_queries": float(len(out_of_domain_scores)),
        "total_queries": float(count),
    }
    metrics["selection_score"] = round(
        0.30 * metrics["mrr"]
        + 0.25 * metrics["recall"]
        + 0.20 * metrics["precision"]
        + 0.25 * metrics["out_of_domain_rejection_rate"],
        4,
    )
    return metrics


def is_valid_candidate(
    metrics: dict[str, float],
    minimum_recall: float,
    minimum_rejection: float,
) -> bool:
    return (
        metrics["recall"] >= minimum_recall
        and metrics["out_of_domain_rejection_rate"] >= minimum_rejection
    )


def retrieve_cases(
    cases: Iterable[dict[str, Any]],
    collection: str,
    model: str,
    threshold: float,
    limit: int,
) -> list[dict[str, Any]]:
    report = []
    for case in cases:
        results = retrieve(
            str(case["question"]),
            limit=limit,
            score_threshold=threshold,
            collection_name=collection,
            model=model,
        )
        report.append(
            {
                "id": case.get("id"),
                "question": case["question"],
                "expected_files": case["relevant_files"],
                "retrieved": [
                    {
                        "filename": result["filename"],
                        "score": round(float(result["score"]), 6),
                        "page_start": result["page_start"],
                        "page_end": result["page_end"],
                    }
                    for result in results
                ],
            }
        )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--documents", type=Path, default=DATA_DIR / "documents" / "documents.json"
    )
    parser.add_argument(
        "--cases", type=Path, default=DATA_DIR / "cases" / "test_cases.json"
    )
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--model", default=MODEL_EMBED)
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument(
        "--thresholds", nargs="+", type=float, default=DEFAULT_THRESHOLDS
    )
    parser.add_argument("--minimum-recall", type=float, default=0.80)
    parser.add_argument("--minimum-rejection", type=float, default=0.80)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Borra la colección de evaluación antes de indexar",
    )
    parser.add_argument(
        "--output",
        nargs="?",
        const="",
        metavar="NOMBRE",
        help="Guarda el reporte en results/; opcionalmente indica el nombre del JSON",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if (
        args.limit <= 0
        or not args.thresholds
        or not 0 <= args.minimum_recall <= 1
        or not 0 <= args.minimum_rejection <= 1
    ):
        raise ValueError(
            "--limit debe ser mayor que cero, debe existir un threshold y los mínimos "
            "deben estar entre 0 y 1"
        )
    documents = load_json(args.documents)
    cases = load_json(args.cases)
    if args.reset and qdrant.collection_exists(args.collection):
        qdrant.delete_collection(args.collection)
    index_documents(documents, args.collection, args.model)

    print(f"Modelo: {args.model} | Colección: {args.collection} | k={args.limit}")
    print(
        "threshold | hit(in) | precision(in) | recall(in) | mrr(in) | "
        "ood_rejection | ood_false_positive | objective | valid"
    )
    candidates: list[tuple[float, dict[str, float], bool]] = []
    for threshold in args.thresholds:
        metrics = evaluate(cases, args.collection, args.model, threshold, args.limit)
        valid = is_valid_candidate(metrics, args.minimum_recall, args.minimum_rejection)
        candidates.append((threshold, metrics, valid))
        print(
            f"{threshold:.2f}      | {metrics['hit']:.4f}  | "
            f"{metrics['precision']:.4f}        | {metrics['recall']:.4f}     | "
            f"{metrics['mrr']:.4f}  | "
            f"{metrics['out_of_domain_rejection_rate']:.4f}          | "
            f"{metrics['out_of_domain_false_positive_rate']:.4f}             | "
            f"{metrics['selection_score']:.4f}    | {'yes' if valid else 'no'}"
        )
    valid_candidates = [
        (threshold, metrics) for threshold, metrics, valid in candidates if valid
    ]
    if valid_candidates:
        best = max(
            valid_candidates,
            key=lambda candidate: (
                candidate[1]["selection_score"],
                candidate[1]["mrr"],
                candidate[1]["precision"],
            ),
        )
        print(
            f"\nThreshold seleccionado entre candidatos válidos "
            f"(recall >= {args.minimum_recall:.2f}, "
            f"rechazo OOD >= {args.minimum_rejection:.2f})"
        )
    else:
        best = max(candidates, key=lambda candidate: candidate[1]["selection_score"])[:2]
        print(
            "\nADVERTENCIA: ningún threshold cumple los mínimos. "
            "Se muestra el mejor objetivo ponderado, pero no se recomienda usarlo aún."
        )
    print(f"Mejor threshold: {best[0]:.2f}")
    pprint.pprint(best[1], width=120)

    case_report = retrieve_cases(
        cases, args.collection, args.model, best[0], args.limit
    )
    print("\nArchivos recuperados con el mejor threshold:")
    for case in case_report:
        files = ", ".join(item["filename"] for item in case["retrieved"]) or "(ninguno)"
        print(f"- {case['id']}: {files}")
    output_path = resolve_output_path(args.output, "report.json")
    if output_path:
        output_path.write_text(
            json.dumps(
                {
                    "model": args.model,
                    "collection": args.collection,
                    "limit": args.limit,
                    "best_threshold": best[0],
                    "metrics": best[1],
                    "cases": case_report,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nReporte guardado en: {output_path}")


if __name__ == "__main__":
    main()
