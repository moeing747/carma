#!/usr/bin/env bash
# Bring up the Carma backend on a local kind cluster (the Kubernetes deploy
# path; see infra/k8s/README.md for what is demo-grade and why).
#
#   scripts/k8s-up.sh          create/reuse the cluster, deploy, port-forward
#
# The realtime pipeline runs fully in-cluster; /api/v1/positions stays empty
# unless the static GTFS is loaded (deliberately out of scope for the kind
# demo -- see infra/k8s/README.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="carma"
IMAGE="carma-backend:dev"
NS="carma"
# 8001 by default so the kind deployment can run alongside the compose
# stack (which publishes the API on 8000) without ambiguity over who answers.
API_LOCAL_PORT="${CARMA_K8S_PORT:-8001}"

for tool in docker kind kubectl; do
  command -v "$tool" >/dev/null || { echo "missing '$tool' (brew install $tool)" >&2; exit 1; }
done
docker version >/dev/null 2>&1 || { echo "Docker daemon is not running" >&2; exit 1; }

echo "==> building $IMAGE"
docker build -q -t "$IMAGE" "$ROOT/backend" >/dev/null

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "==> reusing kind cluster '$CLUSTER'"
else
  echo "==> creating kind cluster '$CLUSTER'"
  kind create cluster --name "$CLUSTER" --wait 120s
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

echo "==> loading image into the cluster"
kind load docker-image "$IMAGE" --name "$CLUSTER"

echo "==> applying manifests"
# The namespace must exist before the (non-kustomized) secret template can
# land in it; the kustomization re-applies it idempotently afterwards.
kubectl apply -f "$ROOT/infra/k8s/namespace.yaml"
kubectl apply -f "$ROOT/infra/k8s/secret.example.yaml"
kubectl apply -k "$ROOT/infra/k8s"

echo "==> waiting for migration job"
kubectl -n "$NS" wait --for=condition=complete "job/carma-migrate" --timeout=180s

echo "==> waiting for services"
kubectl -n "$NS" rollout status deployment/api --timeout=180s
for d in poller consumer projector; do
  kubectl -n "$NS" rollout status "deployment/$d" --timeout=180s || {
    # A few early restarts on a cold cluster are the restart-with-backoff
    # ordering mechanism, not a failure; report and continue.
    echo "    ($d still settling; check: kubectl -n $NS get pods)"
  }
done

if [ -f "$ROOT/.k8s-pf.pid" ] && kill -0 "$(cat "$ROOT/.k8s-pf.pid")" 2>/dev/null; then
  echo "==> port-forward already running (pid $(cat "$ROOT/.k8s-pf.pid"))"
else
  echo "==> port-forwarding svc/api -> localhost:$API_LOCAL_PORT"
  kubectl -n "$NS" port-forward svc/api "$API_LOCAL_PORT:8000" >"$ROOT/.k8s-pf.log" 2>&1 &
  echo $! > "$ROOT/.k8s-pf.pid"
fi

echo -n "==> waiting for API health "
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:$API_LOCAL_PORT/health" >/dev/null 2>&1; then ok=1 && break; fi
  echo -n "."
  sleep 2
done
echo
[ "${ok:-0}" -eq 1 ] || { echo "API not healthy; see kubectl -n $NS get pods and .k8s-pf.log" >&2; exit 1; }

curl -s "http://localhost:$API_LOCAL_PORT/api/v1/feed" || true
echo
echo "==> up. API: http://localhost:$API_LOCAL_PORT · pods: kubectl -n $NS get pods"
echo "    tear down with scripts/k8s-down.sh"
