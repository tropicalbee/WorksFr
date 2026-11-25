import { useEffect, useState } from 'react'
import type { ThemedToken } from 'shiki/core'
import type { EventBlock } from '@/api/types'
import { codeLanguageByKind, loadCodeHighlighter } from '@/lib/highlighter'

export function CodeBlock({ code, kind, quiet = false }: { code: string; kind: EventBlock['kind']; quiet?: boolean }) {
  const language = codeLanguageByKind[kind]
  const [highlighted, setHighlighted] = useState<{ code: string; language: string; lines: ThemedToken[][] } | null>(null)
  useEffect(() => {
    let active = true
    if (language && !quiet) {
      void loadCodeHighlighter(language).then(h => {
        if (active) setHighlighted({ code, language, lines: h.codeToTokens(code, { lang: language, theme: 'worksfr' }).tokens })
      }).catch(() => { /* Plain code remains readable if highlighting is unavailable. */ })
    }
    return () => { active = false }
  }, [code, language, quiet])
  const tokens = !quiet && highlighted?.code === code && highlighted.language === language ? highlighted.lines : undefined

  return <div className={`code-panel${quiet ? ' quiet' : ''}`} tabIndex={0} role="region" aria-label="Code">
    <pre><code>{code.split('\n').map((line, index) => <span className="code-line" key={index}>
      <span className="line-number" aria-hidden="true">{index + 1}</span>
      <span>{tokens?.[index]?.map((token, i) => <span key={i} style={{ color: token.color, fontStyle: token.fontStyle && token.fontStyle & 1 ? 'italic' : undefined }}>{token.content}</span>) ?? line}{'\n'}</span>
    </span>)}</code></pre>
  </div>
}
