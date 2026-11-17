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
services=()
if [[ "${TUTORIA_SOLO_CARGA:-0}" == 1 ]]; then
  services=(webapp backend)
elif [[ "${TUTORIA_GPU:-0}" == 1 ]]; then
  compose+=(-f "$repo/compose.nvidia.yaml")
fi
# El respaldo y la restauración solo pueden operar sobre volúmenes de RNF-AC-2.
require_backup_volume() {
  if [[ ! "${TUTORIA_DB_VOLUME:-}" =~ ^tutoria-exp-rnf-ac-2-(cpu|gpu)-[0-9a-f]{32}(-restaurado)?$ ]]; then
    echo 'RNF-AC-2 requiere un volumen aislado generado por el lanzador.' >&2
    return 1
  fi
  if [[ "$1" == origin && "$TUTORIA_DB_VOLUME" == *-restaurado ]] ||
     [[ "$1" == target && "$TUTORIA_DB_VOLUME" != *-restaurado ]]; then
    echo 'El volumen no corresponde a la fase de respaldo o restauración.' >&2
    return 1
  fi
}

require_live_backup_volume() {
  require_backup_volume "$1" || return 1
  local container mounted
  container=$("${compose[@]}" ps -q db) || return 1
  [[ -n "$container" ]] || return 1
  mounted=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql"}}{{.Name}}{{end}}{{end}}' "$container") || return 1
  if [[ "$mounted" != "$TUTORIA_DB_VOLUME" ]]; then
    echo 'PostgreSQL no usa el volumen esperado. Operación cancelada.' >&2
    return 1
  fi
  printf '%s\n' "$container"
}

case "${1:-up}" in
  up|config)
    prepare_build
    compose+=(-f "$override" --profile prod)
    if [[ "${1:-up}" == config ]]; then
      "${compose[@]}" config --quiet
    else
      "${compose[@]}" up -d --build --wait --wait-timeout 600 "${services[@]}"
    fi
    ;;
  up-db)
    require_backup_volume target
    prepare_build
    compose+=(-f "$override" --profile prod)
    "${compose[@]}" up -d --wait --wait-timeout 600 db
    require_live_backup_volume target >/dev/null
    # Crea los roles que necesitan los propietarios y permisos del respaldo.
    "${compose[@]}" run --rm --no-deps --build db-init
    ;;
  respaldo)
    database=$(require_live_backup_volume origin) || exit 1
    docker exec "$database" sh -c \
      'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom'
    ;;
  restaurar)
    database=$(require_live_backup_volume target) || exit 1
    docker exec -i "$database" sh -c \
      'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --single-transaction --exit-on-error'
    ;;
  ps) "${compose[@]}" --profile prod ps -a ;;
  logs) "${compose[@]}" --profile prod logs --tail=60 db-init backend litellm ollama document-worker chat-worker ;;
  down) "${compose[@]}" --profile prod down ;;
  *) echo 'Uso: bash arrancar-rag.sh [up|ps|logs|down|config|up-db|respaldo|restaurar]' >&2; exit 2 ;;
esac
