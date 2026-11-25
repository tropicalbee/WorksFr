import { Package, X } from 'lucide-react'
import type { CSSProperties } from 'react'

const at = (ms: number) => ({ '--delay': `${ms}ms` }) as CSSProperties

export function FixDiffVisual() {
  return (
    <>
      <div className="lp-code">
        <div className="lp-cl"><span className="lp-ln">14</span><span className="lp-mk" /><code>rows = load_rows("sales.csv")</code></div>
        <div className="lp-cl lp-rm"><span className="lp-ln">15</span><span className="lp-mk"><X aria-hidden="true" /></span><code>df = df.append(row)</code></div>
        <div className="lp-cl lp-ad lp-slide-y" style={at(1360)}><span className="lp-ln">15</span><span className="lp-mk">+</span><code>df = pd.concat([df, row])</code></div>
        <div className="lp-cl"><span className="lp-ln">16</span><span className="lp-mk" /><code>print(df.tail())</code></div>
      </div>
      <p className="lp-verdict lp-slide-x" style={at(2210)}>
        <Package aria-hidden="true" />
        <span>Runs clean. The patched step ran in the sandbox without an error. <span className="lp-dim">DataFrame.append was removed in pandas 2.0.</span></span>
      </p>
    </>
  )
}
