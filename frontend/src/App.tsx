import type { MapViewState } from '@deck.gl/core'
import { ScatterplotLayer } from '@deck.gl/layers'
import DeckGL from '@deck.gl/react'
import { Map } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'

const INITIAL_VIEW_STATE: MapViewState = {
  longitude: 13.405,
  latitude: 52.52,
  zoom: 11,
}

const BASEMAP_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'

const FLIX_GREEN: [number, number, number] = [115, 215, 0]

interface Vehicle {
  id: string
  routeId: string
  position: [longitude: number, latitude: number]
}

// Placeholder until positions are derived from the VBB TripUpdate feed.
const PLACEHOLDER_VEHICLES: Vehicle[] = [
  { id: 'v-001', routeId: 'M10', position: [13.4132, 52.5219] },
  { id: 'v-002', routeId: 'X9', position: [13.3327, 52.5072] },
  { id: 'v-003', routeId: 'S41', position: [13.4019, 52.4731] },
]

export default function App() {
  const vehicleLayer = new ScatterplotLayer<Vehicle>({
    id: 'vehicles',
    data: PLACEHOLDER_VEHICLES,
    getPosition: (vehicle) => vehicle.position,
    getFillColor: FLIX_GREEN,
    radiusMinPixels: 6,
    radiusMaxPixels: 14,
    stroked: true,
    getLineColor: [10, 12, 14],
    lineWidthMinPixels: 2,
    pickable: true,
  })

  return (
    <div className="app">
      <header className="app-header">
        <span className="wordmark">Carma</span>
        <span className="tagline">Berlin transit, live</span>
        <span className="status-pill">feed: awaiting ingest</span>
      </header>
      <main className="map-shell">
        <DeckGL initialViewState={INITIAL_VIEW_STATE} controller layers={[vehicleLayer]}>
          <Map mapStyle={BASEMAP_STYLE} />
        </DeckGL>
      </main>
    </div>
  )
}
