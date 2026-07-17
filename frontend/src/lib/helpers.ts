/**
 * Pure presentation rules from the design comp's behavioral spec.
 * Everything here is deterministic and unit-tested (helpers.test.ts).
 */

export type LineKind = 'u-bahn' | 's-bahn' | 'tram' | 'bus' | 'other'

export type FeedState = 'connecting' | 'fresh' | 'stale' | 'unavailable'

/** Delay ramp CSS custom-property values (kept in sync with index.css). */
export const RAMP = {
  early: '#4aa8ff',
  ontime: '#73D700',
  amber: '#ffcf3f',
  orange: '#ff8a3d',
  late: '#ff4d4d',
} as const

const RAMP_RGB: Record<keyof typeof RAMP, [number, number, number]> = {
  early: [74, 168, 255],
  ontime: [115, 215, 0],
  amber: [255, 207, 63],
  orange: [255, 138, 61],
  late: [255, 77, 77],
}

/** Spec: d < -30 early; d <= 60 on time; d <= 180 amber; d <= 300 orange; else late. */
export function delayBucket(delaySeconds: number): keyof typeof RAMP {
  if (delaySeconds < -30) return 'early'
  if (delaySeconds <= 60) return 'ontime'
  if (delaySeconds <= 180) return 'amber'
  if (delaySeconds <= 300) return 'orange'
  return 'late'
}

export function delayColor(delaySeconds: number): string {
  return RAMP[delayBucket(delaySeconds)]
}

export function delayColorRgb(delaySeconds: number): [number, number, number] {
  return RAMP_RGB[delayBucket(delaySeconds)]
}

/** Spec: |d| <= 30 -> "On time"; else sign + M:SS (e.g. "+3:00", "−1:05").
 * Fractional inputs (per-line averages) round to whole seconds first. */
export function delayLabel(delaySeconds: number): string {
  const rounded = Math.round(delaySeconds)
  if (Math.abs(rounded) <= 30) return 'On time'
  const sign = rounded < 0 ? '−' : '+'
  const magnitude = Math.abs(rounded)
  const minutes = Math.floor(magnitude / 60)
  const seconds = magnitude % 60
  return `${sign}${minutes}:${String(seconds).padStart(2, '0')}`
}

/** Spec: < -30 ahead; <= 60 on time; <= 300 behind; else severely delayed. */
export function delayWord(delaySeconds: number): string {
  if (delaySeconds < -30) return 'ahead of schedule'
  if (delaySeconds <= 60) return 'running on time'
  if (delaySeconds <= 300) return 'behind schedule'
  return 'severely delayed'
}

/** Spec: 'U'* u-bahn, 'S'* s-bahn, 'M'+digits tram, plain digits bus. */
export function kindOf(routeShortName: string): LineKind {
  const name = routeShortName.trim().toUpperCase()
  if (name.startsWith('U')) return 'u-bahn'
  if (name.startsWith('S')) return 's-bahn'
  if (/^M\d+$/.test(name)) return 'tram'
  if (/^\d+$/.test(name)) return 'bus'
  return 'other'
}

/** KIND badge colors from the spec; 'other' (RE/RB/FEX/named lines) is a
 * neutral fallback the comp did not define. */
export function kindColors(kind: LineKind): { bg: string; fg: string } {
  switch (kind) {
    case 'u-bahn':
      return { bg: '#3556d4', fg: '#fff' }
    case 's-bahn':
      return { bg: '#2f7d3a', fg: '#fff' }
    case 'tram':
      return { bg: '#b02a37', fg: '#fff' }
    case 'bus':
      return { bg: '#7a1f8f', fg: '#fff' }
    case 'other':
      return { bg: '#444c58', fg: '#fff' }
  }
}

export function badgeColorsFor(routeShortName: string): { bg: string; fg: string } {
  return kindColors(kindOf(routeShortName))
}

/** Spec: on-time share is the fraction of vehicles with -30 <= d <= 60. */
export function isOnTime(delaySeconds: number): boolean {
  return delaySeconds >= -30 && delaySeconds <= 60
}

/** Feed age as the comp renders it: one decimal + "s" (e.g. "11.6s"). */
export function feedAgeLabel(ageSeconds: number): string {
  return `${ageSeconds.toFixed(1)}s`
}

/** Shortest-arc interpolation between two bearings, in degrees. */
export function lerpBearing(from: number, to: number, f: number): number {
  const delta = ((to - from + 540) % 360) - 180
  return (from + delta * f + 360) % 360
}

// Cached: constructing an Intl.DateTimeFormat is expensive (locale data)
// and berlinSecondsOfDay is called from render paths.
const BERLIN_TIME_FORMAT = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Berlin',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

/**
 * Wall-clock seconds since midnight in the feed's timezone (Europe/Berlin),
 * the time base GTFS service-day seconds compare against.
 */
export function berlinSecondsOfDay(now: Date): number {
  const parts = BERLIN_TIME_FORMAT.formatToParts(now)
  const get = (type: string) =>
    Number(parts.find((part) => part.type === type)?.value ?? '0')
  return (get('hour') % 24) * 3600 + get('minute') * 60 + get('second')
}

/**
 * Project "now" into a trip's service-day seconds space. GTFS times exceed
 * 86400 on trips crossing midnight, so shortly after midnight the comparable
 * instant may be now + 86400. Chooses the candidate closest to (or inside)
 * the trip's scheduled span.
 */
export function serviceNowSeconds(
  nowSecondsOfDay: number,
  firstStopSeconds: number,
  lastStopSeconds: number,
): number {
  const candidates = [nowSecondsOfDay, nowSecondsOfDay + 86400]
  let best = candidates[0]
  let bestDistance = Number.POSITIVE_INFINITY
  for (const candidate of candidates) {
    const distance =
      candidate < firstStopSeconds
        ? firstStopSeconds - candidate
        : candidate > lastStopSeconds
          ? candidate - lastStopSeconds
          : 0
    if (distance < bestDistance) {
      bestDistance = distance
      best = candidate
    }
  }
  return best
}
