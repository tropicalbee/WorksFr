import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowRight, ClipboardPaste, Link2, Upload } from 'lucide-react'
import { useNavigate } from 'react-router'
import type { components } from '@/api/schema'
import { CHECK_INPUT_ID } from '@/components/checkInput'

type CheckCreated = components['schemas']['CheckCreated']
type ValidationError = components['schemas']['ValidationError']

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: string | ValidationError[] }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail) && body.detail[0]?.msg) return body.detail[0].msg
  } catch {
    // The body was not JSON; use the generic message below.
  }
  return `The server returned an error (${response.status}).`
}

export function CheckForm() {
  const navigate = useNavigate()
  const fileInput = useRef<HTMLInputElement>(null)
  const [url, setUrl] = useState('')
  const [pasting, setPasting] = useState(false)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const trimmed = url.trim()
  const canCheck = pasting ? code.trim().length > 0 : trimmed.length > 0

  async function send(request: Promise<Response>) {
    setBusy(true)
    setError(null)
    try {
      const response = await request
      if (!response.ok) {
        setError(await errorMessage(response))
        return
      }
      const created = await response.json() as CheckCreated
      navigate(`/checks/${created.check_id}`)
    } catch {
      setError('Could not reach the server. Check that the backend is running.')
    } finally {
      setBusy(false)
    }
  }

  function submitUrl(event: FormEvent) {
    event.preventDefault()
    if (!canCheck || busy) return
    if (pasting) {
      void send(fetch('/api/checks/code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code } satisfies components['schemas']['CodeSubmission']),
      }))
      return
    }
    void send(fetch('/api/checks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: trimmed } satisfies components['schemas']['URLSubmission']),
    }))
  }

  function submitFile(file: File | undefined) {
    if (!file || busy) return
    const body = new FormData()
    body.append('file', file)
    void send(fetch('/api/checks/notebook', { method: 'POST', body }))
  }

  return (
    <form className="lp-check" onSubmit={submitUrl}>
      <label className="lp-url">
        <Link2 aria-hidden="true" />
        <input
          type="text"
          inputMode="url"
          value={url}
          onChange={event => setUrl(event.target.value)}
          placeholder="Paste a tutorial, notebook, Stack Overflow or YouTube link"
          id={CHECK_INPUT_ID}
          aria-label="Tutorial link"
          disabled={busy || pasting}
        />
      </label>
      {pasting && (
        <textarea
          className="lp-paste"
          rows={10}
          value={code}
          onChange={event => setCode(event.target.value)}
          onKeyDown={event => { if (event.key === 'Escape') setPasting(false) }}
          placeholder="Paste Python code or a Markdown snippet with code blocks"
          aria-label="Pasted code"
          spellCheck={false}
          autoFocus
          disabled={busy}
        />
      )}
      <div className="lp-actions">
        <input
          ref={fileInput}
          type="file"
          accept=".ipynb"
          hidden
          onChange={event => {
            submitFile(event.target.files?.[0])
            event.target.value = ''
          }}
        />
        <button type="button" className="lp-btn lp-btn-ghost" disabled={busy} onClick={() => fileInput.current?.click()}>
          <Upload aria-hidden="true" />Upload .ipynb
        </button>
        <button
          type="button"
          className="lp-btn lp-btn-ghost"
          disabled={busy}
          aria-expanded={pasting}
          onClick={() => setPasting(open => !open)}
        >
          <ClipboardPaste aria-hidden="true" />Paste code
        </button>
        <button type="submit" className="lp-btn lp-btn-primary" disabled={busy || !canCheck}>
          {busy ? 'Starting check…' : <>Check<ArrowRight aria-hidden="true" /></>}
        </button>
      </div>
      {error && <p className="lp-error" role="alert">{error}</p>}
    </form>
  )
}
