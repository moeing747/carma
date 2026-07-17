import { useCallback, useEffect, useMemo, useState } from 'react'

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
    })
    stream.start()
    return () => stream.stop()
  }, [store])

  // Deselect once the selected vehicle ages out of the store.
  useEffect(() => {
    if (selectedId !== null && !store.vehicles.has(selectedId)) setSelectedId(null)
  }, [store, dataTick, selectedId])

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
    return Array.from(groups, ([name, group]) => ({
      name,
      count: group.count,
      avgDelaySeconds: group.delaySum / group.count,
    })).sort((a, b) => {
      const kindDelta = KIND_ORDER[kindOf(a.name)] - KIND_ORDER[kindOf(b.name)]
      if (kindDelta !== 0) return kindDelta
      return a.name.localeCompare(b.name, undefined, { numeric: true })
    })
  }, [store, dataTick])

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
  const showEmpty = loaded && feed.state === 'fresh' && stats.inView === 0

  return (
    <div className="app">
      <MapCanvas
        store={store}
        activeLines={activeLines}
        selectedId={selectedId}
        plan={plan}
        onSelect={setSelectedId}
        onBoundsChange={handleBounds}
      />
      <Header feedState={feed.state} feedAgeSeconds={feed.ageSeconds} vehicleCount={total} />
      {feed.state === 'stale' && <Banner state="stale" subSeconds={feed.ageSeconds} />}
      {feed.state === 'unavailable' && (
        <Banner state="unavailable" subSeconds={feed.unavailableForSeconds} />
      )}
      <LineFilter lines={lines} activeLines={activeLines} onToggle={toggleLine} />
      <LegendStats
        onTimePct={stats.onTimePct}
        inView={stats.inView}
        total={total}
        worstLine={stats.worstLine}
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
      {showEmpty && <EmptyState />}
    </div>
  )
}
