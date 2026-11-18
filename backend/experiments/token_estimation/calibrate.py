"""Calibra el ratio entre tokens del modelo y unidades léxicas aproximadas."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

import requests

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from backend.core.config import Config
from backend.rag.documents import iter_documents, read_document

DEFAULT_DOCUMENTS = Path(__file__).resolve().parent / "data"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def lexical_units(text: str) -> int:
    """Cuenta palabras y signos, la misma unidad base del chunker."""
    return len(re.findall(r"\w+|[^\w\s]", text))


def _prompt_eval_count(config: Config, text: str) -> tuple[int, str]:
    """Obtiene tokens del servicio y devuelve también el endpoint utilizado."""
    response = requests.post(
        f"{config.ollama_url}/api/embed",
        json={"model": config.model_embed, "input": text},
        timeout=300,
    )
    response.raise_for_status()
    body: Any = response.json()
    count = body.get("prompt_eval_count") if isinstance(body, dict) else None
    if isinstance(count, int) and count > 0:
        return count, "embed"

    response = requests.post(
        f"{config.ollama_url}/api/generate",
        json={
            "model": config.model_embed,
            "prompt": text,
            "stream": False,
            "think": False,
            "options": {"num_predict": 1},
        },
        timeout=300,
    )
    response.raise_for_status()
    body = response.json()
    count = body.get("prompt_eval_count") if isinstance(body, dict) else None
    if not isinstance(count, int) or count <= 0:
        raise ValueError("Ollama no devolvió prompt_eval_count en /api/embed ni en /api/generate")
    return count, "generate"


def collect_samples(path: Path, limit: int) -> list[tuple[str, str]]:
    """Selecciona muestras no vacías y deterministas de documentos soportados."""
    if not path.exists():
        raise FileNotFoundError(f"No existe la ruta de muestras: {path}")
    if path.is_file() and path.suffix.lower() not in {".md", ".markdown", ".txt"}:
        raise ValueError(f"La calibración solo admite .md, .markdown y .txt; recibió: {path.name}")
    files = iter_documents(path)
    if not files:
        raise ValueError(
            f"No se encontraron documentos soportados en {path}. "
            "Usa archivos .md, .markdown o .txt."
        )
    samples: list[tuple[str, str]] = []
    for file in files:
        for paragraph in re.split(r"\n\s*\n", read_document(file)):
            text = paragraph.strip()
            if lexical_units(text) >= 10:
                samples.append((str(file), text))
            if len(samples) >= limit:
                return samples
    return samples


def calibrate(config: Config, samples: list[tuple[str, str]]) -> dict[str, Any]:
    """Calcula ratios por muestra y estadísticas para configurar el chunker."""
    if not samples:
        raise ValueError(
            "No se encontraron muestras con al menos 10 unidades léxicas. "
            "Agrega textos más largos al directorio de muestras o aumenta --samples."
        )
    measurements = []
    endpoints: set[str] = set()
    for source, text in samples:
        base = lexical_units(text)
        model_tokens, endpoint = _prompt_eval_count(config, text)
        endpoints.add(endpoint)
        measurements.append(
            {
                "source": source,
                "lexical_units": base,
                "model_tokens": model_tokens,
                "ratio": model_tokens / base,
            }
        )
    ratios = [item["ratio"] for item in measurements]
    return {
        "model": config.model_embed,
        "samples": len(measurements),
        "endpoints": sorted(endpoints),
        "mean": statistics.mean(ratios),
        "median": statistics.median(ratios),
        "p95": sorted(ratios)[max(0, int(len(ratios) * 0.95) - 1)],
        "max": max(ratios),
        "recommended_ratio": max(ratios),
        "measurements": measurements,
    }


def main() -> None:
    """Ejecuta la calibración y muestra el ratio recomendado."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--documents",
        type=Path,
        default=DEFAULT_DOCUMENTS,
        help="Archivo o carpeta con .md/.markdown/.txt; por defecto usa data/samples.",
    )
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument(
        "--output",
        nargs="?",
        const="",
        metavar="NOMBRE",
        help="Guarda el resultado en results/; sin nombre usa calibration.json.",
    )
    args = parser.parse_args()
    if args.samples <= 0:
        raise ValueError("--samples debe ser mayor que cero")

    result = calibrate(Config.from_env(), collect_samples(args.documents, args.samples))
    print(
        f"modelo={result['model']} muestras={result['samples']} "
        f"mean={result['mean']:.4f} median={result['median']:.4f} "
        f"p95={result['p95']:.4f} max={result['max']:.4f}"
    )
    print(f"TOKEN_ESTIMATE_RATIO={result['recommended_ratio']:.4f}")
    if args.output is not None:
        output_path = Path(args.output or "calibration.json")
        if not output_path.is_absolute():
            output_path = RESULTS_DIR / output_path.name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Resultado guardado en: {output_path}")


if __name__ == "__main__":
    main()
