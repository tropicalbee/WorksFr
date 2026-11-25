import { Eye } from 'lucide-react'
import type { EventBlock, StepReport } from '@/api/types'
import { CodeBlock } from './CodeBlock'
import { Disclosure } from './Disclosure'
import { ReplacementDiff } from './ReplacementDiff'
import { StatusLabel } from './StatusLabel'
import { formatTimestamp, videoLinkAt } from './videoLink'

type StepEntryProps = {
  step?: StepReport
  block: EventBlock
  running?: boolean
  source?: string
}

export function StepEntry({ step, block, running = false, source = '' }: StepEntryProps) {
  const status = step ? step.label ?? 'not_run' : running ? 'running' : 'waiting'
  const attempts = step?.repair_attempts ?? []
  const accepted = step?.verdict === 'fixed' || step?.verdict === 'fixed_unverified'
  const stepNumber = block.idx + 1
  const timestamp = block.video_timestamp ?? null
  const lowConfidence = block.low_confidence || (step?.low_confidence ?? false)
  const fromVideo = timestamp !== null || lowConfidence
  const videoLink = timestamp !== null ? videoLinkAt(source, timestamp) : null
  const repeatsDiagnostic = step?.error
    ? step.reason.trim() === `${step.error.type}: ${step.error.message}`.trim()
    : false
  const showReason = step && (
    !step.label || step.label === 'cant_verify' || (step.label === 'broken' && !repeatsDiagnostic)
  )

  return (
    <article className="step-entry" id={`step-${stepNumber}`} aria-label={`Step ${stepNumber}`}>
      <div className="step-meta">
        <a className="step-anchor" href={`#step-${stepNumber}`}>Step {stepNumber}</a>
        <StatusLabel status={status} />
        <span className="step-kind">{block.kind}</span>
        {fromVideo && (
          <div className="step-video">
            <span className="video-badge"><Eye aria-hidden="true" />read from video</span>
            {timestamp !== null && (videoLink ? (
              <a
                className="video-time"
                href={videoLink}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`Open the video at ${formatTimestamp(timestamp)}`}
              >
                {formatTimestamp(timestamp)}
              </a>
            ) : <span className="video-time">{formatTimestamp(timestamp)}</span>)}
            {lowConfidence && <span className="metadata video-note">May have been misread from the video.</span>}
          </div>
        )}
      </div>
      <div className={`step-card${!step && !running ? ' waiting' : ''}`}>
        <CodeBlock
          code={step?.original_code ?? block.code}
          kind={block.kind}
          quiet={!step && !running}
        />
        {step?.label === 'deprecated' && step.warnings?.map((warning, i) => (
          <p className="diagnostic" key={i}>{warning.category}: {warning.message}</p>
        ))}
        {step?.label === 'broken' && step.error && (
          <p className="diagnostic">{step.error.type}: {step.error.message}</p>
        )}
        {accepted && step.explanation && (
          <section className="explanation">
            <h3>What changed</h3>
            <p>{step.explanation}</p>
          </section>
        )}
        {accepted && <ReplacementDiff step={step} />}
        {showReason && step.reason && <p className="reason">{step.reason}</p>}
        {attempts.length > 1 && (
          <Disclosure title={`${attempts.length} attempts`}>
            {attempts.map(attempt => (
              <section className="attempt" key={attempt.attempt}>
                <h4>
                  Attempt {attempt.attempt}
                  <span>
                    {attempt.provider ?? 'Provider not recorded'} {'·'} {attempt.verdict === 'fixed'
                      ? 'Replacement ran'
                      : attempt.verdict === 'fixed_unverified' ? 'Live call unverified' : 'Failed'}
                  </span>
                </h4>
                <CodeBlock code={attempt.patched_code} kind={block.kind} />
                {attempt.result?.error && (
                  <p className="diagnostic">
                    {attempt.result.error.type}: {attempt.result.error.message}
                  </p>
                )}
              </section>
            ))}
          </Disclosure>
        )}
        {(step?.output || step?.error?.traceback) && (
          <Disclosure title="Output and traceback">
            {step.output && (
              <>
                <h4>Output</h4>
                <pre className="output">{step.output}</pre>
              </>
            )}
            {step.error?.traceback && (
              <>
                <h4>Traceback</h4>
                <pre className="output">{step.error.traceback}</pre>
              </>
            )}
          </Disclosure>
        )}
      </div>
    </article>
  )
}
