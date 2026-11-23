import { CornerDownRight, X } from 'lucide-react'
import type { StepReport } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { diffLines } from '@/lib/diff'

export function ReplacementDiff({ step }: { step: StepReport }) {
  const groups = diffLines(step.original_code, step.final_code)
  const unverified = step.verdict === 'fixed_unverified'

  return (
    <section className="replacement" aria-label="Replacement">
      <h3>{unverified ? 'Replacement checked' : 'Replacement that ran'}</h3>
      {groups.length ? (
        <div className="code-panel diff" tabIndex={0} role="region" aria-label="Replacement diff">
          {groups.map((group, index) => (
            <div className="diff-group" key={index}>
              {group.map((line, i) => (
                <div className={`diff-line diff-${line.kind}`} key={i}>
                  <span className="line-number" aria-hidden="true">{line.oldLine ?? ''}</span>
                  <span className="line-number" aria-hidden="true">{line.newLine ?? ''}</span>
                  <span className="diff-mark">
                    {line.kind === 'added' ? (
                      <>
                        <CornerDownRight aria-hidden="true" />
                        <span className="sr-only">Replacement line {line.newLine}: </span>
                      </>
                    ) : line.kind === 'removed' ? (
                      <>
                        <X aria-hidden="true" />
                        <span className="sr-only">Removed line {line.oldLine}: </span>
                      </>
                    ) : null}
                  </span>
                  <code>{line.text || ' '}</code>
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : <p className="metadata">No lines changed.</p>}
      <p className={unverified ? 'verdict unverified' : 'verdict'}>
        <CornerDownRight aria-hidden="true" />
        {unverified
          ? 'Replacement got past the changed API; the live call was not verified'
          : 'Replacement ran'}
      </p>
      <div className="api-chips">
        {step.apis_changed?.map(api => (
          <Badge variant="outline" key={api}>{api}</Badge>
        ))}
      </div>
    </section>
  )
}
