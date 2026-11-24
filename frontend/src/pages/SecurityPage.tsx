import { LegalLayout } from '@/components/LegalLayout'

export default function SecurityPage() {
  return (
    <LegalLayout title="Security">
      <h2>The sandbox</h2>
      <p>Tutorial code is untrusted. It runs only in a Docker container, never in the backend or on your machine.</p>
      <ul>
        <li>The container runs as a non-root user with all capabilities dropped.</li>
        <li>The network is cut before any tutorial code runs.</li>
        <li>Memory, CPU and process limits apply, and every step and every check has a timeout.</li>
        <li>The container is removed when the check ends.</li>
      </ul>
      <h2>Keys</h2>
      <p>Configured keys are redacted from error messages and from extracted code before they are stored or sent on.</p>
      <h2>Reporting a problem</h2>
      <p>
        Open an issue at{' '}
        <a href="https://github.com/tropicalbee/WorksFr" target="_blank" rel="noopener noreferrer">
          github.com/tropicalbee/WorksFr
        </a>.
      </p>
    </LegalLayout>
  )
}
