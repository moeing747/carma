import { useEffect, useMemo, useRef, useState } from 'react'
import { WebMercatorViewport, type MapViewState, type PickingInfo } from '@deck.gl/core'
import { IconLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import DeckGL from '@deck.gl/react'
import { Map } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'

import { buildDartAtlas } from '../lib/icons'
import {
  badgeColorsFor,
  delayColor,
  delayColorRgb,
  delayLabel,
  type FeedState,
} from '../lib/helpers'
import { formatHold, holdsByTrip, planTripIds } from '../lib/optimize'
import type { Vehicle, VehicleStore } from '../lib/store'
import { trailingThrottle } from '../lib/throttle'
import type { OptimizePlan } from '../lib/types'

const INITIAL_VIEW_STATE: MapViewState = {
  longitude: 13.405,
  latitude: 52.52,
  zoom: 11,
}

const BASEMAP_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'

// Marker scales from the comp (dart path length 8.6 units; atlas draws it at
// 51.6/64 of the icon box): base 1.55, hover >= 2.6, selected 2.2.
const SIZE_BASE = 17
const SIZE_HOVER = 28
const SIZE_SELECTED = 24
const HALO_RADIUS = 11
const HALO_WIDTH = 1.4
const PLAN_RING_RADIUS = 13
const PLAN_RING_WIDTH = 1.2
const ACCENT_RGB: [number, number, number] = [115, 215, 0]

const TOOLTIP_OFFSET = 16
const TOOLTIP_WIDTH = 200
const TOOLTIP_HEIGHT = 76

const BOUNDS_THROTTLE_MS = 250
/** Positions are dead-reckoned from the last known delays while the feed is
 * degraded; the layer dims to this opacity factor to say so. */
const DEGRADED_DIM = 0.35

export type Bounds = [minLon: number, minLat: number, maxLon: number, maxLat: number]

interface HoverState {
  tripId: string
  x: number
  y: number
}

interface MapCanvasProps {
  store: VehicleStore
  activeLines: ReadonlySet<string>
  selectedId: string | null
  /** Advisory optimize plan to visualize; null hides the overlay. */
  plan: OptimizePlan | null
  feedState: FeedState
  /** Age of the last feed data in seconds, for the degraded tooltip. */
  feedAgeSeconds: number | null
  onSelect: (tripId: string | null) => void
  onBoundsChange: (bounds: Bounds) => void
}

interface PerfSample {
  fps: number
  frameMs: number
  vehicles: number
}

declare global {
  interface Window {
    __carmaPerf?: PerfSample
    /** Debug/verification handle to the live position store. */
    __carmaStore?: VehicleStore
  }
}

export function MapCanvas({
  store,
  activeLines,
  selectedId,
  plan,
  feedState,
  feedAgeSeconds,
  onSelect,
  onBoundsChange,
}: MapCanvasProps) {
  const [frame, setFrame] = useState(0)
  const [hover, setHover] = useState<HoverState | null>(null)
  const atlas = useMemo(() => buildDartAtlas(), [])
  const containerRef = useRef<HTMLDivElement>(null)
  const perfRef = useRef({ frames: 0, busyMs: 0, windowStart: performance.now() })

  // While the tab is hidden the rAF loop is paused but SSE keeps re-aiming
  // glides from frozen cur* values; snapping on refocus prevents an
  // up-to-MAX_GLIDE_MS sweep of every marker across the map.
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === 'visible') store.snapToTargets()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [store])

  // The animation loop: advance the interpolation store, then re-render so
  // the layers below rebuild from the mutated cur* fields.
  useEffect(() => {
    window.__carmaStore = store
    let rafId = 0
    const loop = () => {
      const started = performance.now()
      store.tick(Date.now())
      setFrame((value) => value + 1)
      const perf = perfRef.current
      perf.frames += 1
      perf.busyMs += performance.now() - started
      const windowMs = performance.now() - perf.windowStart
      if (windowMs >= 1000) {
        window.__carmaPerf = {
          fps: Math.round((perf.frames * 1000) / windowMs),
          frameMs: perf.busyMs / perf.frames,
          vehicles: store.vehicles.size,
        }
        perf.frames = 0
        perf.busyMs = 0
        perf.windowStart = performance.now()
      }
      rafId = requestAnimationFrame(loop)
    }
    rafId = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(rafId)
  }, [store])

  // Data array is rebuilt only when membership or the line filter changes;
  // per-frame motion mutates the vehicle objects inside it.
  const membership = store.membershipVersion
  const data = useMemo(() => {
    void membership // recompute key: the store mutates its map in place
    const all = Array.from(store.vehicles.values())
    if (activeLines.size === 0) return all
    return all.filter((vehicle) => activeLines.has(vehicle.line))
  }, [store, membership, activeLines])

  // Optimize overlay data: an accent ring on every vehicle the plan covers,
  // plus a hold chip ("HOLD +M:SS") beside each held vehicle. Chips were
  // chosen over ghost markers at the projected positions: at night-fleet
  // density ghosts double the marker count and read as extra vehicles,
  // while a labeled chip keeps the advice unambiguous.
  const planData = useMemo(() => {
    void membership // recompute key: the store mutates its map in place
    if (plan === null) return { rings: [] as Vehicle[], chips: [] as { vehicle: Vehicle; hold: number }[] }
    const rings: Vehicle[] = []
    for (const tripId of planTripIds(plan)) {
      const vehicle = store.vehicles.get(tripId)
      if (vehicle !== undefined) rings.push(vehicle)
    }
    const chips: { vehicle: Vehicle; hold: number }[] = []
    for (const [tripId, hold] of holdsByTrip(plan)) {
      const vehicle = store.vehicles.get(tripId)
      if (vehicle !== undefined) chips.push({ vehicle, hold })
    }
    return { rings, chips }
  }, [store, membership, plan])

  const haloData = useMemo(() => {
    void membership // recompute key: the store mutates its map in place
    const rows: { vehicle: Vehicle; accent: boolean }[] = []
    if (hover !== null && hover.tripId !== selectedId) {
      const vehicle = store.vehicles.get(hover.tripId)
      if (vehicle !== undefined) rows.push({ vehicle, accent: false })
    }
    if (selectedId !== null) {
      const vehicle = store.vehicles.get(selectedId)
      if (vehicle !== undefined) rows.push({ vehicle, accent: true })
    }
    return rows
  }, [store, membership, hover, selectedId])

  // Stale/unavailable feed: positions keep gliding from the last known
  // delays (dead reckoning), so the layer dims to flag reduced confidence.
  const feedDegraded = feedState === 'stale' || feedState === 'unavailable'

  const layers = [
    new IconLayer<Vehicle>({
      id: 'vehicles',
      data,
      iconAtlas: atlas.url,
      iconMapping: atlas.mapping,
      getIcon: () => 'dart',
      sizeUnits: 'pixels',
      getPosition: (vehicle) => [vehicle.curLon, vehicle.curLat],
      getAngle: (vehicle) => -vehicle.curBearing,
      getSize: (vehicle) =>
        vehicle.tripId === selectedId
          ? SIZE_SELECTED
          : vehicle.tripId === hover?.tripId
            ? SIZE_HOVER
            : SIZE_BASE,
      getColor: (vehicle) => {
        const [r, g, b] = delayColorRgb(vehicle.delaySeconds)
        return [r, g, b, Math.round(255 * vehicle.fade * (feedDegraded ? DEGRADED_DIM : 1))]
      },
      pickable: true,
      updateTriggers: {
        getPosition: frame,
        getAngle: frame,
        getColor: frame,
        getSize: [selectedId, hover?.tripId],
      },
    }),
    new ScatterplotLayer<{ vehicle: Vehicle; accent: boolean }>({
      id: 'halos',
      data: haloData,
      filled: false,
      stroked: true,
      radiusUnits: 'pixels',
      lineWidthUnits: 'pixels',
      getPosition: (row) => [row.vehicle.curLon, row.vehicle.curLat],
      getRadius: HALO_RADIUS,
      getLineWidth: HALO_WIDTH,
      getLineColor: (row) =>
        row.accent ? [...ACCENT_RGB, 255] : [...delayColorRgb(row.vehicle.delaySeconds), 255],
      updateTriggers: { getPosition: frame },
    }),
    new ScatterplotLayer<Vehicle>({
      id: 'optimize-rings',
      data: planData.rings,
      filled: false,
      stroked: true,
      radiusUnits: 'pixels',
      lineWidthUnits: 'pixels',
      getPosition: (vehicle) => [vehicle.curLon, vehicle.curLat],
      getRadius: PLAN_RING_RADIUS,
      getLineWidth: PLAN_RING_WIDTH,
      getLineColor: [...ACCENT_RGB, 165],
      updateTriggers: { getPosition: frame },
    }),
    new TextLayer<{ vehicle: Vehicle; hold: number }>({
      id: 'optimize-hold-chips',
      data: planData.chips,
      getPosition: (row) => [row.vehicle.curLon, row.vehicle.curLat],
      getText: (row) => `HOLD ${formatHold(row.hold)}`,
      getSize: 11,
      getColor: [...ACCENT_RGB, 255],
      getPixelOffset: [0, -26],
      fontFamily: 'IBM Plex Mono, ui-monospace, monospace',
      fontWeight: 600,
      background: true,
      getBackgroundColor: [15, 19, 27, 235],
      backgroundPadding: [7, 4, 7, 4],
      getBorderColor: [...ACCENT_RGB, 110],
      getBorderWidth: 1,
      updateTriggers: { getPosition: frame },
    }),
  ]

  // Leading + trailing throttle: the mount-time call always lands and the
  // final view state of a short interaction is emitted after the interval.
  const notifyBounds = useMemo(
    () =>
      trailingThrottle(BOUNDS_THROTTLE_MS, (viewState: MapViewState) => {
        const container = containerRef.current
        const viewport = new WebMercatorViewport({
          ...viewState,
          width: container?.clientWidth ?? window.innerWidth,
          height: container?.clientHeight ?? window.innerHeight,
        })
        onBoundsChange(viewport.getBounds() as Bounds)
      }),
    [onBoundsChange],
  )

  useEffect(() => {
    notifyBounds(INITIAL_VIEW_STATE)
    return () => notifyBounds.cancel()
  }, [notifyBounds])

  const hoveredVehicle = hover === null ? null : (store.vehicles.get(hover.tripId) ?? null)
  const tooltip = hover !== null && hoveredVehicle !== null && (
    <VehicleTooltip
      vehicle={hoveredVehicle}
      x={hover.x}
      y={hover.y}
      container={containerRef.current}
      feedDegraded={feedDegraded}
      feedAgeSeconds={feedAgeSeconds}
    />
  )

  return (
    <div className="map-shell" ref={containerRef}>
      <DeckGL
        initialViewState={INITIAL_VIEW_STATE}
        controller
        pickingRadius={6}
        layers={layers}
        onViewStateChange={({ viewState }) => notifyBounds(viewState as MapViewState)}
        onHover={(info: PickingInfo<Vehicle>) => {
          setHover(
            info.object === undefined || info.object === null
              ? null
              : { tripId: info.object.tripId, x: info.x, y: info.y },
          )
        }}
        onClick={(info: PickingInfo<Vehicle>) => {
          onSelect(info.object === undefined || info.object === null ? null : info.object.tripId)
        }}
        getCursor={({ isDragging }) =>
          isDragging ? 'grabbing' : hover !== null ? 'pointer' : 'grab'
        }
      >
        <Map mapStyle={BASEMAP_STYLE} />
      </DeckGL>
      {tooltip}
    </div>
  )
}

