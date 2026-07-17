import { useCallback, useState } from 'react'

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
  const gate = optimizeGate(activeLines, countForLine)

  const run = useCallback(() => {
    if (gate.kind !== 'ready' || running) return
    setRunning(true)
    setError(null)
    fetch('/api/v1/optimize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ route_short_name: gate.line }),
    })
      .then(async (response) => {
        const body = (await response.json()) as OptimizePlan & { error?: string }
        if (!response.ok) throw new Error(body.error ?? `optimize ${response.status}`)
        onPlan(body)
      })
      .catch((cause: unknown) => {
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
        {plan !== null && (
          <button
            className="close-btn"
            onClick={() => onPlan(null)}
            aria-label="Dismiss plan"
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
