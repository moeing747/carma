# Carma on Kubernetes

A Kustomize base mirroring `infra/docker-compose.yml`: the four backend
workloads (api, poller, consumer, projector) run the same image with different
commands, a one-shot `carma-migrate` Job applies the schema, and PostGIS and
Kafka run in-cluster.

**Demo-grade, deliberately.** PostGIS and Kafka are single-replica
StatefulSets with one PVC each and replication factor 1 — fine for kind or a
lab cluster, not a production posture. A real deployment would put managed
services or operators (CloudNativePG/Zalando for Postgres, Strimzi for Kafka)
behind the same `DATABASE_URL`/`KAFKA_BROKERS` env vars and delete those two
manifests; nothing in the app would change.

Compose's `depends_on` ordering has no Kubernetes equivalent and none is
faked: every process exits when its dependencies are unreachable and the
restart backoff brings it up once they are. Expect a few restarts in the first
minute of a cold cluster; that is the mechanism working, not a problem.

## Quickstart (kind)

```sh
kind create cluster --name carma

# the same image the compose file builds, loaded into the kind nodes
docker build -t carma-backend:dev backend
kind load docker-image carma-backend:dev --name carma

# the only secret: DB credentials (values are yours; nothing is committed).
# DATABASE_URL must agree with the other three values.
kubectl create namespace carma
kubectl -n carma create secret generic carma-db \
  --from-literal=POSTGRES_USER=carma \
  --from-literal=POSTGRES_PASSWORD=carma \
  --from-literal=POSTGRES_DB=carma \
  --from-literal=DATABASE_URL=postgres://carma:carma@postgis:5432/carma
# (equivalently: copy secret.example.yaml, fill it in, kubectl apply -f it)

kubectl apply -k infra/k8s
kubectl -n carma wait --for=condition=available deployment --all --timeout=300s

kubectl -n carma port-forward svc/api 8000:8000
curl localhost:8000/health
```

Static GTFS is loaded the same way as with compose, through the forwarded
port's Postgres equivalent — or simplest, exec into the cluster:
`kubectl -n carma port-forward svc/postgis 5432:5432`, then run
`carma-load-gtfs` locally against `localhost:5432`. Positions appear once the
static feed is loaded and the poller has delivered live delays.

The dashboard dev server proxies `/api` to `localhost:8000`, so
`npm run dev` in `frontend/` works against the port-forward unchanged.

## Validating without a cluster

CI validates the base clusterlessly; the same commands work locally:

```sh
kubectl kustomize infra/k8s | kubeconform -strict -summary \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
```

(`kubectl apply --dry-run=client` is not enough: it still contacts the API
server for discovery and schema validation.)
