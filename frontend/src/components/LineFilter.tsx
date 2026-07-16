import { useState } from 'react'

import { badgeColorsFor, delayColor, delayLabel } from '../lib/helpers'

export interface LineStat {
  name: string
  count: number
  avgDelaySeconds: number
}

interface LineFilterProps {
  lines: LineStat[]
  activeLines: ReadonlySet<string>
  onToggle: (line: string) => void
}

export function LineFilter({ lines, activeLines, onToggle }: LineFilterProps) {
  const [collapsed, setCollapsed] = useState(false)
  const [query, setQuery] = useState('')

  if (collapsed) {
    return (
      <button className="lines-collapsed glass" onClick={() => setCollapsed(false)}>
        LINES ›
      </button>
    )
  }

  const needle = query.trim().toUpperCase()
  const visible = needle === '' ? lines : lines.filter((line) => line.name.toUpperCase().includes(needle))
  const anyActive = activeLines.size > 0

  return (
    <div className="line-filter glass">
      <div className="line-filter-header">
        <span className="panel-title">LINES</span>
        <button className="collapse-btn" onClick={() => setCollapsed(true)} aria-label="Collapse">
          ‹
        </button>
      </div>
      <div className="line-filter-search">
        <input
          placeholder="Search U6, S7, M10, 140…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div className="line-filter-rows">
        {visible.map((line) => {
          const active = activeLines.has(line.name)
          const badge = badgeColorsFor(line.name)
          const className = `line-row${active ? ' active' : ''}${anyActive && !active ? ' dimmed' : ''}`
          return (
            <button key={line.name} className={className} onClick={() => onToggle(line.name)}>
              <span className="line-badge" style={{ background: badge.bg, color: badge.fg }}>
                {line.name || '—'}
              </span>
              <span className="count">{line.count} veh</span>
              <span className="delay" style={{ color: delayColor(line.avgDelaySeconds) }}>
                {delayLabel(line.avgDelaySeconds)}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
