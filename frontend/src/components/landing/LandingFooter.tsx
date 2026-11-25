import { Link, useLocation, useNavigate } from 'react-router'
import type { MouseEvent } from 'react'
import { isShowcase } from '@/api/checkSource'
import { focusCheckInput } from '@/components/checkInput'
import './footer.css'

const REPO_URL = 'https://github.com/tropicalbee/WorksFr'
const READS = ['Web pages', 'Jupyter notebooks', 'GitHub READMEs', 'Stack Overflow answers', 'YouTube videos']
const SAFETY = ['Checks run locally in Docker, not on a shared server.', 'Tutorial code runs only in a container.', 'No network during runs.']

export function LandingFooter() {
  const navigate = useNavigate()
  const onHome = useLocation().pathname === '/'

  function startCheck(event: MouseEvent) {
    event.preventDefault()
    if (onHome && focusCheckInput()) return
    navigate('/', { state: { focusCheck: true } })
  }

  return (
    <footer className="lp-foot">
      <div className="lp-foot-grid">
        <div className="lp-foot-brand">
          <Link to="/" className="wordmark">WorksFr<span className="wordmark-period">.</span></Link>
          <p>Checks Python tutorials against today's libraries in a sandbox.</p>
        </div>
        <section aria-labelledby="foot-product">
          <h2 id="foot-product">Product</h2>
          <ul>
            <li><Link to="/#how">How it works</Link></li>
            <li><Link to="/#recent">Recent checks</Link></li>
            <li>{isShowcase ? <Link to="/try">Check a tutorial</Link> : <Link to="/" onClick={startCheck}>Check a tutorial</Link>}</li>
            <li><a href={REPO_URL} target="_blank" rel="noopener noreferrer">Source on GitHub</a></li>
          </ul>
        </section>
        <section aria-labelledby="foot-reads">
          <h2 id="foot-reads">Reads</h2>
          <ul>{READS.map(item => <li key={item}>{item}</li>)}</ul>
        </section>
        <section aria-labelledby="foot-safety">
          <h2 id="foot-safety">Safety</h2>
          <ul>{SAFETY.map(item => <li key={item}>{item}</li>)}</ul>
        </section>
        <section aria-labelledby="foot-legal">
          <h2 id="foot-legal">Legal</h2>
          <ul>
            <li><Link to="/terms">Terms</Link></li>
            <li><Link to="/privacy">Privacy</Link></li>
            <li><Link to="/security">Security</Link></li>
          </ul>
        </section>
      </div>
      <p className="lp-foot-credit">Effects adapted from Magic UI and shadcn/ui.</p>
      <div className="lp-foot-base">
        <span>
          Built by{' '}
          <a href="https://github.com/tropicalbee" target="_blank" rel="noopener noreferrer">tropicalbee</a>
        </span>
        <span>Labels come from real sandbox runs, never from the model alone.</span>
      </div>
    </footer>
  )
}
