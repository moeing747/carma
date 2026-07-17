#!/usr/bin/env bash
# Bring up the full Carma demo: infrastructure, pipeline, and dashboard.
#
#   scripts/up.sh                 everything (compose stack + web dev server)
#   scripts/up.sh --backend-only  compose stack only (API on :8000)
#
# First boot downloads the VBB static GTFS (~73 MB) and loads it; override
# the source with CARMA_GTFS_ZIP=/path/to/GTFS.zip or CARMA_GTFS_URL=...
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/infra/docker-compose.yml")
API_URL="http://localhost:8000"
GTFS_URL="${CARMA_GTFS_URL:-https://www.vbb.de/fileadmin/user_upload/VBB/Dokumente/API-Datensaetze/gtfs-mastscharf/GTFS.zip}"

BACKEND_ONLY=0
[ "${1:-}" = "--backend-only" ] && BACKEND_ONLY=1

echo "==> compose up (build)"
"${COMPOSE[@]}" up -d --build

echo -n "==> waiting for API health at $API_URL/health "
for _ in $(seq 1 60); do
  if curl -sf "$API_URL/health" >/dev/null 2>&1; then healthy=1 && break; fi
  echo -n "."
  sleep 2
done
echo
[ "${healthy:-0}" -eq 1 ] || { echo "API did not become healthy; check: ${COMPOSE[*]} logs api" >&2; exit 1; }

stops="$("${COMPOSE[@]}" exec -T postgis sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM stops"' | tr -d '[:space:]')"
if [ "${stops:-0}" -eq 0 ]; then
  if [ -n "${CARMA_GTFS_ZIP:-}" ]; then
    zip="$CARMA_GTFS_ZIP"
    echo "==> loading static GTFS from $zip"
  else
    zip="$(mktemp -d)/gtfs.zip"
    echo "==> downloading VBB static GTFS (~73 MB)"
    curl -fL --progress-bar "$GTFS_URL" -o "$zip"
  fi
  echo "==> loading static GTFS (takes ~1-2 min for the full VBB dataset)"
  "${COMPOSE[@]}" run --rm -v "$zip:/tmp/gtfs.zip:ro" api carma-load-gtfs /tmp/gtfs.zip
else
  echo "==> static GTFS already loaded ($stops stops), skipping seed"
fi

echo "==> API:  $API_URL/health · $API_URL/api/v1/feed · $API_URL/api/v1/positions"

if [ "$BACKEND_ONLY" -eq 1 ]; then
  echo "==> backend only; start the dashboard with: cd frontend && npm run dev"
  exit 0
fi

cd "$ROOT/frontend"
[ -d node_modules ] || { echo "==> npm install"; npm install; }
if [ -f "$ROOT/.web.pid" ] && kill -0 "$(cat "$ROOT/.web.pid")" 2>/dev/null; then
  echo "==> web dev server already running (pid $(cat "$ROOT/.web.pid"))"
else
  echo "==> starting web dev server"
  nohup npm run dev >"$ROOT/.web.log" 2>&1 &
  echo $! > "$ROOT/.web.pid"
fi
echo -n "==> waiting for dashboard at http://localhost:5173 "
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:5173" >/dev/null 2>&1; then web=1 && break; fi
  echo -n "."
  sleep 1
done
echo
[ "${web:-0}" -eq 1 ] || { echo "dev server did not come up; see .web.log" >&2; exit 1; }
echo "==> dashboard: http://localhost:5173"
