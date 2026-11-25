import { useState } from 'react'
import { Copy, ExternalLink, X } from 'lucide-react'
import { Link } from 'react-router'
import { formatCheckCount } from './caseStudies'
import type { CaseStudyData, TutorialCaseStudy } from './caseStudies'

const REACH_LABEL = 'Reach (as the page shows it)'

function TutorialRow({ tutorial }: { tutorial: TutorialCaseStudy }) {
  return (
    <tr className="lp-result-row" role="row">
      <th className="lp-result-title" scope="row" role="rowheader">
        <div className="lp-case-tutorial">
          {tutorial.broken > 0
            ? <X className="lp-mark-broken lp-title-mark" aria-hidden="true" />
            : <span className="lp-mark-dot lp-mark-works lp-title-mark" aria-hidden="true" />}
          <Link className="lp-case-link" to={`/checks/${encodeURIComponent(tutorial.check_id)}`}>{tutorial.title}</Link>{' '}
          <a className="lp-case-source" href={tutorial.url} target="_blank" rel="noopener noreferrer"
            aria-label={`Open original tutorial: ${tutorial.title} (opens in a new tab)`}>
            <ExternalLink size={12} aria-hidden="true" />
          </a>
        </div>
        <span className="lp-result-meta">
          {tutorial.site} · {tutorial.language}{tutorial.hit_time_limit && ' · hit the 15-minute limit'}
        </span>
      </th>
      <td className="lp-case-reach" role="cell">{tutorial.reach}</td>
      <td className="lp-case-cause" role="cell">
        <span tabIndex={0} title={tutorial.cause}>{tutorial.cause_plain || tutorial.cause}</span>
      </td>
      <td className="lp-result-count" role="cell">
        <span className="lp-result-mobile-label" aria-hidden="true">Broken</span>
        <span data-zero={tutorial.broken === 0}>{formatCheckCount(tutorial.broken)}</span>
      </td>
      <td className="lp-result-count" role="cell">
        <span className="lp-result-mobile-label" aria-hidden="true">Fixed</span>
        <span data-zero={tutorial.fixed === 0}>{formatCheckCount(tutorial.fixed)}</span>
        {tutorial.fixed_unverified > 0 && (
          <span className="lp-result-note" title={`${tutorial.unverified_reason || 'Replacement'} unverified`}>
            +{tutorial.fixed_unverified} unverified
          </span>
        )}
      </td>
      <td className="lp-result-count" role="cell">
        <span className="lp-result-mobile-label" aria-hidden="true">Can't verify</span>
        <span data-zero={tutorial.cant_verify === 0}>{formatCheckCount(tutorial.cant_verify)}</span>
      </td>
    </tr>
  )
}

function SetupLine({ line }: { line: string }) {
  const [status, setStatus] = useState('')

  async function copy() {
    setStatus('')
    try {
      await navigator.clipboard.writeText(line)
      setStatus('Copied')
    } catch {
      setStatus('Select the setup line to copy it manually.')
    }
  }

  return (
    <>
      <p className="lp-setup-label">Setup line</p>
      <div className="lp-case-setup">
        <pre><code>{line}</code></pre>
        <button type="button" aria-label="Copy setup line" onClick={() => void copy()}>
          <Copy size={14} aria-hidden="true" />Copy
        </button>
      </div>
      <p className="lp-copy-status" role="status">{status}</p>
    </>
  )
}

