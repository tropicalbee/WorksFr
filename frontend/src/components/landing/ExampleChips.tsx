import { Link } from 'react-router'
import type { RecentCheck } from '@/api/checkSource'
import { Badge } from '@/components/ui/badge'

function sourceLabel(check: RecentCheck): string {
  if (check.source_kind === 'paste') return 'Paste'
  if (check.source_kind === 'notebook') return 'Notebook'
  const host = URL.canParse(check.source) ? new URL(check.source).hostname : ''
  return host === 'youtu.be' || /(^|\.)youtube\.com$/.test(host) ? 'YouTube' : 'Article'
}

export function ExampleChips({ checks }: { checks: RecentCheck[] }) {
  if (checks.length === 0) return null
  return (
    <div className="mt-4">
      <p className="lp-muted mb-2 text-sm">Try an example</p>
      <ul className="flex flex-wrap gap-2" aria-label="Example tutorials">
        {checks.map(check => {
          const title = check.title || check.source
          const label = check.showcase_label?.trim() || (title.length > 40 ? `${title.slice(0, 39).trimEnd()}…` : title)
          return (
            <li key={check.id} className="min-w-0 max-w-full">
              <Badge variant="outline" className="h-auto whitespace-normal focus-visible:outline-2 focus-visible:outline-offset-2" asChild>
                <Link to={`/checks/${encodeURIComponent(check.id)}`}>
                  {label} <span className="lp-muted">· {sourceLabel(check)}</span>
                </Link>
              </Badge>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
