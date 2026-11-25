import { Link } from 'react-router'
import type { RecentCheck } from '@/api/checkSource'
import { Badge } from '@/components/ui/badge'
import type { FeaturedExample } from './caseStudies'
import { selectFeaturedExamples } from './caseStudies'

export function ExampleChips({ featured, checks }: { featured?: FeaturedExample[]; checks: RecentCheck[] }) {
  const examples = selectFeaturedExamples(featured, checks)
  if (examples.length === 0) return null
  return (
    <div className="lp-example-chips">
      <p className="lp-muted mb-2">Try an example</p>
      <ul className="flex flex-wrap gap-2" aria-label="Example tutorials">
        {examples.map(({ check_id: id, label, kind }) => (
          <li key={id} className="min-w-0 max-w-full">
            <Badge variant="outline" className="lp-example-chip" asChild>
              <Link to={`/checks/${encodeURIComponent(id)}`}>
                {label} <span className="lp-muted">· {kind}</span>
              </Link>
            </Badge>
          </li>
        ))}
      </ul>
    </div>
  )
}
