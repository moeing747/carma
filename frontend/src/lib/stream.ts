import type { PositionRow } from './types'

/**
 * EventSource client for /api/v1/positions/stream.
 *
 * The server's first event is a full snapshot (positions_since with no
 * cursor), after which each event carries only rows recomputed since the
 * client's cursor. The event id doubles as the cursor, so reconnects resume
 * where the stream left off: the connection is re-opened manually with
 * ?cursor=<last id> (the query-parameter twin of Last-Event-ID, which a
 * manually re-created EventSource would not send) under exponential backoff.
 */

export interface StreamHandlers {
  onPositions: (rows: PositionRow[], receivedAtMs: number) => void
  onConnectionChange?: (connected: boolean) => void
}

const BACKOFF_BASE_MS = 1_000
const BACKOFF_MAX_MS = 30_000
/** A connection only counts as healthy after its first positions event or
 * after surviving this long: the server sends 200 + headers before the
 * stream body runs, so an instantly-dying connection (e.g. DB down) must
 * not reset the backoff into a 1s retry hammer. */
const STABLE_AFTER_MS = 5_000

export class PositionStream {
  private source: EventSource | null = null
  private cursor: string | null = null
  private attempts = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private stableTimer: ReturnType<typeof setTimeout> | null = null
  private stopped = false
  private readonly url: string
  private readonly handlers: StreamHandlers

  constructor(url: string, handlers: StreamHandlers) {
    this.url = url
    this.handlers = handlers
  }

  start(): void {
    this.stopped = false
    this.open()
  }

  stop(): void {
    this.stopped = true
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    this.clearStableTimer()
    this.source?.close()
    this.source = null
  }

  private open(): void {
    const url =
      this.cursor === null ? this.url : `${this.url}?cursor=${encodeURIComponent(this.cursor)}`
    const source = new EventSource(url)
    this.source = source
    source.onopen = () => {
      this.stableTimer = setTimeout(() => this.markHealthy(), STABLE_AFTER_MS)
    }
    source.addEventListener('positions', (event: MessageEvent<string>) => {
      this.markHealthy()
      const payload = JSON.parse(event.data) as { positions: PositionRow[]; cursor: string }
      this.cursor = payload.cursor ?? event.lastEventId
      this.handlers.onPositions(payload.positions, Date.now())
    })
    source.onerror = () => {
      // Take over from EventSource's built-in retry: close and re-open with
      // the cursor in the URL so the resume survives a full re-connect.
      this.clearStableTimer()
      source.close()
      if (this.source === source) this.source = null
      this.handlers.onConnectionChange?.(false)
      if (this.stopped) return
      const delay = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** this.attempts)
      this.attempts += 1
      this.reconnectTimer = setTimeout(() => this.open(), delay)
    }
  }

  private markHealthy(): void {
    this.clearStableTimer()
    this.attempts = 0
    this.handlers.onConnectionChange?.(true)
  }

  private clearStableTimer(): void {
    if (this.stableTimer !== null) clearTimeout(this.stableTimer)
    this.stableTimer = null
  }
}
