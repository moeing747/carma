import { describe, expect, it } from 'vitest'

import { VehicleStore } from './store'
import type { PositionRow } from './types'

function row(overrides: Partial<PositionRow> = {}): PositionRow {
  return {
    trip_id: 'T1',
    route_id: 'R1',
    route_short_name: 'M1',
    headsign: 'Hauptbahnhof',
    lat: 52.52,
    lon: 13.4,
    bearing: 90,
    delay_seconds: 0,
    computed_at: '2026-07-17T00:00:00+00:00',
    ...overrides,
  }
}

describe('VehicleStore interpolation', () => {
  it('places a brand-new vehicle at its server position immediately', () => {
    const store = new VehicleStore()
    store.update([row()], 1_000)
    store.tick(1_000)
    const vehicle = store.vehicles.get('T1')!
    expect(vehicle.curLon).toBe(13.4)
    expect(vehicle.curLat).toBe(52.52)
    expect(vehicle.curBearing).toBe(90)
  })

  it('glides toward a new target instead of teleporting', () => {
    const store = new VehicleStore()
    store.update([row()], 1_000)
    store.tick(1_000)
    // 5s later the server reports the vehicle further east.
    store.update([row({ lon: 13.5 })], 6_000)
    store.tick(6_000)
    const vehicle = store.vehicles.get('T1')!
    expect(vehicle.curLon).toBe(13.4) // still where it was rendered
    store.tick(8_500) // halfway through the 5s glide window
    expect(vehicle.curLon).toBeCloseTo(13.45, 5)
    store.tick(60_000_000) // far past the window: clamped at the target
    expect(store.vehicles.get('T1')?.curLon ?? 13.5).toBeCloseTo(13.5, 5)
  })

  it('re-aims mid-glide from the currently rendered position', () => {
    const store = new VehicleStore()
    store.update([row()], 1_000)
    store.update([row({ lon: 13.5 })], 6_000)
    store.tick(8_500) // mid-glide at ~13.45
    store.update([row({ lon: 13.6 })], 8_500)
    const vehicle = store.vehicles.get('T1')!
    expect(vehicle.fromLon).toBeCloseTo(13.45, 5) // no jump back, no snap forward
    expect(vehicle.toLon).toBe(13.6)
  })

  it('interpolates bearing over the shortest arc and keeps it when null', () => {
    const store = new VehicleStore()
    store.update([row({ bearing: 350 })], 1_000)
    store.update([row({ bearing: 10 })], 6_000)
    store.tick(8_500)
    const vehicle = store.vehicles.get('T1')!
    expect(Math.min(vehicle.curBearing, 360 - vehicle.curBearing)).toBeLessThan(10)
    store.update([row({ bearing: null })], 11_000)
    expect(vehicle.toBearing).toBe(10) // dwell: heading preserved
  })

  it('fades and removes vehicles the stream stops mentioning', () => {
    const store = new VehicleStore()
    store.update([row()], 0)
    const versionBefore = store.membershipVersion
    store.tick(30_000)
    const vehicle = store.vehicles.get('T1')!
    expect(vehicle.fade).toBeGreaterThan(0)
    expect(vehicle.fade).toBeLessThan(1)
    store.tick(46_000)
    expect(store.vehicles.has('T1')).toBe(false)
    expect(store.membershipVersion).toBeGreaterThan(versionBefore)
  })

  it('notifies membership listeners on add and age-out removal', () => {
    const store = new VehicleStore()
    let notified = 0
    const unsubscribe = store.onMembershipChange(() => {
      notified += 1
    })
    store.update([row()], 0)
    expect(notified).toBe(1)
    store.tick(46_000) // age-out removal happens inside the rAF tick
    expect(notified).toBe(2)
    unsubscribe()
    store.update([row()], 47_000)
    expect(notified).toBe(2)
  })

  it('snapToTargets lands every glide at its target (tab refocus)', () => {
    const store = new VehicleStore()
    store.update([row()], 1_000)
    store.tick(1_000)
    // Tab hidden: rAF paused, but SSE updates keep re-aiming from the
    // frozen rendered position.
    store.update([row({ lon: 13.5, bearing: 180 })], 6_000)
    store.update([row({ lon: 13.6, bearing: 270 })], 11_000)
    store.snapToTargets()
    const vehicle = store.vehicles.get('T1')!
    expect(vehicle.curLon).toBe(13.6)
    expect(vehicle.fromLon).toBe(13.6)
    expect(vehicle.curBearing).toBe(270)
    // The next tick must not restart a sweep from the stale position.
    store.tick(12_000)
    expect(vehicle.curLon).toBe(13.6)
  })
})
