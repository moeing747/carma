import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'

import { Banner } from './components/Banner'
import { EmptyState } from './components/EmptyState'
import { Header } from './components/Header'
import { LegendStats } from './components/LegendStats'
import { LineFilter, type LineStat } from './components/LineFilter'
import { MapCanvas, type Bounds } from './components/MapCanvas'
import { OptimizePanel } from './components/OptimizePanel'
import { SelectedPanel } from './components/SelectedPanel'
import { isOnTime, kindOf, type LineKind } from './lib/helpers'
import { PositionStream } from './lib/stream'
import { VehicleStore } from './lib/store'
import type { OptimizePlan } from './lib/types'
import { useFeedStatus } from './lib/useFeedStatus'

const KIND_ORDER: Record<LineKind, number> = {
  'u-bahn': 0,
  's-bahn': 1,
  tram: 2,
  bus: 3,
  other: 4,
}

export default function App() {
  const [store] = useState(() => new VehicleStore())
  // Bumped on every SSE batch (~3s); panel derivations key off it. The 60fps
  // motion lives inside MapCanvas and never re-renders the panels.
  const [dataTick, setDataTick] = useState(0)
  const [bounds, setBounds] = useState<Bounds | null>(null)
  const [activeLines, setActiveLines] = useState<ReadonlySet<string>>(new Set())
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [plan, setPlan] = useState<OptimizePlan | null>(null)
  // Optimistic until the stream proves otherwise, so a healthy boot never
  // flashes the reconnecting notice.
  const [streamConnected, setStreamConnected] = useState(true)
  const feed = useFeedStatus()

  // The plan visualizes one line's snapshot; changing the filter drops it.
  useEffect(() => {
    setPlan(null)
  }, [activeLines])

  useEffect(() => {
    const stream = new PositionStream('/api/v1/positions/stream', {
      onPositions: (rows, receivedAtMs) => {
        store.update(rows, receivedAtMs)
        setDataTick((tick) => tick + 1)
      },
      onConnectionChange: setStreamConnected,
    })
    stream.start()
    return () => stream.stop()
  }, [store])

  // Membership changes happen inside MapCanvas's rAF tick (age-out) as well
  // as per SSE batch; subscribing keeps deselection in step with removal.
  const membershipVersion = useSyncExternalStore(
    useCallback((onChange: () => void) => store.onMembershipChange(onChange), [store]),
    () => store.membershipVersion,
  )

  // Deselect once the selected vehicle leaves the store (removal or age-out).
  useEffect(() => {
    if (selectedId !== null && !store.vehicles.has(selectedId)) setSelectedId(null)
  }, [store, membershipVersion, selectedId])

  const lines = useMemo<LineStat[]>(() => {
    void dataTick // recompute key: the store mutates in place per SSE batch
    const groups = new Map<string, { count: number; delaySum: number }>()
    for (const vehicle of store.vehicles.values()) {
      const group = groups.get(vehicle.line)
      if (group === undefined) {
        groups.set(vehicle.line, { count: 1, delaySum: vehicle.delaySeconds })
      } else {
        group.count += 1
        group.delaySum += vehicle.delaySeconds
      }
    }
    // An active filter stays listed at zero even when its last vehicle ends
    // service — otherwise the row vanishes and the filter cannot be cleared.
    for (const name of activeLines) {
      if (!groups.has(name)) groups.set(name, { count: 0, delaySum: 0 })
    }
    return Array.from(groups, ([name, group]) => ({
      name,
      count: group.count,
      avgDelaySeconds: group.count === 0 ? null : group.delaySum / group.count,
    })).sort((a, b) => {
      const kindDelta = KIND_ORDER[kindOf(a.name)] - KIND_ORDER[kindOf(b.name)]
      if (kindDelta !== 0) return kindDelta
      return a.name.localeCompare(b.name, undefined, { numeric: true })
    })
  }, [store, dataTick, activeLines])

  const stats = useMemo(() => {
    void dataTick // recompute key: the store mutates in place per SSE batch
    let inView = 0
    let onTime = 0
    const lineDelays = new Map<string, { count: number; delaySum: number }>()
    for (const vehicle of store.vehicles.values()) {
      if (activeLines.size > 0 && !activeLines.has(vehicle.line)) continue
      if (bounds !== null) {
        const [minLon, minLat, maxLon, maxLat] = bounds
        if (
          vehicle.toLon < minLon ||
          vehicle.toLon > maxLon ||
          vehicle.toLat < minLat ||
          vehicle.toLat > maxLat
        ) {
          continue
        }
      }
      inView += 1
      if (isOnTime(vehicle.delaySeconds)) onTime += 1
      const group = lineDelays.get(vehicle.line)
      if (group === undefined) {
        lineDelays.set(vehicle.line, { count: 1, delaySum: vehicle.delaySeconds })
      } else {
        group.count += 1
        group.delaySum += vehicle.delaySeconds
      }
    }
    let worstLine: { name: string; avgDelaySeconds: number } | null = null
    for (const [name, group] of lineDelays) {
      const avg = group.delaySum / group.count
      if (worstLine === null || avg > worstLine.avgDelaySeconds) {
        worstLine = { name, avgDelaySeconds: avg }
      }
    }
    return {
      inView,
      onTimePct: inView === 0 ? null : (100 * onTime) / inView,
      worstLine,
    }
  }, [store, dataTick, bounds, activeLines])

  const toggleLine = useCallback((line: string) => {
    setActiveLines((current) => {
      const next = new Set(current)
      if (next.has(line)) {
        next.delete(line)
      } else {
        next.add(line)
      }
      return next
    })
  }, [])

  const handleBounds = useCallback((next: Bounds) => setBounds(next), [])

  const countForLine = useCallback(
    (line: string) => lines.find((stat) => stat.name === line)?.count ?? 0,
    [lines],
  )

  const loaded = dataTick > 0
  const total = store.vehicles.size
  // "No vehicles" is only a truthful diagnosis while both the feed and the
  // position stream are actually delivering.
  const showEmpty = loaded && streamConnected && feed.state === 'fresh' && stats.inView === 0

  // One banner at a time; a dead stream trumps feed staleness because it
  // freezes the map regardless of what the poller does.
  const banner = !streamConnected ? (
    <Banner state="stream" subSeconds={null} />
  ) : feed.state === 'unavailable' ? (
    <Banner state="unavailable" subSeconds={feed.unavailableForSeconds} />
  ) : feed.state === 'stale' ? (
    <Banner state="stale" subSeconds={feed.ageSeconds} />
  ) : null

  return (
    <div className="app">
      <MapCanvas
        store={store}
        activeLines={activeLines}
        selectedId={selectedId}
        plan={plan}
        feedState={feed.state}
        feedAgeSeconds={feed.state === 'stale' ? feed.ageSeconds : feed.unavailableForSeconds}
        onSelect={setSelectedId}
        onBoundsChange={handleBounds}
      />
      <Header feedState={feed.state} feedAgeSeconds={feed.ageSeconds} vehicleCount={total} />
      {banner}
      <LineFilter lines={lines} activeLines={activeLines} onToggle={toggleLine} />
      <LegendStats
        onTimePct={stats.onTimePct}
        inView={stats.inView}
        total={total}
        worstLine={stats.worstLine}
        streamConnected={streamConnected}
      />
      <OptimizePanel
        activeLines={activeLines}
        countForLine={countForLine}
        plan={plan}
        onPlan={setPlan}
      />
      {selectedId !== null && (
        <SelectedPanel tripId={selectedId} store={store} onClose={() => setSelectedId(null)} />
      )}
      {showEmpty && (
        <EmptyState
          filteredLineCount={activeLines.size}
          onClearFilters={() => setActiveLines(new Set())}
        />
      )}
    </div>
  )
}
