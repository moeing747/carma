<!-- hero screenshot here -->

# Carma

Real-time Berlin transit monitoring and network-planning demo. Carma ingests the open VBB GTFS-RT feed (TripUpdates for ~6,600 trips), pushes it through a Kafka pipeline into a typed Python core, and renders live vehicles on a WebGL map. The feed publishes **no GPS**: vehicle positions are **derived** by combining the static schedule with live delays and projecting trip progress onto route geometry in PostGIS — which makes positions continuous by construction and smooth animation free. A mock optimization engine sits behind the same port a real Operations-Research engine would use; the engineering shell around it is the point, not the algorithm.

> **Status: under construction.** The hexagonal core, decoder, and map shell are in place; ingest loop, position projection, and the optimization panel are landing next.

## Quickstart

```sh
# infrastructure + API (schema migrations run automatically as a one-shot job)
docker compose -f infra/docker-compose.yml up -d

# backend, hackable
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest

# static GTFS: migrate (no-op after compose up) and load a feed zip
# VBB's feed: https://www.vbb.de/vbb-services/api-open-data/datensaetze/ (GTFS static)
export DATABASE_URL=postgres://carma:carma@localhost:5432/carma
.venv/bin/carma-migrate
.venv/bin/carma-load-gtfs path/to/gtfs.zip

# frontend
cd frontend
npm install
npm run dev
```

## Architecture

Hexagonal (ports and adapters), with the boundaries **enforced by tooling**, not convention:

- `backend/src/carma/domain` — pure models (`TripDelay`, `StopTimeEvent`, `VehiclePosition`). Stdlib only, imports no other layer.
- `backend/src/carma/application` — use cases and ports (`FeedSource`, `TripUpdatePublisher`, `TripDelayRepository`, `PositionProjector`, `OptimizationEngine`). Imports domain only.
- `backend/src/carma/adapters` — the edges; the GTFS-RT decoder is the only code that knows the wire format.
- `backend/src/carma/entrypoints` — Flask app factory, HTTP in.

[import-linter](https://import-linter.readthedocs.io/) carries a layered contract in `pyproject.toml` and runs in CI: a domain module importing an adapter fails the build. `mypy --strict`, `ruff`, and `pytest` gate every push alongside it.

Frontend: React + TypeScript + Vite, MapLibre GL basemap with deck.gl layers on top.

## Commit conventions

[Conventional Commits](https://www.conventionalcommits.org/) are enforced in CI (commitlint). Examples:

```
feat(ingest): poll VBB GTFS-RT feed
fix(api): return 503 while feed is stale
```
