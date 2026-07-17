import { describe, expect, it } from 'vitest'

import { FeedPollTracker } from './useFeedStatus'
import type { FeedReport } from './types'

function fresh(ageSeconds = 10): FeedReport {
  return { state: 'fresh', fresh: true, age_seconds: ageSeconds }
}

describe('FeedPollTracker', () => {
  it('maps a fresh report to the fresh state', () => {
    const tracker = new FeedPollTracker(0)
    const ticket = tracker.begin()
    expect(tracker.resolve(ticket, fresh(11.6), 1_000)).toEqual({
      state: 'fresh',
      ageSeconds: 11.6,
      unavailableForSeconds: null,
    })
  })

  it('drops a slow poll that resolves after a newer one (sequence guard)', () => {
    const tracker = new FeedPollTracker(0)
    const slow = tracker.begin()
    const newer = tracker.begin()
    expect(tracker.resolve(newer, fresh(), 1_000)?.state).toBe('fresh')
    // The stale response arrives late claiming the feed is gone: ignored.
    expect(tracker.resolve(slow, null, 2_000)).toBeNull()
  })

  it('keeps the current state on a single failed poll', () => {
    const tracker = new FeedPollTracker(0)
    expect(tracker.resolve(tracker.begin(), null, 1_000)).toBeNull()
  })

  it('turns unavailable only after two consecutive failures', () => {
    const tracker = new FeedPollTracker(0)
    expect(tracker.resolve(tracker.begin(), null, 5_000)).toBeNull()
    const second = tracker.resolve(tracker.begin(), null, 10_000)
    expect(second?.state).toBe('unavailable')
    expect(second?.unavailableForSeconds).toBe(10)
  })

  it('a success between failures resets the failure count', () => {
    const tracker = new FeedPollTracker(0)
    expect(tracker.resolve(tracker.begin(), null, 1_000)).toBeNull()
    expect(tracker.resolve(tracker.begin(), fresh(), 2_000)?.state).toBe('fresh')
    // The next lone failure is again not enough to downgrade.
    expect(tracker.resolve(tracker.begin(), null, 3_000)).toBeNull()
  })

  it('counts unavailable-time from the last successful report', () => {
    const tracker = new FeedPollTracker(0)
    tracker.resolve(tracker.begin(), fresh(), 4_000)
    tracker.resolve(tracker.begin(), null, 9_000)
    const status = tracker.resolve(tracker.begin(), null, 14_000)
    expect(status?.unavailableForSeconds).toBe(10)
  })

  it('treats no_data like a failure (needs two in a row)', () => {
    const tracker = new FeedPollTracker(0)
    const noData: FeedReport = { state: 'no_data', fresh: false }
    expect(tracker.resolve(tracker.begin(), noData, 1_000)).toBeNull()
    expect(tracker.resolve(tracker.begin(), noData, 6_000)?.state).toBe('unavailable')
  })
})
