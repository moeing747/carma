import { memo, useEffect, useState } from 'react'

import {
  badgeColorsFor,
  berlinSecondsOfDay,
  delayColor,
  delayLabel,
  delayWord,
  serviceNowSeconds,
} from '../lib/helpers'
import type { VehicleStore } from '../lib/store'
import type { TripSchedule } from '../lib/types'

const SCHEDULE_WINDOW = 5

interface SelectedPanelProps {
  tripId: string
  store: VehicleStore
  onClose: () => void
}

export function SelectedPanel({ tripId, store, onClose }: SelectedPanelProps) {
  const [schedule, setSchedule] = useState<TripSchedule | null>(null)
  const [scheduleError, setScheduleError] = useState(false)
  // Per-frame tick so the technical block (bearing/position) tracks the
  // interpolated marker live, exactly like the comp specifies.
  const [, setTick] = useState(0)
  useEffect(() => {
    let rafId = 0
    const loop = () => {
      setTick((value) => value + 1)
      rafId = requestAnimationFrame(loop)
    }
    rafId = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(rafId)
  }, [])

  useEffect(() => {
    setSchedule(null)
    setScheduleError(false)
    const controller = new AbortController()
    fetch(`/api/v1/trips/${encodeURIComponent(tripId)}/schedule`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`schedule ${response.status}`)
        return response.json() as Promise<TripSchedule>
      })
      .then(setSchedule)
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === 'AbortError')) {
          setScheduleError(true)
        }
      })
    return () => controller.abort()
  }, [tripId])

  const vehicle = store.vehicles.get(tripId)
  if (vehicle === undefined) return null
  const badge = badgeColorsFor(vehicle.line)

  return (
    <div className="selected-panel">
      <div className="selected-head">
        <span className="selected-badge" style={{ background: badge.bg, color: badge.fg }}>
          {vehicle.line || '—'}
        </span>
        <div className="selected-towards">
          <span className="label">TOWARDS</span>
          <span className="headsign">{vehicle.headsign || '—'}</span>
        </div>
        <button className="close-btn" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      <div className="selected-delay">
        <span className="figure" style={{ color: delayColor(vehicle.delaySeconds) }}>
          {delayLabel(vehicle.delaySeconds)}
        </span>
        <span className="word">{delayWord(vehicle.delaySeconds)}</span>
      </div>
      <div className="selected-schedule">
        <span className="panel-title">SCHEDULE</span>
        <ScheduleStrip
          schedule={schedule}
          error={scheduleError}
          delaySeconds={vehicle.delaySeconds}
        />
      </div>
      <div className="selected-technical">
        <span className="panel-title">TECHNICAL</span>
        <div className="technical-rows">
          <div className="technical-row">
            <span className="key">trip_id</span>
            <span className="value">{vehicle.tripId}</span>
          </div>
          <div className="technical-row">
            <span className="key">bearing</span>
            <span className="value">{vehicle.curBearing.toFixed(1)}°</span>
          </div>
          <div className="technical-row">
            <span className="key">position</span>
            <span className="value">
              {vehicle.curLat.toFixed(5)}, {vehicle.curLon.toFixed(5)}
            </span>
          </div>
          <div className="technical-row">
            <span className="key">computed_at</span>
            <span className="value">
              {new Date(vehicle.computedAtMs).toISOString().slice(11, 19)}Z
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

// Memoized and self-ticking at 1 Hz: the parent re-renders every frame for
// the technical block, but a minutes-granularity strip has no business
// recomputing (and re-reading the Berlin wall clock) at 60 fps.
const ScheduleStrip = memo(function ScheduleStrip({
  schedule,
  error,
  delaySeconds,
}: {
  schedule: TripSchedule | null
  error: boolean
  delaySeconds: number
}) {
  const [nowSeconds, setNowSeconds] = useState(() => berlinSecondsOfDay(new Date()))
  useEffect(() => {
    const timer = setInterval(() => setNowSeconds(berlinSecondsOfDay(new Date())), 1_000)
    return () => clearInterval(timer)
  }, [])

  if (error) return <div className="schedule-note">schedule unavailable</div>
  if (schedule === null) return <div className="schedule-note">loading…</div>
  const stops = schedule.stops.filter(
    (stop) => stop.arrival_seconds !== null || stop.departure_seconds !== null,
  )
  if (stops.length === 0) return <div className="schedule-note">no scheduled stops</div>

  const first = stops[0].arrival_seconds ?? stops[0].departure_seconds ?? 0
  const last =
    stops[stops.length - 1].arrival_seconds ?? stops[stops.length - 1].departure_seconds ?? first
  const effectiveNow = serviceNowSeconds(nowSeconds, first, last)
  const isPast = (index: number) => {
    const stop = stops[index]
    // The live store delay updates every SSE batch; the fetched schedule's
    // delay is a snapshot from selection time and goes stale.
    const leaveAt = (stop.departure_seconds ?? stop.arrival_seconds ?? 0) + delaySeconds
    return leaveAt <= effectiveNow
  }
  let nextIndex = stops.findIndex((_, index) => !isPast(index))
  if (nextIndex === -1) nextIndex = stops.length // trip complete
  const start = Math.max(0, Math.min(nextIndex - 1, stops.length - SCHEDULE_WINDOW))
  const visible = stops.slice(start, start + SCHEDULE_WINDOW)

  return (
    <div className="schedule-rows">
      {visible.map((stop, offset) => {
        const index = start + offset
        const past = index < nextIndex
        const next = index === nextIndex
        return (
          <div className="stop-row" key={`${stop.stop_id}-${stop.stop_sequence}`}>
            <span className="stop-marker">
              <span className={`stop-dot${next ? ' next' : ''}`} />
              <span className={`stop-line${past ? ' past' : ''}`} />
            </span>
            <span className={`stop-name${next ? ' next' : past ? ' past' : ''}`}>{stop.name}</span>
            <span className={`stop-time${next ? ' next' : ''}`}>
              {stop.arrival ?? stop.departure ?? ''}
            </span>
          </div>
        )
      })}
    </div>
  )
})
