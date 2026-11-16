#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd -- "$here/../.." && pwd)
temporary="$here/../instancias/temp"
override="$temporary/compose.rnf-r1-build.yaml"

# Conserva los Dockerfiles del checkout y ajusta solo la tolerancia de pip para esta compilación.
prepare_build() {
  mkdir -p -- "$temporary"
  python3 - "$repo" "$override" <<'PY'
import json
from pathlib import Path
import sys

repo = Path(sys.argv[1])
override = Path(sys.argv[2])
lines = ["services:"]
for source, names in (
    ("Dockerfile", ("backend", "document-worker", "chat-worker")),
    ("db/Dockerfile", ("db-init",)),
):
    original = (repo / "backend" / source).read_text()
    marker = "\nCOPY pyproject.toml ./\n"
    if marker not in original:
        sys.exit(f"No se encontró el paso de dependencias en {source}.")
    adjusted = original.replace(
        marker, "\nENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=8\n" + marker, 1
    )
    for name in names:
        lines.extend((
            f"  {name}:",
            "    build:",
            "      dockerfile: !reset null",
            f"      dockerfile_inline: {json.dumps(adjusted, ensure_ascii=False)}",
        ))
override.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

compose=(docker compose --project-directory "$repo" --env-file /dev/null -p tutoria-rnf-r1
  -f "$repo/compose.yaml" -f "$repo/compose.dev-account.yaml"
  -f "$here/compose.rnf-r1.yaml")
if [[ "${TUTORIA_GPU:-0}" == 1 ]]; then
  compose+=(-f "$repo/compose.nvidia.yaml")
fi
case "${1:-up}" in
  up|config)
    prepare_build
    compose+=(-f "$override" --profile prod)
    if [[ "${1:-up}" == config ]]; then
      "${compose[@]}" config --quiet
    else
      "${compose[@]}" up -d --build --wait --wait-timeout 600
    fi
    ;;
  ps) "${compose[@]}" --profile prod ps -a ;;
  logs) "${compose[@]}" --profile prod logs --tail=60 db-init backend litellm ollama document-worker chat-worker ;;
  down) "${compose[@]}" --profile prod down ;;
  *) echo 'Uso: bash arrancar-rag.sh [up|ps|logs|down|config]' >&2; exit 2 ;;
esac
