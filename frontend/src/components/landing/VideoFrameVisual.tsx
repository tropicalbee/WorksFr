import { Eye, Play } from 'lucide-react'

export function VideoFrameVisual() {
  return (
    <div className="lp-frame">
      <span className="lp-badge"><Eye aria-hidden="true" />read from video</span>
      <div className="lp-screen">
        <div className="lp-vl"><code><span className="lp-q">import</span> pandas <span className="lp-q">as</span> pd</code></div>
        <div className="lp-vl"><code>df = pd.read_csv("sales.csv")</code></div>
        <div className="lp-vl lp-hit"><code>df = df.append(row)</code></div>
        <div className="lp-vl"><code>print(df.tail())</code></div>
      </div>
      <div className="lp-player">
        <Play aria-hidden="true" />
        <span className="lp-bar"><span className="lp-fill" /><span className="lp-thumb" /></span>
        <span className="lp-tc">12:39</span>
      </div>
    </div>
  )
}
