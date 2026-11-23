import { useEffect, useState } from 'react'
import type { ThemedToken } from 'shiki/core'
import { highlighter } from '@/lib/highlighter'

export function CodeBlock({ code, python = true, quiet = false }: { code: string; python?: boolean; quiet?: boolean }) {
  const [highlighted, setHighlighted] = useState<{ code: string; lines: ThemedToken[][] } | null>(null)
  useEffect(() => {
    let active = true
    if (python && !quiet) {
      void highlighter.then(h => {
        if (active) setHighlighted({ code, lines: h.codeToTokens(code, { lang: 'python', theme: 'worksfr' }).tokens })
      }).catch(() => { /* Plain code remains readable if highlighting is unavailable. */ })
    }
    return () => { active = false }
  }, [code, python, quiet])

  return <div className={`code-panel${quiet ? ' quiet' : ''}`} tabIndex={0} role="region" aria-label="Code">
    <pre><code>{code.split('\n').map((line, index) => <span className="code-line" key={index}>
      <span className="line-number" aria-hidden="true">{index + 1}</span>
      <span>{!quiet && highlighted?.code === code ? highlighted.lines[index]?.map((token, i) => <span key={i} style={{ color: token.color, fontStyle: token.fontStyle && token.fontStyle & 1 ? 'italic' : undefined }}>{token.content}</span>) : line}{'\n'}</span>
    </span>)}</code></pre>
  </div>
}
