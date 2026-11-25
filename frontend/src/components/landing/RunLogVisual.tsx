import { LoaderCircle, Timer, TriangleAlert, User, WifiOff, X } from 'lucide-react'
import type { CSSProperties } from 'react'

type Row = { code: string; tone: 'works' | 'broken' | 'dep'; label: string }

const ROWS: Row[] = [
  { code: 'pip install pandas', tone: 'works', label: 'Works' },
  { code: 'pd.read_csv("sales.csv")', tone: 'works', label: 'Works' },
  { code: 'df.append(row)', tone: 'broken', label: 'Broken' },
  { code: 'df.groupby("city").sum()', tone: 'dep', label: 'Deprecated' },
  { code: 'df.to_csv("out.csv")', tone: 'works', label: 'Works' },
  { code: 'const total = _.sum(prices)', tone: 'works', label: 'Works' },
  { code: 'printf("%d\\n", count);', tone: 'works', label: 'Works' },
]

const at = (ms: number) => ({ '--delay': `${ms}ms` }) as CSSProperties

function Mark({ tone }: { tone: Row['tone'] }) {
  if (tone === 'works') return <span className="lp-dot" />
  return tone === 'broken' ? <X aria-hidden="true" /> : <TriangleAlert aria-hidden="true" />
}

export function RunLogVisual() {
  return (
    <>
      <ol className="lp-log">
        {ROWS.map((row, index) => {
          const rowDelay = index * 442
          return (
            <li key={row.code} className="lp-reveal-y" style={at(rowDelay)}>
              <span className="lp-sn">{index + 1}</span>
              <code>{row.code}</code>
              <span className={`lp-status lp-s-${row.tone}`}>
                <span className="lp-spinner" aria-hidden="true" style={at(rowDelay + 425)}><LoaderCircle /></span>
                <span className="lp-label lp-slide-x" style={at(rowDelay + 1360)}>
                  <Mark tone={row.tone} />{row.label}
                </span>
              </span>
            </li>
          )
        })}
      </ol>
      <ul className="lp-limits">
        <li><WifiOff aria-hidden="true" />no network</li>
        <li><User aria-hidden="true" />non-root</li>
        <li><Timer aria-hidden="true" />60 s step limit</li>
      </ul>
    </>
  )
}
