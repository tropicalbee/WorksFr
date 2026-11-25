import { ArrowRight, CircleX } from 'lucide-react'
import type { CheckReport } from '@/api/types'

export function ReportEnvironment({ report }: { report: CheckReport }) {
  const versions = Object.entries(report.versions ?? {})
  const failedPackages = report.failed_packages ?? []
  const pins = report.install_pins ?? []
  if (!versions.length && !failedPackages.length && !pins.length) return null

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
        {pins.length > 0 && (
          <div className="tutorial-pins">
            <h3>Tutorial pins</h3>
            <ul>
              {pins.map(pin => (
                <li key={`${pin.step_idx}-${pin.ecosystem}-${pin.package}-${pin.pinned}`}>
                  <code className="pin-old">{pin.package}{pin.pinned}</code>
                  <ArrowRight aria-hidden="true" />
                  <span className="pin-used">
                    <span className="metadata">Checked with</span>{' '}
                    {pin.used ? <code>{pin.package} {pin.used}</code> : <span className="metadata">not installed</span>}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
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
