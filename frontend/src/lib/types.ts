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

export interface OptimizeVehicle {
  trip_id: string
  delay_seconds: number
  position_seconds: number
  hold_seconds: number
  next_stop_id: string
  next_stop_name: string
  headway_before_seconds: number | null
  headway_after_seconds: number | null
}

export interface OptimizeSummary {
  vehicle_count: number
  headway_stddev_before_seconds: number
  headway_stddev_after_seconds: number
  max_hold_seconds: number
}

export interface OptimizePlan {
  route_short_name: string
  direction: string
  engine: string
  vehicles: OptimizeVehicle[]
  summary: OptimizeSummary
}
