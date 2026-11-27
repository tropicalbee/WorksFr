import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { createCheck } from '@/api/checkSource'
import type { components } from '@/api/schema'
import { SiteHeader } from '@/components/SiteHeader'
import { LandingFooter } from '@/components/landing/LandingFooter'
import { Button } from '@/components/ui/button'
import { importSubmission, readImportFragment } from '@/lib/bookmarklet'
import type { ImportPayload, ImportResult } from '@/lib/bookmarklet'
import '@/components/landing/landing.css'
import '@/components/legal.css'

export function SendPreview({
  payload, onCheck, busy = false, error,
}: {
  payload: ImportPayload
  onCheck: (selected: number[]) => void
  busy?: boolean
  error?: string
}) {
  const [included, setIncluded] = useState(() => payload.blocks.map(() => true))
  const selected = included.flatMap((checked, index) => checked ? [index] : [])

  return (
    <>
      <p className="metadata">Import preview</p>
      <h1 className="wrap-anywhere">{payload.title || 'Imported tutorial'}</h1>
      <p className="metadata wrap-anywhere">{payload.url}</p>
      <form onSubmit={event => {
        event.preventDefault()
        if (!busy && selected.length) onCheck(selected)
      }}>
        <div className="overflow-x-auto rounded-lg border border-border bg-card">
          <table className="w-full table-fixed text-left text-sm" aria-label="Imported code blocks">
            <thead className="bg-secondary">
              <tr>
                <th scope="col" className="w-16 px-3 py-2">Use</th>
                <th scope="col" className="px-3 py-2">Code preview</th>
                <th scope="col" className="w-16 px-3 py-2">Lines</th>
              </tr>
            </thead>
            <tbody>
              {payload.blocks.map((code, index) => (
                <tr className="border-t border-border align-top" key={index}>
                  <td className="px-3 py-2">
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        aria-label={`Include block ${index + 1}`}
                        checked={included[index]}
                        disabled={busy}
                        onChange={event => {
                          const checked = event.currentTarget.checked
                          setIncluded(previous => previous.map((value, i) => i === index ? checked : value))
                        }}
                      />
                      {index + 1}
                    </label>
                  </td>
                  <td className="px-3 py-2">
                    <pre className="max-h-48 overflow-auto rounded-md bg-secondary p-2" tabIndex={0} role="region" aria-label={`Code block ${index + 1}`}>{code}</pre>
                  </td>
                  <td className="px-3 py-2">{code.split(/\r\n|\r|\n/).length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-4 flex flex-wrap items-baseline justify-between gap-2">
          <p className="metadata">{busy ? 'Starting check…' : 'Nothing has run yet.'}</p>
          <Button type="submit" disabled={!selected.length || busy}>Check selected code</Button>
        </div>
        {error && <p className="lp-error" role="alert">{error}</p>}
      </form>
    </>
  )
}

export default function SendPage() {
  const navigate = useNavigate()
  const [result, setResult] = useState<ImportResult | null>(null)
  const fragmentRead = useRef(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    if (fragmentRead.current) return
    fragmentRead.current = true
    const imported = readImportFragment(window.location, window.history)
    queueMicrotask(() => setResult(imported))
  }, [])

  async function check(payload: ImportPayload, selected: number[]) {
    const body = importSubmission(payload, selected)
    if (body.code.length > 200_000) {
      setError('The selected blocks exceed the paste limit. Uncheck some blocks and try again.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const response = await createCheck('code', {
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      })
      if (!response.ok) {
        setError(`The server could not start the check (${response.status}).`)
        return
      }
      const created = await response.json() as components['schemas']['CheckCreated']
      navigate(`/checks/${encodeURIComponent(created.check_id)}`, { replace: true })
    } catch {
      setError('Could not reach the server. Check that the backend is running.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <SiteHeader />
      <div className="lp-page">
        <main className="legal !max-w-4xl">
          {!result ? <p>Reading imported code…</p> : 'payload' in result ? (
            <SendPreview
              payload={result.payload}
              onCheck={selected => void check(result.payload, selected)}
              busy={busy}
              error={error}
            />
          ) : (
            <>
              <h1>{result.error === 'oversized' ? 'Transfer too large' : 'No code received'}</h1>
              <p className="metadata">
                {result.error === 'oversized'
                  ? 'This page exceeds the 200 KB limit. Copy fewer blocks and paste them into WorksFr.'
                  : result.error === 'invalid'
                    ? 'The transfer could not be read. Open the tutorial and run the bookmarklet again.'
                    : 'Open a tutorial and run the bookmarklet again.'}
              </p>
            </>
          )}
        </main>
        <LandingFooter />
      </div>
    </>
  )
}
