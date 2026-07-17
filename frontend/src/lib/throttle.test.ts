import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { trailingThrottle } from './throttle'

describe('trailingThrottle', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('emits the very first call immediately', () => {
    const emitted: number[] = []
    const throttled = trailingThrottle(250, (value: number) => emitted.push(value))

    throttled(1)

    expect(emitted).toEqual([1])
  })

  it('coalesces calls inside the interval into one trailing emit of the latest value', () => {
    const emitted: number[] = []
    const throttled = trailingThrottle(250, (value: number) => emitted.push(value))

    throttled(1) // leading
    vi.advanceTimersByTime(100)
    throttled(2)
    throttled(3)
    expect(emitted).toEqual([1])

    vi.advanceTimersByTime(150) // interval since the leading emit elapses
    expect(emitted).toEqual([1, 3])
  })

  it('does not drop a mount-time call followed by a fast first interaction', () => {
    // Regression for the bounds throttle: with the ref initialised to "0"
    // and a first paint under the interval, the mount notification vanished.
    const emitted: string[] = []
    const throttled = trailingThrottle(250, (value: string) => emitted.push(value))

    throttled('mount')
    vi.advanceTimersByTime(50)
    throttled('first-pan')

    expect(emitted).toEqual(['mount'])
    vi.advanceTimersByTime(200)
    expect(emitted).toEqual(['mount', 'first-pan'])
  })

  it('emits immediately again once the interval has passed', () => {
    const emitted: number[] = []
    const throttled = trailingThrottle(250, (value: number) => emitted.push(value))

    throttled(1)
    vi.advanceTimersByTime(300)
    throttled(2)

    expect(emitted).toEqual([1, 2])
  })

  it('cancel drops a pending trailing emit', () => {
    const emitted: number[] = []
    const throttled = trailingThrottle(250, (value: number) => emitted.push(value))

    throttled(1)
    throttled(2)
    throttled.cancel()
    vi.advanceTimersByTime(1_000)

    expect(emitted).toEqual([1])
  })
})
