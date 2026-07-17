import { describe, expect, it } from 'vitest'

import {
  abbreviateStopName,
  RAMP,
  badgeColorsFor,
  berlinSecondsOfDay,
  delayColor,
  delayLabel,
  delayWord,
  feedAgeLabel,
  isOnTime,
  kindOf,
  lerpBearing,
  serviceNowSeconds,
} from './helpers'

describe('delayColor (spec ramp)', () => {
  it.each([
    [-120, RAMP.early],
    [-31, RAMP.early],
    [-30, RAMP.ontime],
    [0, RAMP.ontime],
    [60, RAMP.ontime],
    [61, RAMP.amber],
    [180, RAMP.amber],
    [181, RAMP.orange],
    [300, RAMP.orange],
    [301, RAMP.late],
    [1200, RAMP.late],
  ])('%d s -> %s', (delay, expected) => {
    expect(delayColor(delay)).toBe(expected)
  })
})

describe('delayLabel', () => {
  it('renders small offsets as On time', () => {
    expect(delayLabel(0)).toBe('On time')
    expect(delayLabel(30)).toBe('On time')
    expect(delayLabel(-30)).toBe('On time')
  })
  it('renders sign + M:SS beyond 30s', () => {
    expect(delayLabel(180)).toBe('+3:00')
    expect(delayLabel(-65)).toBe('−1:05')
    expect(delayLabel(31)).toBe('+0:31')
    expect(delayLabel(725)).toBe('+12:05')
  })
  it('rounds fractional per-line averages to whole seconds', () => {
    expect(delayLabel(67.5)).toBe('+1:08')
    expect(delayLabel(77.142857)).toBe('+1:17')
    expect(delayLabel(30.4)).toBe('On time')
  })
})

describe('delayWord', () => {
  it.each([
    [-120, 'ahead of schedule'],
    [0, 'running on time'],
    [60, 'running on time'],
    [61, 'behind schedule'],
    [300, 'behind schedule'],
    [301, 'severely delayed'],
  ])('%d s -> %s', (delay, expected) => {
    expect(delayWord(delay)).toBe(expected)
  })
})

describe('kindOf', () => {
  it.each([
    ['U6', 'u-bahn'],
    ['U55', 'u-bahn'],
    ['S7', 's-bahn'],
    ['S41', 's-bahn'],
    ['M10', 'tram'],
    ['M1', 'tram'],
    ['140', 'bus'],
    ['676', 'bus'],
    ['RE1', 'other'],
    ['FEX', 'other'],
    ['', 'other'],
  ])('%s -> %s', (name, expected) => {
    expect(kindOf(name)).toBe(expected)
  })

  it('maps kinds to the spec badge colors', () => {
    expect(badgeColorsFor('U6')).toEqual({ bg: '#3556d4', fg: '#fff' })
    expect(badgeColorsFor('S7')).toEqual({ bg: '#2f7d3a', fg: '#fff' })
    expect(badgeColorsFor('M10')).toEqual({ bg: '#b02a37', fg: '#fff' })
    expect(badgeColorsFor('140')).toEqual({ bg: '#7a1f8f', fg: '#fff' })
  })
})

describe('isOnTime', () => {
  it('is the -30..60 band', () => {
    expect(isOnTime(-31)).toBe(false)
    expect(isOnTime(-30)).toBe(true)
    expect(isOnTime(60)).toBe(true)
    expect(isOnTime(61)).toBe(false)
  })
})

describe('feedAgeLabel', () => {
  it('renders one decimal + s', () => {
    expect(feedAgeLabel(11.64)).toBe('11.6s')
    expect(feedAgeLabel(0)).toBe('0.0s')
  })
})

describe('lerpBearing', () => {
  it('interpolates plainly when no wrap is involved', () => {
    expect(lerpBearing(10, 30, 0.5)).toBeCloseTo(20)
  })
  it('takes the shortest arc across north', () => {
    expect(lerpBearing(350, 10, 0.5)).toBeCloseTo(0)
    expect(lerpBearing(10, 350, 0.5)).toBeCloseTo(0)
  })
  it('stays within 0..360', () => {
    const result = lerpBearing(350, 10, 0.25)
    expect(result).toBeGreaterThanOrEqual(0)
    expect(result).toBeLessThan(360)
  })
})

describe('berlinSecondsOfDay', () => {
  it('converts UTC instants to Berlin wall clock (CEST in July)', () => {
    // 2026-07-17 10:15:30 UTC == 12:15:30 in Berlin
    const seconds = berlinSecondsOfDay(new Date('2026-07-17T10:15:30Z'))
    expect(seconds).toBe(12 * 3600 + 15 * 60 + 30)
  })
})

describe('serviceNowSeconds', () => {
  it('keeps the plain time for a daytime trip', () => {
    expect(serviceNowSeconds(12 * 3600, 11 * 3600, 13 * 3600)).toBe(12 * 3600)
  })
  it('projects past-midnight wall time into the previous service day', () => {
    // 00:30 wall clock, trip scheduled 23:50 -> 25:10 GTFS time
    const projected = serviceNowSeconds(1800, 23 * 3600 + 50 * 60, 25 * 3600 + 600)
    expect(projected).toBe(1800 + 86400)
  })
  it('prefers the candidate nearest the span when outside it', () => {
    expect(serviceNowSeconds(2 * 3600, 8 * 3600, 9 * 3600)).toBe(2 * 3600)
  })
})

describe('abbreviateStopName', () => {
  it('shortens standard German transit terms departure-board style', () => {
    expect(abbreviateStopName('Neustrelitz, Hauptbahnhof')).toBe('Neustrelitz, Hbf')
    expect(abbreviateStopName('U Osloer Straße')).toBe('U Osloer Str.')
    expect(abbreviateStopName('Bahnhofstraße')).toBe('Bahnhofstr.')
    expect(abbreviateStopName('S Ostbahnhof')).toBe('S Ostbahnhof')
    expect(abbreviateStopName('Schloßplatz Köpenick (Berlin)')).toBe('Schloßpl. Köpenick (Berlin)')
  })
})
