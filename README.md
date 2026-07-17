![Carma dashboard: live Berlin vehicles, delay-colored, on a dark WebGL map](docs/media/dashboard.png)

# Carma

Real-time Berlin transit monitoring and network-planning demo. Carma ingests the open VBB GTFS-RT feed (TripUpdates for ~6,600 trips), pushes it through a Kafka pipeline into a typed Python core, and renders live vehicles on a WebGL map. The feed publishes **no GPS**: vehicle positions are **derived** by combining the static schedule with live delays and projecting trip progress onto route geometry in PostGIS — which makes positions continuous by construction and smooth animation free. A deliberately naive optimization engine sits behind the same port a real Operations-Research engine would use; the engineering shell around it is the point, not the algorithm.

> **Status: under construction.** The hexagonal core, decoder, realtime ingest pipeline (poller → Kafka → PostGIS), position derivation engine (`/api/v1/positions` + SSE stream), the live dashboard, and the optimization shell (`POST /api/v1/optimize` + map overlay) are in place; ops polish (K8s manifests, Terraform stub, structured logging) is landing next.

## Quickstart

```sh
# full stack: Kafka + PostGIS + API + realtime ingest (poller & consumer)
# + position projector; schema migrations run automatically as a one-shot job
docker compose -f infra/docker-compose.yml up -d

# live delays start landing within a poll cycle (~30s):
curl localhost:8000/api/v1/feed   # freshness + last snapshot
curl localhost:8000/health        # liveness, always 200; feed state in body

# backend, hackable
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest

# static GTFS: migrate (no-op after compose up) and load a feed zip
# VBB's feed: https://www.vbb.de/vbb-services/api-open-data/datensaetze/ (GTFS static)
export DATABASE_URL=postgres://carma:carma@localhost:5432/carma
.venv/bin/carma-migrate
.venv/bin/carma-load-gtfs path/to/gtfs.zip

# dashboard (dev server proxies /api to the compose API on localhost:8000)
cd frontend
npm install
npm run dev     # open http://localhost:5173
```

## Architecture

Hexagonal (ports and adapters), with the boundaries **enforced by tooling**, not convention:

- `backend/src/carma/domain` — pure models (`TripDelay`, `StopTimeEvent`, `VehiclePosition`) and pure rules (position interpolation semantics, service-day resolution). Stdlib only, imports no other layer.
- `backend/src/carma/application` — use cases and ports (`FeedSource`, `TripUpdatePublisher`, `TripDelayRepository`, `PositionRecomputeEngine`, `VehiclePositionReader`, `OptimizationEngine`). Imports domain only.
- `backend/src/carma/adapters` — the edges: GTFS-RT decoder, HTTP feed source, Kafka producer/consumer, PostGIS repositories and the set-based position engine. Only adapters know wire formats and SQL.
- `backend/src/carma/entrypoints` — Flask app factory (HTTP in) and the console scripts wiring adapters to use cases (`carma-poll-feed`, `carma-consume-trip-updates`, `carma-project-positions`, `carma-migrate`, `carma-load-gtfs`).

