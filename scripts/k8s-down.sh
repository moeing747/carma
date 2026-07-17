#!/usr/bin/env bash
# Tear down the local kind deployment created by scripts/k8s-up.sh.
#
#   scripts/k8s-down.sh          delete the kind cluster (and everything in it)
#   scripts/k8s-down.sh --keep   stop the port-forward, keep the cluster
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="carma"

if [ -f "$ROOT/.k8s-pf.pid" ]; then
  pid="$(cat "$ROOT/.k8s-pf.pid")"
  if kill -0 "$pid" 2>/dev/null; then
    echo "==> stopping port-forward (pid $pid)"
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$ROOT/.k8s-pf.pid" "$ROOT/.k8s-pf.log"
fi

if [ "${1:-}" = "--keep" ]; then
  echo "==> cluster kept; redeploy with scripts/k8s-up.sh"
  exit 0
fi

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "==> deleting kind cluster '$CLUSTER'"
  kind delete cluster --name "$CLUSTER"
else
  echo "==> no kind cluster '$CLUSTER' found"
fi
