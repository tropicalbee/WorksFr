import { ArrowDown } from 'lucide-react'
import type { CSSProperties } from 'react'

const at = (ms: number) => ({ '--delay': `${ms}ms` }) as CSSProperties

export function RealRunVisual() {
  return (
    <>
      <ol className="lp-flow">
        <li className="n1 lp-reveal-x" style={at(0)}><span className="lp-nt">Proposed patch</span><code>pd.concat([df, row])</code><span className="lp-ns">not saved yet</span></li>
        <li className="conn lp-reveal-y" style={at(765)}><ArrowDown aria-hidden="true" /></li>
        <li className="n2 lp-reveal-x" style={at(1105)}><span className="lp-nt">Sandbox run</span><code>exit 0, no error</code><span className="lp-ns ok"><span className="lp-dot" />passed</span></li>
        <li className="conn lp-reveal-y" style={at(1870)}><ArrowDown aria-hidden="true" /></li>
        <li className="n3 lp-reveal-x" style={at(2210)}><span className="lp-nt">Saved as the fix</span><code>shown in the report</code><span className="lp-ns blue">runs clean</span></li>
      </ol>
      <p className="lp-fail">If the run errors, the patch is dropped and the step stays Broken.</p>
    </>
  )
}
