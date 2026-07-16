import { useEffect, useState } from 'react'

import { feedAgeLabel, type FeedState } from '../lib/helpers'

const FEED_META: Record<FeedState, { label: string; colorVar: string }> = {
  fresh: { label: 'FRESH', colorVar: 'var(--ok)' },
  stale: { label: 'STALE', colorVar: 'var(--warn)' },
  unavailable: { label: 'UNAVAILABLE', colorVar: 'var(--bad)' },
}

interface HeaderProps {
  feedState: FeedState
  feedAgeSeconds: number | null
  vehicleCount: number
}

export function Header({ feedState, feedAgeSeconds, vehicleCount }: HeaderProps) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])

  const iso = now.toISOString()
  const clock = iso.slice(11, 19)
  const clockDate = iso.slice(0, 10)
  const feed = FEED_META[feedState]

  return (
    <div className="header">
      <div className="wordmark">
        <span>Carm</span>
        <span className="accent">a</span>
        <span className="dot" />
      </div>
      <div className="clock">{clock}</div>
      <div className="clock-date">UTC {clockDate}</div>
      <div className="header-spacer" />
      <div className={`feed-pill ${feedState}`}>
        <span className="dot" style={{ background: feed.colorVar }} />
        <span className="label" style={{ color: feed.colorVar }}>
          {feed.label}
        </span>
        {feedAgeSeconds !== null && <span className="age">{feedAgeLabel(feedAgeSeconds)}</span>}
      </div>
      <div className="counter-pill">
        <span className="value">{vehicleCount}</span>
        <span className="unit">VEHICLES</span>
      </div>
    </div>
  )
}
