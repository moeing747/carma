import { useEffect, useRef, useState } from 'react'

import type { FeedState } from './helpers'
import type { FeedReport } from './types'

const POLL_MS = 5_000

export interface FeedStatus {
  state: FeedState
  /** Feed age from the last successful report, or null when unknown. */
  ageSeconds: number | null
  /** Seconds since data was last seen, for the "unavailable" banner. */
  unavailableForSeconds: number | null
}

/** Polls /api/v1/feed every 5s; "no_data" and fetch failures both read as
 * unavailable. Starts as "connecting" so the first render never flashes a
 * degraded state before the first report has arrived. */
export function useFeedStatus(): FeedStatus {
  const [status, setStatus] = useState<FeedStatus>({
    state: 'connecting',
    ageSeconds: null,
    unavailableForSeconds: null,
  })
  const lastOkAtRef = useRef<number>(Date.now())

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      let next: FeedStatus
      try {
        const response = await fetch('/api/v1/feed')
        if (!response.ok) throw new Error(`feed ${response.status}`)
        const report = (await response.json()) as FeedReport
        if (report.state === 'fresh' || report.state === 'stale') {
          lastOkAtRef.current = Date.now()
          next = {
            state: report.state,
            ageSeconds: report.age_seconds ?? null,
            unavailableForSeconds: null,
          }
        } else {
          next = unavailable(lastOkAtRef.current)
        }
      } catch {
        next = unavailable(lastOkAtRef.current)
      }
      if (!cancelled) setStatus(next)
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

function unavailable(lastOkAtMs: number): FeedStatus {
  return {
    state: 'unavailable',
    ageSeconds: null,
    unavailableForSeconds: Math.max(0, (Date.now() - lastOkAtMs) / 1000),
  }
}
