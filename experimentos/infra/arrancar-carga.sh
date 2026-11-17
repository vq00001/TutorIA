#!/usr/bin/env bash
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
TUTORIA_SOLO_CARGA=1 exec bash "$here/arrancar-rag.sh" "$@"
