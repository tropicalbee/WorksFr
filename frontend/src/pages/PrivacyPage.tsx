import { LegalLayout } from '@/components/LegalLayout'

export default function PrivacyPage() {
  return (
    <LegalLayout title="Privacy">
      <p>WorksFr runs on your machine. Its authors receive nothing from it, and it has no analytics or tracking.</p>
      <h2>What stays on your machine</h2>
      <p>Checks, steps and events are stored in a local SQLite database. Secrets live only in <code>backend/.env</code>.</p>
      <h2>What leaves your machine</h2>
      <ul>
        <li>The tutorial URL is fetched directly from the site.</li>
        <li>
          The failing code, the error and the evidence gathered for it go to the LLM providers you configure
          (NVIDIA, Groq or Gemini, whichever you set).
        </li>
        <li>
          Package names go to PyPI and GitHub (release and changelog pages) to gather evidence for repairs.
        </li>
        <li>Stack Overflow links are read through the Stack Exchange API.</li>
        <li>
          The sandbox installs packages from PyPI before a run. The run itself has no network.
        </li>
        <li>
          When a page has no code because JavaScript renders it, its URL goes to the page reader you
          configure (Firecrawl or Jina Reader).
        </li>
        <li>From repair attempt 2, search queries go to Tavily, if you set a key.</li>
        <li>
          YouTube links go to Gemini, which reads the public video, and to the YouTube Data API for the
          description, if you set a key.
        </li>
      </ul>
    </LegalLayout>
  )
}
