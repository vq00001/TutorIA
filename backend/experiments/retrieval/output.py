from __future__ import annotations

from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def resolve_output_path(value: str | None, default_name: str) -> Path | None:
    """Resuelve nombres de reporte relativos dentro de `retrieval/results`."""
    if value is None:
        return None
    path = Path(default_name if value == "" else value)
    if not path.is_absolute():
        path = RESULTS_DIR / path.name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
