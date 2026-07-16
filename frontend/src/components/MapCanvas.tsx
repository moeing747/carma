import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { WebMercatorViewport, type MapViewState, type PickingInfo } from '@deck.gl/core'
import { IconLayer, ScatterplotLayer } from '@deck.gl/layers'
import DeckGL from '@deck.gl/react'
import { Map } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'

import { buildDartAtlas } from '../lib/icons'
import { badgeColorsFor, delayColor, delayColorRgb, delayLabel } from '../lib/helpers'
import type { Vehicle, VehicleStore } from '../lib/store'

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
const ACCENT_RGB: [number, number, number] = [115, 215, 0]

const TOOLTIP_OFFSET = 16
const TOOLTIP_WIDTH = 200
const TOOLTIP_HEIGHT = 76

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
  onSelect,
  onBoundsChange,
}: MapCanvasProps) {
  const [frame, setFrame] = useState(0)
  const [hover, setHover] = useState<HoverState | null>(null)
  const atlas = useMemo(() => buildDartAtlas(), [])
  const containerRef = useRef<HTMLDivElement>(null)
  const lastBoundsNotify = useRef(0)
  const perfRef = useRef({ frames: 0, busyMs: 0, windowStart: performance.now() })

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
        return [r, g, b, Math.round(255 * vehicle.fade)]
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
  ]

  const notifyBounds = useCallback(
    (viewState: MapViewState) => {
      const now = performance.now()
      if (now - lastBoundsNotify.current < 250) return
      lastBoundsNotify.current = now
      const container = containerRef.current
      const viewport = new WebMercatorViewport({
        ...viewState,
        width: container?.clientWidth ?? window.innerWidth,
        height: container?.clientHeight ?? window.innerHeight,
      })
      onBoundsChange(viewport.getBounds() as Bounds)
    },
    [onBoundsChange],
  )

  useEffect(() => {
    notifyBounds(INITIAL_VIEW_STATE)
  }, [notifyBounds])

  const hoveredVehicle = hover === null ? null : (store.vehicles.get(hover.tripId) ?? null)
  const tooltip = hover !== null && hoveredVehicle !== null && (
    <VehicleTooltip
      vehicle={hoveredVehicle}
      x={hover.x}
      y={hover.y}
      container={containerRef.current}
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
}: {
  vehicle: Vehicle
  x: number
  y: number
  container: HTMLDivElement | null
}) {
  const maxX = (container?.clientWidth ?? window.innerWidth) - TOOLTIP_WIDTH - 8
  const maxY = (container?.clientHeight ?? window.innerHeight) - TOOLTIP_HEIGHT - 8
  const left = Math.min(x + TOOLTIP_OFFSET, Math.max(maxX, 8))
  const top = Math.min(y + TOOLTIP_OFFSET, Math.max(maxY, 8))
  const agoSeconds = Math.max(0, Math.round((Date.now() - vehicle.computedAtMs) / 1000))
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
        <span className="tooltip-ago">{agoSeconds}s ago</span>
      </div>
    </div>
  )
}

function badgeStyle(line: string): { background: string; color: string } {
  const { bg, fg } = badgeColorsFor(line)
  return { background: bg, color: fg }
}
