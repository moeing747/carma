/**
 * Pure rules for the OPTIMIZE panel (unit-tested in optimize.test.ts).
 * The panel itself only wires these to fetch/state.
 */

import type { OptimizePlan } from './types'

/** Backend contract: headway re-spacing needs at least this many vehicles. */
export const MIN_OPTIMIZE_VEHICLES = 3

export type OptimizeGate =
  | { kind: 'ready'; line: string; count: number }
  | { kind: 'hint'; message: string }

/**
 * The RUN action is available only when exactly one line is filtered and it
 * currently has enough live vehicles; otherwise the panel shows why not.
 */
export function optimizeGate(
  activeLines: ReadonlySet<string>,
  countForLine: (line: string) => number,
): OptimizeGate {
  if (activeLines.size !== 1) {
    return { kind: 'hint', message: 'filter to one line to plan holds' }
  }
  const [line] = activeLines
  const count = countForLine(line)
  if (count < MIN_OPTIMIZE_VEHICLES) {
    return {
      kind: 'hint',
      message: `${line}: ${count} live vehicle${count === 1 ? '' : 's'} — needs ${MIN_OPTIMIZE_VEHICLES}+`,
    }
  }
  return { kind: 'ready', line, count }
}

/** Hold rendering: 0 stays quiet ("—"), otherwise "+M:SS". */
export function formatHold(holdSeconds: number): string {
  if (holdSeconds <= 0) return '—'
  const minutes = Math.floor(holdSeconds / 60)
  const seconds = holdSeconds % 60
  return `+${minutes}:${String(seconds).padStart(2, '0')}`
}

/** Headway spreads render as whole mono seconds ("142s"). */
export function formatSpread(seconds: number): string {
  return `${Math.round(seconds)}s`
}

/** Relative improvement of the spread in percent; null when before is 0. */
export function spreadImprovementPct(before: number, after: number): number | null {
  if (before <= 0) return null
  return Math.round((100 * (before - after)) / before)
}

/** Long GTFS trip ids get middle-truncated for the plan rows. */
export function shortTripId(tripId: string): string {
  if (tripId.length <= 12) return tripId
  return `${tripId.slice(0, 6)}…${tripId.slice(-5)}`
}

/** trip_id -> hold seconds for the map overlay (held vehicles only). */
export function holdsByTrip(plan: OptimizePlan): Map<string, number> {
  const holds = new Map<string, number>()
  for (const vehicle of plan.vehicles) {
    if (vehicle.hold_seconds > 0) holds.set(vehicle.trip_id, vehicle.hold_seconds)
  }
  return holds
}

/** Every trip in the plan, for the accent-ring overlay. */
export function planTripIds(plan: OptimizePlan): Set<string> {
  return new Set(plan.vehicles.map((vehicle) => vehicle.trip_id))
}
