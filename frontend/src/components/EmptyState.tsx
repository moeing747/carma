interface EmptyStateProps {
  /** Number of lines currently filtered; > 0 changes the diagnosis. */
  filteredLineCount: number
  onClearFilters: () => void
}

export function EmptyState({ filteredLineCount, onClearFilters }: EmptyStateProps) {
  const filtered = filteredLineCount > 0
  return (
    <div className="empty-state">
      <div className="empty-state-inner">
        <div className="empty-state-icon">◍</div>
        <span className="empty-state-title">No vehicles in view</span>
        <span className="empty-state-sub">
          {filtered
            ? 'The feed is healthy, but the active line filter matches no vehicle in the current map extent.'
            : 'The feed is healthy, but no active vehicles fall within the current map extent. Zoom out or pan back to Berlin centre.'}
        </span>
        {filtered && (
          <button className="empty-state-clear" onClick={onClearFilters}>
            Clear line filter
          </button>
        )}
      </div>
    </div>
  )
}
