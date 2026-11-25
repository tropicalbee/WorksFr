import { lazy, Suspense } from 'react'
import { BrowserRouter, Link, Route, Routes } from 'react-router'
import { isShowcase } from '@/api/checkSource'

const HomePage = lazy(() => import('./pages/HomePage'))
const CheckPage = lazy(() => import('./pages/CheckPage'))
const TermsPage = lazy(() => import('./pages/TermsPage'))
const PrivacyPage = lazy(() => import('./pages/PrivacyPage'))
const SecurityPage = lazy(() => import('./pages/SecurityPage'))
const TryPage = isShowcase ? lazy(() => import('./pages/TryPage')) : null
const ReportPreview = import.meta.env.DEV ? lazy(() => import('./preview/ReportPreview')) : null

export default function App() {
  return (
    <BrowserRouter>
      {isShowcase && (
        <aside className="border-b border-border bg-card px-4 py-2 text-center text-sm">
          Showcase: saved reports from real checks.{' '}
          <Link to="/try" className="underline underline-offset-2">Run WorksFr yourself</Link> to check your own tutorial.
        </aside>
      )}
      <Suspense fallback={<p className="loading">Loading report…</p>}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/checks/:id" element={<CheckPage />} />
          <Route path="/terms" element={<TermsPage />} />
          <Route path="/privacy" element={<PrivacyPage />} />
          <Route path="/security" element={<SecurityPage />} />
          {TryPage && <Route path="/try" element={<TryPage />} />}
          {ReportPreview && <Route path="/preview/report" element={<ReportPreview />} />}
          <Route
            path="*"
            element={
              <main className="loading">
                <h1>Page unavailable</h1>
                <p>This page is not available in this build.</p>
              </main>
            }
          />
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}
