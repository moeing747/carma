import { useCallback, useEffect, useRef, useState } from 'react'

import { badgeColorsFor, delayColor, delayLabel } from '../lib/helpers'
import {
  formatHold,
  formatSpread,
  optimizeGate,
  shortTripId,
  spreadImprovementPct,
} from '../lib/optimize'
import type { OptimizePlan } from '../lib/types'

interface OptimizePanelProps {
  activeLines: ReadonlySet<string>
  countForLine: (line: string) => number
  plan: OptimizePlan | null
  onPlan: (plan: OptimizePlan | null) => void
}

/**
 * Bottom-right OPTIMIZE panel: runs the advisory headway re-spacing for the
 * single filtered line and lists the recommended holds. Nothing here is
 * "applied" anywhere — the backend plan is advisory by design.
 */
export function OptimizePanel({ activeLines, countForLine, plan, onPlan }: OptimizePanelProps) {
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const gate = optimizeGate(activeLines, countForLine)
  const gatedLine = gate.kind === 'ready' ? gate.line : null

  // A resolving optimize call must never deliver a plan for a previous line
  // filter: abort in-flight requests when the gated line changes (and on
  // unmount). A stale error message must not survive the change either.
  useEffect(() => {
    setError(null)
    return () => controllerRef.current?.abort()
  }, [gatedLine])

  const run = useCallback(() => {
    if (gate.kind !== 'ready' || running) return
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setRunning(true)
    setError(null)
    fetch('/api/v1/optimize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ route_short_name: gate.line }),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          // The backend answers errors as JSON, but never assume a parsable
          // body on a failure status (proxies serve HTML).
          const body = (await response.json().catch(() => null)) as { error?: string } | null
          throw new Error(body?.error ?? `optimize ${response.status}`)
        }
        onPlan((await response.json()) as OptimizePlan)
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        onPlan(null)
        setError(cause instanceof Error ? cause.message : 'optimize failed')
      })
      .finally(() => setRunning(false))
  }, [gate, running, onPlan])

  return (
    <div className="optimize-panel glass">
      <div className="optimize-head">
        <span className="panel-title">OPTIMIZE</span>
        <span className="advisory-tag">ADVISORY</span>
        {(plan !== null || error !== null) && (
          <button
            className="close-btn"
            onClick={() => {
              onPlan(null)
              setError(null)
            }}
            aria-label="Dismiss result"
          >
            ×
          </button>
        )}
      </div>

      {gate.kind === 'hint' ? (
        <div className="optimize-hint">{gate.message}</div>
      ) : (
        <div className="optimize-run-row">
          <span className="line-badge" style={badgeStyle(gate.line)}>
            {gate.line}
          </span>
          <span className="optimize-count">{gate.count} vehicles</span>
          <button className="run-btn" onClick={run} disabled={running}>
            {running ? 'RUNNING…' : plan !== null ? 'RE-RUN' : 'RUN'}
          </button>
        </div>
      )}

      {error !== null && <div className="optimize-error">{error}</div>}

      {plan !== null && <PlanResult plan={plan} />}
    </div>
  )
}

function PlanResult({ plan }: { plan: OptimizePlan }) {
  const { summary } = plan
  const before = summary.headway_stddev_before_seconds
  const after = summary.headway_stddev_after_seconds
  const improvement = spreadImprovementPct(before, after)
  const improved = after < before
  return (
    <div className="optimize-result">
      <div className="optimize-spread">
        <span className="name">headway σ</span>
        <span className="figures">
          <span className="before">{formatSpread(before)}</span>
          <span className="arrow">→</span>
          <span className="after" style={{ color: improved ? 'var(--accent)' : 'var(--text-2)' }}>
            {formatSpread(after)}
          </span>
          {improvement !== null && improvement > 0 && (
            <span className="gain">−{improvement}%</span>
          )}
        </span>
      </div>
      <div className="optimize-rows">
        {plan.vehicles.map((vehicle) => (
          <div className="optimize-row" key={vehicle.trip_id}>
            <span className="tooltip-badge" style={badgeStyle(plan.route_short_name)}>
              {plan.route_short_name}
            </span>
            <span className="trip" title={vehicle.trip_id}>
              {shortTripId(vehicle.trip_id)}
            </span>
            <span className="row-delay" style={{ color: delayColor(vehicle.delay_seconds) }}>
              {delayLabel(vehicle.delay_seconds)}
            </span>
            {vehicle.hold_seconds > 0 ? (
              <span className="hold">
                HOLD <b>{formatHold(vehicle.hold_seconds)}</b>
                <span className="at"> at {vehicle.next_stop_name}</span>
              </span>
            ) : (
              <span className="hold none">no hold</span>
            )}
          </div>
        ))}
      </div>
      <div className="optimize-foot">
        engine {plan.engine} · towards {plan.direction || '—'} · holds ≤{' '}
        {Math.round(summary.max_hold_seconds / 60)} min · advisory only
      </div>
    </div>
  )
}

function badgeStyle(line: string): { background: string; color: string } {
  const { bg, fg } = badgeColorsFor(line)
  return { background: bg, color: fg }
}
