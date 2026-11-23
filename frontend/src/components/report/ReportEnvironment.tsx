import { CircleX } from 'lucide-react'
import type { CheckReport } from '@/api/types'

export function ReportEnvironment({ report }: { report: CheckReport }) {
  const versions = Object.entries(report.versions ?? {})
  const failedPackages = report.failed_packages ?? []
  if (!versions.length && !failedPackages.length) return null

  return (
    <section className="environment" aria-labelledby="environment-heading">
      <div className="environment-heading">
        <h2 id="environment-heading">Checked against</h2>
        <p className="metadata">Installed in the sandbox</p>
      </div>
      <div>
        <dl className="version-list">
          {versions.map(([name, version]) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{version}</dd>
            </div>
          ))}
        </dl>
        {failedPackages.length > 0 && (
          <div className="failed-packages">
            <h3>Could not install</h3>
            <ul>
              {failedPackages.map(name => (
                <li key={name}>
                  <CircleX aria-hidden="true" />
                  {name}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  )
}
