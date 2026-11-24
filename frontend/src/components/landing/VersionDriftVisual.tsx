import { ArrowRight } from 'lucide-react'
import type { CSSProperties } from 'react'

export function VersionDriftVisual() {
  return (
    <>
      <div className="lp-drift">
        <div className="lp-dcol"><span className="lp-dlabel">Tutorial pins</span><code className="lp-old">pandas==0.24.2</code></div>
        <div className="lp-dmid"><ArrowRight aria-hidden="true" /></div>
        <div className="lp-dcol">
          <span className="lp-dlabel">Checked with</span>
          <code className="lp-new lp-slide-x" style={{ '--delay': '2040ms' } as CSSProperties}>pandas 3.1.1</code>
        </div>
      </div>
      <div className="lp-rail" aria-hidden="true">
        <span className="lp-rail-line" />
        <span className="lp-rail-trail" />
        <span className="lp-rail-dot a" />
        <span className="lp-rail-dot b" />
        <span className="lp-rail-tag a">0.24.2</span>
        <span className="lp-rail-tag b">3.1.1</span>
      </div>
    </>
  )
}
