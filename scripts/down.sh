#!/usr/bin/env bash
# Stop the Carma demo.
#
#   scripts/down.sh      stop web server + containers (data volumes kept)
#   scripts/down.sh -v   also wipe the Kafka/PostGIS volumes (full reset;
#                        next up.sh re-downloads and re-loads the GTFS)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/infra/docker-compose.yml")

if [ -f "$ROOT/.web.pid" ]; then
  pid="$(cat "$ROOT/.web.pid")"
  if kill -0 "$pid" 2>/dev/null; then
    echo "==> stopping web dev server (pid $pid)"
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$ROOT/.web.pid"
fi

if [ "${1:-}" = "-v" ]; then
  echo "==> compose down (wiping volumes)"
  "${COMPOSE[@]}" down -v
else
  echo "==> compose down (volumes kept)"
  "${COMPOSE[@]}" down
fi