[import-linter](https://import-linter.readthedocs.io/) carries a layered contract in `pyproject.toml` and runs in CI: a domain module importing an adapter fails the build. `mypy --strict`, `ruff`, and `pytest` gate every push alongside it.

**Derived positions.** The VBB feed has no GPS, so `carma-project-positions` recomputes every active trip's position every ~5s: scheduled stop times plus the latest per-stop delay events give the trip's progress between two stops, and PostGIS projects that progress onto the trip's shape (`ST_LineLocatePoint`/`ST_LineInterpolatePoint`, bearing via `ST_Azimuth`) — one set-based SQL statement for the whole fleet, no per-trip loop. The semantics live as pure, exhaustively unit-tested code in `domain/positioning.py`; an integration test proves the SQL engine equivalent to that reference. Results land in the UNLOGGED `vehicle_positions` projection table (state that is rebuilt every tick needs no durability) and are served by `GET /api/v1/positions?bbox=…` plus an SSE delta stream at `/api/v1/positions/stream`. Trips without realtime data are positioned from schedule alone, so the map is never empty.

Frontend: React + TypeScript + Vite, MapLibre GL basemap with deck.gl layers on top.

## Dashboard

![Selected vehicle: schedule strip, live delay, technical block](docs/media/dashboard-selected.png)

The dashboard subscribes to the SSE delta stream (`/api/v1/positions/stream`, cursor-resumed reconnects with backoff) into a client-side store that keeps, per trip, the previous rendered state and the latest server state. A `requestAnimationFrame` loop interpolates each vehicle between the two (linear in lon/lat, shortest-arc in bearing), so markers glide between the ~5s server recomputes instead of teleporting; a new server row always re-aims the glide from the currently rendered position. Vehicles the stream stops mentioning fade out and are dropped. On top of that: delay-ramp coloring, a collapsible per-line filter, on-time/in-view/worst-line stats, feed-health pill and stale/unavailable banners driven by `/api/v1/feed`, and a per-vehicle panel with the trip's schedule strip (`/api/v1/trips/<id>/schedule`) marking past and next stops from the live delay.

**Performance.** The deck.gl `IconLayer` reuses its data array (rebuilt only when fleet membership or the line filter changes) and recomputes attributes per frame via `updateTriggers`; the interpolation tick mutates vehicle objects in place. Measured with Chromium on an Apple-silicon MacBook (120 Hz display), sampling frame-to-frame `requestAnimationFrame` deltas over 5s windows: the live night fleet (~250 vehicles) averaged 9.3 ms/frame (~108 fps, p95 13.3 ms); with the store inflated to 2,200 vehicles (daytime fleet size, refreshed through the same pipeline on the real 5s cadence) it averaged 8.4 ms/frame (~119 fps, p95 9.3 ms) — display-limited, with the interpolation tick itself at ~0.15 ms/frame. Numbers are from `window.__carmaPerf`, which the app maintains at runtime.

## Optimization shell

![Optimize panel: recommended holds evening out headways on one line](docs/media/optimize.png)

Domain models in, typed plan out, engine behind a port: `OptimizeLineHeadways` (application) gathers a line's live vehicles, places them on a pattern-time axis, and hands them to whatever stands behind the `OptimizationEngine` port — the HTTP endpoint, use case, and dashboard never know which. **The shipped algorithm is naive on purpose**: the exhibit is the engineering shell a real Operations-Research engine would drop into, not the OR itself.

The problem is delay-aware headway re-spacing (bus-bunching mitigation). `POST /api/v1/optimize` with `{"route_short_name": "M10"}` returns advisory hold times (0–300 s) at each vehicle's next stop for the line's busiest direction, chosen so projected headways even out — per-vehicle headway before/after plus the headway standard deviation the plan achieves. Nothing is applied anywhere; the dashboard's OPTIMIZE panel (visible with exactly one line filtered) draws the plan as accent rings and hold chips on the live map.

Two interchangeable engines prove the port honest, selected via `CARMA_OPTIMIZER`:

- `cpsat` (default) — a tiny [OR-Tools](https://developers.google.com/optimization) CP-SAT model, ~30 lines: minimize the maximum deviation from the mean headway, holds bounded, no overtaking, ties broken toward fewer held seconds (`adapters/optimize_cpsat.py`).
- `heuristic` — a solver-free greedy pass placing each follower one mean headway behind the vehicle ahead (`adapters/optimize_heuristic.py`).

Both are held to the same contract by shared property tests (holds within bounds, order preserved, spread never worse than doing nothing), and the pure headway math lives in `domain/headway.py`. Swapping in a real engine means implementing one `solve()` method.

## Commit conventions

[Conventional Commits](https://www.conventionalcommits.org/) are enforced in CI (commitlint). Examples:

```
feat(ingest): poll VBB GTFS-RT feed
fix(api): return 503 while feed is stale
```
