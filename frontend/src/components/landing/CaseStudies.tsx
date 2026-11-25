import { ExternalLink } from 'lucide-react'
import { Link } from 'react-router'
import type { CaseStudyData, TutorialCaseStudy } from './caseStudies'

function TutorialRow({ tutorial }: { tutorial: TutorialCaseStudy }) {
  return (
    <tr role="row">
      <th scope="row" role="rowheader">
        <div className="lp-case-tutorial">
          <Link className="lp-case-link" to={`/checks/${tutorial.check_id}`}>{tutorial.title}</Link>
          <a className="lp-case-source" href={tutorial.url} target="_blank" rel="noopener noreferrer"
            aria-label={`Open original tutorial: ${tutorial.title} (opens in a new tab)`}>
            <ExternalLink size={14} aria-hidden="true" />
          </a>
        </div>
        <span className="lp-case-note">{tutorial.site} · {tutorial.language}</span>
        {tutorial.hit_time_limit && <span className="lp-case-note">hit the 15-minute limit</span>}
      </th>
      <td role="cell">
        <span className="lp-case-mobile-label" aria-hidden="true">Reach</span>
        {tutorial.reach}
      </td>
      <td role="cell">
        <span className="lp-case-mobile-label" aria-hidden="true">What broke</span>
        {tutorial.cause}
      </td>
      <td role="cell">
        <span className="lp-case-mobile-label" aria-hidden="true">Broken</span>
        <span className="lp-case-broken">{tutorial.broken}</span>
      </td>
      <td role="cell">
        <span className="lp-case-mobile-label" aria-hidden="true">Fixed</span>
        <span className="lp-case-fixed">{tutorial.fixed}</span>
        {tutorial.fixed_unverified > 0 && (
          <span className="lp-case-note">+{tutorial.fixed_unverified} live call unverified</span>
        )}
      </td>
      <td role="cell">
        <span className="lp-case-mobile-label" aria-hidden="true">Can't verify</span>
        <span className="lp-case-unchecked">{tutorial.cant_verify}</span>
      </td>
    </tr>
  )
}

export function CaseStudies({ data }: { data: CaseStudyData }) {
  const { count, sites, languages } = data.tutorial_totals
  const { agent_test: snippets, mcp_agent: agent } = data
  const minutes = Math.round(agent.seconds / 60)

  return (
    <section className="lp-case-studies" id="case-studies" aria-labelledby="case-studies-heading">
      <h2 id="case-studies-heading">Case studies</h2>
      <p>
        We picked {count} Python tutorials from {sites} sources in {languages} languages, from big learning sites to
        personal blogs, and ran each one as written. A fix counts only when its replacement ran in the sandbox without
        an error. Its output was not compared with the tutorial's. Steps that need a file, the network or a real API
        key are counted as Can't verify, not guessed.
      </p>
      <table className="lp-case-table" role="table" aria-label="Tutorial check results">
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader">Tutorial</th>
            <th scope="col" role="columnheader">Reach</th>
            <th scope="col" role="columnheader">What broke</th>
            <th scope="col" role="columnheader">Broken</th>
            <th scope="col" role="columnheader">Fixed</th>
            <th scope="col" role="columnheader">Can't verify</th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          {data.tutorials.map(tutorial => <TutorialRow key={tutorial.check_id} tutorial={tutorial} />)}
        </tbody>
      </table>
      <div className="lp-case-detail">
        <h3>AI-written code</h3>
        <p>
          We asked {snippets.model} for {snippets.snippets} everyday snippets without naming library versions.
          {' '}{snippets.broke} broke on today's libraries ({snippets.broken_apis.join(', ')}), and WorksFr found a
          fix that ran for {snippets.fixed}. One more tried to load a removed dataset inside <code>try/except</code>{' '}
          and quietly fell back to made-up data. So it ran, but it never did the task. That's why we say a fix runs
          clean, not that it's correct.
        </p>
      </div>
      <div className="lp-case-detail">
        <h3>A real agent used it</h3>
        <p>
          {agent.agent} on {agent.model} received the request <q>{agent.prompt}</q> With one setup line in its
          instructions, it called WorksFr's MCP tool <code>check_code</code> {agent.tool_calls} times in {minutes}{' '}
          {minutes === 1 ? 'minute' : 'minutes'}. {agent.result}
        </p>
        <code className="lp-case-setup">{agent.setup_line}</code>
        <p className="lp-muted">{agent.without_setup_line}</p>
        <Link className="lp-case-link" to={`/checks/${agent.check_id}`}>See the report</Link>
      </div>
    </section>
  )
}
