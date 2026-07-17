/**
 * Leading + trailing throttle (unit-tested in throttle.test.ts).
 *
 * The first call in a quiet period emits immediately; calls landing inside
 * the interval are coalesced into one trailing emit of the *latest* value.
 * Both edges matter for the map-bounds notifications: a leading-only
 * throttle dropped the mount-time bounds when the first interaction came
 * within the interval, and swallowed the final view state of short pans.
 */

export interface Throttled<T> {
  (value: T): void
  /** Drop any pending trailing emit (call on unmount). */
  cancel: () => void
}

export function trailingThrottle<T>(
  intervalMs: number,
  emit: (value: T) => void,
): Throttled<T> {
  let lastEmitMs = Number.NEGATIVE_INFINITY
  let timer: ReturnType<typeof setTimeout> | null = null

  const call = (value: T): void => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    const wait = intervalMs - (Date.now() - lastEmitMs)
    if (wait <= 0) {
      lastEmitMs = Date.now()
      emit(value)
      return
    }
    timer = setTimeout(() => {
      timer = null
      lastEmitMs = Date.now()
      emit(value)
    }, wait)
  }

  const cancel = (): void => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  return Object.assign(call, { cancel })
}
