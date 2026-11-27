import { Check, CircleAlert } from 'lucide-react'
import type { StepReport } from '@/api/types'

export function OutputComparison({ step }: { step: StepReport }) {
  const check = step.output_check
  if (!check) return null

  if (check.status === 'not_compared') {
    return <p className="reason" aria-label="Output comparison">{check.reason}</p>
  }

  if (check.status !== 'differs') {
    const ignored = check.status === 'matches_ignoring'
    const details = check.ignored?.join(', ')
    return (
      <section className="output-comparison" aria-label="Output comparison">
        <p className="output-comparison-title">
          <Check aria-hidden="true" />
          {ignored ? 'Output matches with differences ignored' : 'Output matches'}
        </p>
        {ignored && details && <p className="metadata">Ignored {details}.</p>}
      </section>
    )
  }

  return (
    <section className="output-comparison" aria-label="Output comparison">
      <h3 className="output-comparison-title"><CircleAlert aria-hidden="true" />Output differs</h3>
      {step.verdict === 'fixed' && <p className="metadata">Compared with the replacement run.</p>}
      <div className="output-comparison-pair">
        <figure>
          <figcaption className="metadata">Tutorial showed</figcaption>
          <pre className="output" tabIndex={0} role="region" aria-label="Tutorial output">{check.shown}</pre>
        </figure>
        <figure>
          <figcaption className="metadata">Run printed</figcaption>
          <pre className="output" tabIndex={0} role="region" aria-label="This run's output">{check.actual}</pre>
        </figure>
      </div>
    </section>
  )
}
