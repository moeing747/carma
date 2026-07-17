interface BannerProps {
  state: 'stale' | 'unavailable' | 'stream'
  subSeconds: number | null
}

export function Banner({ state, subSeconds }: BannerProps) {
  const text =
    state === 'stale'
      ? 'Feed is stale — positions may be inaccurate'
      : state === 'stream'
        ? 'Position stream interrupted — reconnecting'
        : 'Feed unavailable — reconnecting'
  const sub =
    subSeconds === null
      ? null
      : state === 'stale'
        ? `last update ${Math.round(subSeconds)}s ago`
        : `no data for ${Math.round(subSeconds)}s`
  return (
    <div className="banner-wrap">
      <div className={`banner ${state}`}>
        <span className="dot" />
        <span className="text">{text}</span>
        {sub !== null && <span className="sub">{sub}</span>}
      </div>
    </div>
  )
}
