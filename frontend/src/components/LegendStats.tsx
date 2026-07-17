import { delayColor, delayLabel } from '../lib/helpers'

interface LegendStatsProps {
  onTimePct: number | null
  inView: number
  total: number
  worstLine: { name: string; avgDelaySeconds: number } | null
  streamConnected: boolean
}

export function LegendStats({
  onTimePct,
  inView,
  total,
  worstLine,
  streamConnected,
}: LegendStatsProps) {
  return (
    <div className="legend glass">
      <div className="legend-ramp-block">
        <span className="panel-title">DELAY</span>
        <div className="legend-ramp">
          <span />
          <span />
          <span />
          <span />
          <span />
        </div>
        <div className="legend-scale">
          <span>early</span>
          <span>on&nbsp;time</span>
          <span>+1m</span>
          <span>+3m</span>
          <span>+5m</span>
        </div>
      </div>
      <div className="legend-stats">
        {!streamConnected && (
          <div className="legend-note">
            <span className="dot" />
            stream reconnecting — stats frozen
          </div>
        )}
        <div className="stat-row">
          <span className="name">On time</span>
          <span className="ontime">{onTimePct === null ? '—' : `${Math.round(onTimePct)}%`}</span>
        </div>
        <div className="stat-row">
          <span className="name">In view</span>
          <span className="mono">
            {inView} / {total}
          </span>
        </div>
        <div className="stat-row">
          <span className="name">Worst line</span>
          {worstLine === null ? (
            <span className="mono">—</span>
          ) : (
            <span className="worst">
              {worstLine.name}
              <span style={{ color: delayColor(worstLine.avgDelaySeconds) }}>
                {delayLabel(worstLine.avgDelaySeconds)}
              </span>
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
