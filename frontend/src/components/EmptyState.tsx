export function EmptyState() {
  return (
    <div className="empty-state">
      <div className="empty-state-inner">
        <div className="empty-state-icon">◍</div>
        <span className="empty-state-title">No vehicles in view</span>
        <span className="empty-state-sub">
          The feed is healthy, but no active vehicles fall within the current map extent. Zoom out
          or pan back to Berlin centre.
        </span>
      </div>
    </div>
  )
}
