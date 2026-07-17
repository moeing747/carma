import { lerpBearing } from './helpers'
import type { PositionRow } from './types'

/**
 * Client-side position store + interpolation.
 *
 * Each vehicle keeps the previous rendered state (`from*`) and the latest
 * server state (`to*`). When a server row arrives, `from*` is set to the
 * *currently rendered* values, so a new target never teleports the marker —
 * it re-aims the glide from wherever the vehicle is drawn right now. The
 * animation loop calls tick(now) once per frame, which mutates `cur*` in
 * place (no per-vehicle allocation per frame).
 *
 * Removal: the projector rewrites every active row each ~5s tick, so every
 * live vehicle is re-sent over SSE within seconds. A vehicle that has not
 * been mentioned for FADE_START_MS starts fading and is dropped at
 * REMOVE_MS — this covers both ended trips (the server deletes the row and
 * simply stops mentioning it) and vehicles scrolled out of the delta stream.
 */

export interface Vehicle {
  tripId: string
  routeId: string
  line: string
  headsign: string
  delaySeconds: number
  computedAtMs: number
  lastSeenMs: number
  fromLon: number
  fromLat: number
  fromBearing: number
  fromTimeMs: number
  toLon: number
  toLat: number
  toBearing: number
  toTimeMs: number
  curLon: number
  curLat: number
  curBearing: number
  /** 0..1, driven by age; the icon layer multiplies its alpha with this. */
  fade: number
}

const FADE_START_MS = 25_000
const REMOVE_MS = 45_000
/** Glide duration bounds; the observed inter-update gap is used when sane.
 * The stretch deliberately overshoots the expected gap: aiming to arrive
 * exactly when the next update is due makes the whole fleet land and freeze
 * in unison whenever an update runs late. Overshooting keeps markers
 * mid-glide when the re-aim arrives (a seamless bend), trading ~1s of
 * display latency for uninterrupted motion. */
const MIN_GLIDE_MS = 1_000
const MAX_GLIDE_MS = 10_000
const DEFAULT_GLIDE_MS = 6_000
const GLIDE_STRETCH = 1.25

export class VehicleStore {
  readonly vehicles = new Map<string, Vehicle>()
  /** Bumped whenever the set of vehicles changes (add/remove). */
  membershipVersion = 0
  private readonly membershipListeners = new Set<() => void>()

  /** Notify on every membership bump (add/remove); returns unsubscribe.
   * Removal happens inside the rAF tick, so React state that must react to
   * it (e.g. deselecting a removed vehicle) subscribes here rather than
   * waiting for the next SSE batch. */
  onMembershipChange(listener: () => void): () => void {
    this.membershipListeners.add(listener)
    return () => this.membershipListeners.delete(listener)
  }

  update(rows: readonly PositionRow[], receivedAtMs: number): void {
    for (const row of rows) {
      const existing = this.vehicles.get(row.trip_id)
      if (existing === undefined) {
        const bearing = row.bearing ?? 0
        this.vehicles.set(row.trip_id, {
          tripId: row.trip_id,
          routeId: row.route_id,
          line: row.route_short_name,
          headsign: row.headsign,
          delaySeconds: row.delay_seconds,
          computedAtMs: Date.parse(row.computed_at),
          lastSeenMs: receivedAtMs,
          fromLon: row.lon,
          fromLat: row.lat,
          fromBearing: bearing,
          fromTimeMs: receivedAtMs,
          toLon: row.lon,
          toLat: row.lat,
          toBearing: bearing,
          toTimeMs: receivedAtMs,
          curLon: row.lon,
          curLat: row.lat,
          curBearing: bearing,
          fade: 1,
        })
        this.bumpMembership()
        continue
      }
      const glideMs = clampGlide((receivedAtMs - existing.lastSeenMs) * GLIDE_STRETCH)
      existing.fromLon = existing.curLon
      existing.fromLat = existing.curLat
      existing.fromBearing = existing.curBearing
      existing.fromTimeMs = receivedAtMs
      existing.toLon = row.lon
      existing.toLat = row.lat
      // A dwelling vehicle reports no bearing; keep the last known heading.
      existing.toBearing = row.bearing ?? existing.toBearing
      existing.toTimeMs = receivedAtMs + glideMs
      existing.delaySeconds = row.delay_seconds
      existing.headsign = row.headsign
      existing.computedAtMs = Date.parse(row.computed_at)
      existing.lastSeenMs = receivedAtMs
      existing.fade = 1
    }
  }

  /** Advance every vehicle to `nowMs`; returns true when membership changed. */
  tick(nowMs: number): boolean {
    let removed = false
    for (const vehicle of this.vehicles.values()) {
      const age = nowMs - vehicle.lastSeenMs
      if (age > REMOVE_MS) {
        this.vehicles.delete(vehicle.tripId)
        removed = true
        continue
      }
      vehicle.fade =
        age <= FADE_START_MS ? 1 : 1 - (age - FADE_START_MS) / (REMOVE_MS - FADE_START_MS)
      const span = vehicle.toTimeMs - vehicle.fromTimeMs
      const f = span <= 0 ? 1 : Math.min(Math.max((nowMs - vehicle.fromTimeMs) / span, 0), 1)
      vehicle.curLon = vehicle.fromLon + (vehicle.toLon - vehicle.fromLon) * f
      vehicle.curLat = vehicle.fromLat + (vehicle.toLat - vehicle.fromLat) * f
      vehicle.curBearing = lerpBearing(vehicle.fromBearing, vehicle.toBearing, f)
    }
    if (removed) this.bumpMembership()
    return removed
  }

  /** Snap every glide to its target (from = cur = to).
   *
   * Called on tab refocus: while the rAF loop was paused, SSE updates kept
   * re-aiming glides from the frozen cur* values, so resuming would sweep
   * markers across the map for up to MAX_GLIDE_MS. */
  snapToTargets(): void {
    for (const vehicle of this.vehicles.values()) {
      vehicle.fromLon = vehicle.curLon = vehicle.toLon
      vehicle.fromLat = vehicle.curLat = vehicle.toLat
      vehicle.fromBearing = vehicle.curBearing = vehicle.toBearing
      vehicle.fromTimeMs = vehicle.toTimeMs
    }
  }

  private bumpMembership(): void {
    this.membershipVersion += 1
    for (const listener of this.membershipListeners) listener()
  }
}

function clampGlide(observedGapMs: number): number {
  if (!Number.isFinite(observedGapMs) || observedGapMs <= 0) return DEFAULT_GLIDE_MS
  return Math.min(Math.max(observedGapMs, MIN_GLIDE_MS), MAX_GLIDE_MS)
}