function VehicleTooltip({
  vehicle,
  x,
  y,
  container,
  feedDegraded,
  feedAgeSeconds,
}: {
  vehicle: Vehicle
  x: number
  y: number
  container: HTMLDivElement | null
  feedDegraded: boolean
  feedAgeSeconds: number | null
}) {
  const maxX = (container?.clientWidth ?? window.innerWidth) - TOOLTIP_WIDTH - 8
  const maxY = (container?.clientHeight ?? window.innerHeight) - TOOLTIP_HEIGHT - 8
  const left = Math.min(x + TOOLTIP_OFFSET, Math.max(maxX, 8))
  const top = Math.min(y + TOOLTIP_OFFSET, Math.max(maxY, 8))
  const agoSeconds = Math.max(0, Math.round((Date.now() - vehicle.computedAtMs) / 1000))
  // computed_at is stamped by the projector even while the poller is down;
  // when the feed is degraded, the honest age is the feed's, not the row's.
  const agoText = feedDegraded
    ? feedAgeSeconds === null
      ? 'feed down'
      : `feed ${Math.round(feedAgeSeconds)}s old`
    : `${agoSeconds}s ago`
  const badge = badgeStyle(vehicle.line)
  return (
    <div className="tooltip" style={{ left, top }}>
      <div className="tooltip-head">
        <span className="tooltip-badge" style={badge}>
          {vehicle.line || '—'}
        </span>
        <span className="tooltip-headsign">{vehicle.headsign}</span>
      </div>
      <div className="tooltip-foot">
        <span className="tooltip-delay" style={{ color: delayColor(vehicle.delaySeconds) }}>
          {delayLabel(vehicle.delaySeconds)}
        </span>
        <span className="tooltip-ago">{agoText}</span>
      </div>
    </div>
  )
}

function badgeStyle(line: string): { background: string; color: string } {
  const { bg, fg } = badgeColorsFor(line)
  return { background: bg, color: fg }
}
