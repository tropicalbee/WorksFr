import { LegalLayout } from '@/components/LegalLayout'

export default function TermsPage() {
  return (
    <LegalLayout title="Terms">
      <p>WorksFr is a tool you run on your own machine. It is provided as is, with no warranty.</p>
      <h2>What the results mean</h2>
      <p>
        A result comes from a sandbox run against the library versions installed at the time of the check.
        It can change as libraries change.
      </p>
      <h2>Your responsibilities</h2>
      <ul>
        <li>You are responsible for the API keys you configure and for following each provider&apos;s terms.</li>
        <li>Do not use WorksFr on code or pages you have no right to access.</li>
      </ul>
      <h2>Tutorials</h2>
      <p>
        Tutorials belong to their authors. WorksFr shows their code only to check it and does not republish it.
        WorksFr never logs in and never gets past paywalls.
      </p>
    </LegalLayout>
  )
}
