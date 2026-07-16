/** Wire types for the Carma API (backend/src/carma/entrypoints/api.py). */

export interface PositionRow {
  trip_id: string
  route_id: string
  route_short_name: string
  headsign: string
  lat: number
  lon: number
  bearing: number | null
  delay_seconds: number
  computed_at: string
}

export interface FeedReport {
  state: 'fresh' | 'stale' | 'unavailable' | 'no_data'
  fresh: boolean
  age_seconds?: number
  last_snapshot_at?: string
  last_entity_count?: number
  freshness_window_seconds?: number
}

export interface ScheduleStop {
  stop_id: string
  name: string
  stop_sequence: number
  arrival_seconds: number | null
  departure_seconds: number | null
  arrival: string | null
  departure: string | null
}

export interface TripSchedule {
  trip_id: string
  delay_seconds: number | null
  stops: ScheduleStop[]
}
