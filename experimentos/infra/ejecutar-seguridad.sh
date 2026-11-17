#!/usr/bin/env bash
set -euo pipefail

infra=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd -- "$infra/../.." && pwd)
results="$repo/experimentos/resultados"
mkdir -p -- "$results"
output=$(mktemp -d "$results/rnf-seguridad-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXXXX")
project=$(basename "$output" | tr '[:upper:]' '[:lower:]')
compose=(docker compose --env-file /dev/null --project-directory "$repo"
  -f "$repo/compose.yaml" --project-name "$project" --profile test)

{
  printf 'inicio_utc=%s\n' "$(date -u +%FT%TZ)"
  printf 'proyecto_compose=%s\n' "$project"
  printf 'revision=%s\n' "$(git -C "$repo" rev-parse HEAD)"
  printf 'diff_versionado_sha256=%s\n' "$(git -C "$repo" diff --binary HEAD | sha256sum | cut -d ' ' -f 1)"
  printf '\n--- git status --porcelain ---\n'
  git -C "$repo" status --porcelain
} > "$output/entorno.txt"

phase=construccion
cleanup() {
  local status=$? teardown=0
  "${compose[@]}" down --volumes --rmi local > "$output/limpieza.log" 2>&1 || teardown=$?
  {
    printf 'fase_final=%s\n' "$phase"
    printf 'codigo_fase=%s\n' "$status"
    printf 'codigo_limpieza=%s\n' "$teardown"
  } > "$output/ejecucion.txt"
  printf 'Evidencia RNF-S-1 a S-4: %s\n' "$output"
  if (( teardown != 0 )); then
    printf 'El entorno de pruebas no se pudo limpiar. Revisa %s\n' "$output/limpieza.log" >&2
    if (( status == 0 )); then
      status=1
    fi
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'RNF-S-1 a S-4 · PostgreSQL temporal · proyecto %s\n' "$project"
"${compose[@]}" build test-db-init backend-test 2>&1 | tee "$output/construccion.log"

phase=integracion
"${compose[@]}" run --rm -T \
  --volume "$infra/reporte_pytest.py:/reporte_pytest.py:ro" \
  --volume "$output:/resultados" \
  backend-test python /reporte_pytest.py /resultados 2>&1 | tee "$output/pytest.log"
