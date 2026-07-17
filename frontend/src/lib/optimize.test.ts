import { describe, expect, it } from 'vitest'

import {
  formatHold,
  formatSpread,
  holdsByTrip,
  optimizeGate,
  planTripIds,
  shortTripId,
  spreadImprovementPct,
} from './optimize'
import type { OptimizePlan } from './types'

const counts = new Map([
  ['M10', 6],
  ['U8', 2],
  ['100', 1],
])
const countFor = (line: string) => counts.get(line) ?? 0

describe('optimizeGate', () => {
  it('asks for a single line when none or several are filtered', () => {
    expect(optimizeGate(new Set(), countFor)).toEqual({
      kind: 'hint',
      message: 'filter to one line to plan holds',
    })
    expect(optimizeGate(new Set(['M10', 'U8']), countFor).kind).toBe('hint')
  })

  it('requires at least 3 live vehicles on the line', () => {
    expect(optimizeGate(new Set(['U8']), countFor)).toEqual({
      kind: 'hint',
      message: 'U8: 2 live vehicles — needs 3+',
    })
    expect(optimizeGate(new Set(['100']), countFor)).toEqual({
      kind: 'hint',
      message: '100: 1 live vehicle — needs 3+',
    })
  })

  it('is ready for a single sufficiently busy line', () => {
    expect(optimizeGate(new Set(['M10']), countFor)).toEqual({
      kind: 'ready',
      line: 'M10',
      count: 6,
    })
  })
})

describe('formatHold', () => {
  it('renders +M:SS for positive holds and a dash otherwise', () => {
    expect(formatHold(0)).toBe('—')
    expect(formatHold(45)).toBe('+0:45')
    expect(formatHold(90)).toBe('+1:30')
    expect(formatHold(300)).toBe('+5:00')
  })
})

describe('spread formatting', () => {
  it('renders whole seconds', () => {
    expect(formatSpread(142.4)).toBe('142s')
    expect(formatSpread(0)).toBe('0s')
  })

  it('computes the relative improvement', () => {
    expect(spreadImprovementPct(200, 50)).toBe(75)
    expect(spreadImprovementPct(200, 200)).toBe(0)
    expect(spreadImprovementPct(0, 0)).toBeNull()
  })
})

describe('shortTripId', () => {
  it('keeps short ids and middle-truncates long ones', () => {
    expect(shortTripId('27288_700')).toBe('27288_700')
    expect(shortTripId('123456789012345_700')).toBe('123456…5_700')
  })
})

const plan: OptimizePlan = {
  route_short_name: 'M10',
  direction: 'S+U Warschauer Str.',
  engine: 'cpsat',
  vehicles: [
    {
      trip_id: 'A',
      delay_seconds: 0,
      position_seconds: 900,
      hold_seconds: 0,
      next_stop_id: 'S3',
      next_stop_name: 'Gamma',
      headway_before_seconds: null,
      headway_after_seconds: null,
    },
    {
      trip_id: 'B',
      delay_seconds: 300,
      position_seconds: 600,
      hold_seconds: 120,
      next_stop_id: 'S2',
      next_stop_name: 'Beta',
      headway_before_seconds: 300,
      headway_after_seconds: 420,
    },
  ],
  summary: {
    vehicle_count: 2,
    headway_stddev_before_seconds: 200,
    headway_stddev_after_seconds: 80,
    max_hold_seconds: 300,
  },
}

describe('map overlay projections', () => {
  it('collects only held vehicles for the chips', () => {
    expect(holdsByTrip(plan)).toEqual(new Map([['B', 120]]))
  })

  it('collects every plan vehicle for the rings', () => {
    expect(planTripIds(plan)).toEqual(new Set(['A', 'B']))
  })
})
