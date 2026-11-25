import { useState } from 'react'
import { SiteHeader } from '@/components/SiteHeader'
import { LandingFooter } from '@/components/landing/LandingFooter'
import { Button } from '@/components/ui/button'
import '@/components/landing/landing.css'
import '@/components/legal.css'

function Command({ command, platform }: { command: string; platform?: string }) {
  const [status, setStatus] = useState('')

  async function copy() {
    setStatus('')
    try {
      await navigator.clipboard.writeText(command)
      setStatus('Copied')
    } catch {
      setStatus('Select the command to copy it manually.')
    }
  }

  return (
    <div className="my-2.5">
      {platform && <span className="metadata">{platform}</span>}
      <div className="flex items-start gap-2 rounded-lg border border-border bg-secondary p-2">
        <pre className="min-w-0 flex-1 overflow-x-auto px-2 py-1"><code>{command}</code></pre>
        <Button type="button" variant="outline" size="sm" onClick={() => void copy()}>Copy</Button>
      </div>
      <div aria-live="polite" className="metadata">{status}</div>
    </div>
  )
}

export default function TryPage() {
  return (
    <>
      <SiteHeader />
      <div className="lp-page">
        <main className="legal">
          <h1>Run WorksFr yourself</h1>

          <h2>1. What you need</h2>
          <ul>
            <li>Docker Desktop, running</li>
            <li>Python 3.12 or newer</li>
            <li>Node.js <code>^20.19.0</code> or <code>&gt;=22.12.0</code></li>
            <li>Git</li>
            <li>An API key and a model name for NVIDIA, Groq, or Google Gemini</li>
          </ul>

          <h2>2. Clone the repository</h2>
          <Command command="git clone https://github.com/tropicalbee/WorksFr.git" />
          <Command command="cd WorksFr" />

          <h2>3. Build the sandbox image</h2>
          <p>The first build takes several minutes and the image is large.</p>
          <Command command="docker build -t worksfr-sandbox sandbox/" />

          <h2>4. Start the backend</h2>
          <Command command="cd backend" />
          <Command command="python -m venv .venv" />
          <div className="grid gap-x-3 sm:grid-cols-2">
            <Command platform="Windows PowerShell" command={String.raw`.venv\Scripts\Activate.ps1`} />
            <Command platform="macOS or Linux" command="source .venv/bin/activate" />
          </div>
          <Command command="pip install -r requirements.txt" />
          <div className="grid gap-x-3 sm:grid-cols-2">
            <Command platform="Windows PowerShell" command="Copy-Item .env.example .env" />
            <Command platform="macOS or Linux" command="cp .env.example .env" />
          </div>
          <p>
            In <code>backend/.env</code>, fill in <code>LLM_&lt;NAME&gt;_API_KEY</code> and{' '}
            <code>LLM_&lt;NAME&gt;_MODEL</code> for at least one provider.
          </p>
          <Command command="uvicorn app.main:app --reload" />

          <h2>5. Start the frontend in a second terminal</h2>
          <Command command="cd frontend" />
          <Command command="npm install" />
          <Command command="npm run dev" />

          <h2>6. Check a tutorial</h2>
          <p>Open <a href="http://localhost:5173">http://localhost:5173</a> and paste a tutorial link.</p>

          <h2>Why isn't there a live demo?</h2>
          <p>Tutorial code is untrusted, so WorksFr runs it in a Docker sandbox on your own machine, never on someone else's server. Running untrusted code for the public safely needs real infrastructure, so a hosted version is on the roadmap.</p>
          <p><a href="https://github.com/tropicalbee/WorksFr">WorksFr on GitHub</a></p>
        </main>
        <LandingFooter />
      </div>
    </>
  )
}
