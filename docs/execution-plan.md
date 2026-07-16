# Carma — execution plan

Phased plan; a phase is done when its tests pass, CI is green, and the demo still comes up with one command. Commit messages follow Conventional Commits (enforced in CI).

## Phase 0 — Scaffold

Repo skeleton: Python hexagonal backend (domain / application / adapters / entrypoints), import-linter layer contracts failing the build on violations, GTFS-RT decoder against a trimmed live-feed fixture, Flask app factory with /health, React + Vite + deck.gl shell with a Berlin map, Compose (Kafka KRaft + PostGIS + API) with healthcheck gating, backend Dockerfile, CI (ruff, mypy --strict, lint-imports, pytest, frontend build, commitlint).

**Done when:** all checks green locally and in CI on the first push.

## Phase 1 — Static GTFS foundation

- Loader for VBB static GTFS (agencies, routes, trips, stops, stop_times, shapes, calendar) into PostGIS; shapes as LineString geometries, stops as Points.
- Schema migrations (plain SQL, numbered).
- Trip/route/shape lookups exposed through a TripRepository port implementation.
- Service-day resolution (calendar + calendar_dates) for "which trips are active now".
- Tests: loader against a small handcrafted GTFS fixture; repository integration tests (Testcontainers PostGIS).

**Done when:** `carma load-gtfs <zip>` populates PostGIS and active-trip lookup answers correctly for the fixture.

## Phase 2 — Realtime ingestion pipeline

- Poller entrypoint: fetch VBB GTFS-RT (~30s cadence), decode to canonical TripDelay events, publish to Kafka topic `trip-updates` (partition key `routeId:tripId`).
- Consumer: persist latest TripDelay per trip (idempotent upsert; feed snapshots overlap by design).
- Staleness handling: mark feed unhealthy when no snapshot for >120s; /health reflects it.
- Tests: decoder edge cases, publisher/consumer integration (Testcontainers Kafka), replay of two overlapping fixture snapshots asserting idempotency.

**Done when:** compose up shows live Berlin delays landing in PostGIS continuously.

## Phase 3 — Position derivation engine

The VBB feed publishes no vehicle GPS; Carma computes positions.

- For each active trip: static schedule + latest TripDelay → estimated progress between stops → PostGIS projection onto the trip's shape → VehiclePosition (lat, lon, bearing, delay).
- Trips without realtime data derive positions from schedule alone (the map is never empty).
- Recompute loop (~5s) over active trips; positions exposed via `GET /api/v1/positions` (bbox-filtered) and an SSE stream of deltas.
- Tests: projection correctness on synthetic straight-line and curved shapes with known expected positions; schedule-only fallback; performance guard (all active VBB trips recomputed within the loop budget).

**Done when:** /positions returns plausible moving coordinates for live Berlin traffic, verified against a map spot-check.

## Phase 4 — Dashboard MVP

- SSE client feeding a position store; deck.gl layer with client-side interpolation between server ticks (vehicles glide, never teleport).
- Delay coloring, route and stop overlays, vehicle count + feed-health HUD, line filter.
- Performance target: all live VBB vehicles animating at 60fps, with a documented measurement.
- README hero: screenshot + short GIF above the fold; quickstart verified from a clean clone.

**Done when:** the vertical slice is publicly linkable and smooth.

## Phase 5 — Optimization shell

- OptimizationEngine port + a deliberately simple OR-Tools implementation: delay-aware vehicle re-assignment over a small line subset.
- Use case wiring optimization output back into domain models; `POST /api/v1/optimize` producing a plan diff.
- UI: "optimize" action visualizing the re-assignment diff on the map.
- README states explicitly: the algorithm is naive on purpose; the exhibit is the engineering shell around a swappable engine.
- Tests: engine behind a fake in use-case tests; one end-to-end optimize round-trip.

## Phase 6 — Ops polish

- K8s manifests (kind/k3s): Deployments, probes, Kustomize base; documented as the second deploy path.
- Terraform stub: one real module (object storage for GTFS static snapshots), plan-verified.
- Structured logging + a few counters.
- Final comment thinning, scaffolding-artifact removal, README architecture diagram.
