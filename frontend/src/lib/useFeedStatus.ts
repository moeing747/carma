import { useEffect, useState } from 'react'

import type { FeedState } from './helpers'
import type { FeedReport } from './types'

const POLL_MS = 5_000
/** Downgrading to "unavailable" needs this many failed polls in a row: one
 * lost request (or a slow first poll during compose boot) must not flash
 * the red banner over an otherwise healthy feed. */
const FAILURES_BEFORE_UNAVAILABLE = 2

export interface FeedStatus {
  state: FeedState
  /** Feed age from the last successful report, or null when unknown. */
  ageSeconds: number | null
  /** Seconds since data was last seen, for the "unavailable" banner. */
  unavailableForSeconds: number | null
}

/**
 * Pure bookkeeping for the feed poll loop (unit-tested in
 * useFeedStatus.test.ts). Every poll takes a ticket via begin(); resolve()
 * drops responses that a newer poll has since superseded (polls have no
 * ordering guarantee — a slow old response must not overwrite a newer
 * state) and returns the next status, or null when the current one stands.
 */
export class FeedPollTracker {
  private newest = 0
  private failures = 0
  private lastOkAtMs: number

  constructor(nowMs: number) {
    this.lastOkAtMs = nowMs
  }

  begin(): number {
    this.newest += 1
    return this.newest
  }

  resolve(ticket: number, report: FeedReport | null, nowMs: number): FeedStatus | null {
    if (ticket !== this.newest) return null // superseded by a newer poll
    if (report !== null && (report.state === 'fresh' || report.state === 'stale')) {
      this.failures = 0
      this.lastOkAtMs = nowMs
      return {
        state: report.state,
        ageSeconds: report.age_seconds ?? null,
        unavailableForSeconds: null,
      }
    }
    this.failures += 1
    if (this.failures < FAILURES_BEFORE_UNAVAILABLE) return null
    return {
      state: 'unavailable',
      ageSeconds: null,
      unavailableForSeconds: Math.max(0, (nowMs - this.lastOkAtMs) / 1000),
    }
  }
}

/** Polls /api/v1/feed every 5s; "no_data" and fetch failures both read as
 * unavailable (after two consecutive failures). Starts as "connecting" so
 * the first render never flashes a degraded state before the first report
 * has arrived. */
export function useFeedStatus(): FeedStatus {
  const [status, setStatus] = useState<FeedStatus>({
    state: 'connecting',
    ageSeconds: null,
    unavailableForSeconds: null,
  })

  useEffect(() => {
    const tracker = new FeedPollTracker(Date.now())
    let cancelled = false
    const poll = async () => {
      const ticket = tracker.begin()
      let report: FeedReport | null = null
      try {
        const response = await fetch('/api/v1/feed', { signal: AbortSignal.timeout(POLL_MS) })
        if (response.ok) report = (await response.json()) as FeedReport
      } catch {
        // counted as a failed poll below
      }
      const next = tracker.resolve(ticket, report, Date.now())
      if (!cancelled && next !== null) setStatus(next)
    }
    void poll()
    const timer = setInterval(() => void poll(), POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  return status
}