export function CaseStudies({ data }: { data: CaseStudyData }) {
  const { count, sites, languages } = data.tutorial_totals
  const { agent_test: snippets, mcp_agent: agent } = data
  const minutes = Math.round(agent.seconds / 60)
  const [firstReplacement, secondReplacement] = agent.replacement_check_ids ?? []

  return (
    <section className="lp-evidence-section" id="case-studies" aria-labelledby="case-studies-heading">
      <h2 id="case-studies-heading">Case studies</h2>
      <p className="lp-case-lede">
        We ran {count} tutorials from {sites} sources in {languages} languages, as written. A fix counts only when
        its replacement ran in the sandbox without an error; its output was not compared with the tutorial's. Steps
        that need a file, the network or a real API key are Can't verify, not guessed.
      </p>
      <table className="lp-results-table lp-case-table" role="table" aria-label="Tutorial check results">
        <colgroup>
          <col className="lp-case-title-column" />
          <col className="lp-case-reach-column" />
          <col />
          <col className="lp-case-count-column" span={2} />
          <col className="lp-case-unchecked-column" />
        </colgroup>
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader">Tutorial</th>
            <th scope="col" role="columnheader">{REACH_LABEL}</th>
            <th scope="col" role="columnheader">What broke</th>
            <th className="lp-result-align" scope="col" role="columnheader">
              <X className="lp-mark-broken" aria-hidden="true" />Broken
            </th>
            <th className="lp-result-align" scope="col" role="columnheader">
              <span className="lp-mark-dot lp-mark-fixed" aria-hidden="true" />Fixed
            </th>
            <th className="lp-result-align" scope="col" role="columnheader">
              <span className="lp-mark-dot lp-mark-unchecked" aria-hidden="true" />Can't verify
            </th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          {data.tutorials.map(tutorial => <TutorialRow key={tutorial.check_id} tutorial={tutorial} />)}
        </tbody>
      </table>
      <div className="lp-case-experiments">
        <div>
          <h3>AI-written code</h3>
          <p>
            We asked an AI model for {snippets.snippets} short code snippets.{' '}
            {snippets.broke + snippets.hidden_removed_api} of them used code that no longer works.
          </p>
          <details className="lp-case-method">
            <summary>How we tested</summary>
            <dl className="lp-case-facts">
              <div><dt>Model</dt><dd>{snippets.model}</dd></div>
              <div><dt>Asked for</dt><dd>{snippets.snippets} snippets about recently changed APIs, no library versions named</dd></div>
              <div>
                <dt>Broke</dt>
                <dd>
                  {snippets.broke} snippets ({snippets.broken_apis.join(', ')}).{' '}
                  WorksFr found a fix that ran for {snippets.fixed}.
                </dd>
              </div>
              <div>
                <dt>Removed API</dt>
                <dd>
                  {snippets.hidden_removed_api} more loaded a removed dataset inside <code>try/except</code> and fell
                  back to made-up data. Its run finished with data that failed to answer the original request.
                </dd>
              </div>
              {snippets.cant_verify !== undefined && <div><dt>Can't verify</dt><dd>{snippets.cant_verify}</dd></div>}
            </dl>
          </details>
        </div>
        <div>
          <h3>A real agent used it</h3>
          <p>
            An AI coding assistant can use WorksFr to test code before handing it to you. In our test it caught a
            broken line and only gave back code that ran.
          </p>
          <details className="lp-case-method">
            <summary>How we tested</summary>
            <dl className="lp-case-facts">
              <div><dt>Agent</dt><dd>{agent.agent} on {agent.model}</dd></div>
              <div><dt>Prompt</dt><dd>{agent.prompt}</dd></div>
              <div><dt>Calls</dt><dd><code>check_code</code> × {agent.tool_calls} in {minutes} {minutes === 1 ? 'minute' : 'minutes'}</dd></div>
              <div><dt>Result</dt><dd>{agent.result}</dd></div>
              <div><dt>Without the line</dt><dd>{agent.without_setup_line}</dd></div>
            </dl>
            <SetupLine line={agent.setup_line} />
            <div className="lp-evidence-links">
              <Link className="lp-case-link" to={`/checks/${encodeURIComponent(agent.check_id)}`}>See the report</Link>
              {firstReplacement && <Link className="lp-case-link" to={`/checks/${encodeURIComponent(firstReplacement)}`}>Replacement 1</Link>}
              {secondReplacement && <Link className="lp-case-link" to={`/checks/${encodeURIComponent(secondReplacement)}`}>Replacement 2</Link>}
            </div>
          </details>
        </div>
      </div>
    </section>
  )
}
