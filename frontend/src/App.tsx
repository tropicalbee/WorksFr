import { lazy, Suspense } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router'

const HomePage = lazy(() => import('./pages/HomePage'))
const CheckPage = lazy(() => import('./pages/CheckPage'))
const TermsPage = lazy(() => import('./pages/TermsPage'))
const PrivacyPage = lazy(() => import('./pages/PrivacyPage'))
const SecurityPage = lazy(() => import('./pages/SecurityPage'))
const ReportPreview = import.meta.env.DEV ? lazy(() => import('./preview/ReportPreview')) : null

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<p className="loading">Loading report…</p>}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/checks/:id" element={<CheckPage />} />
          <Route path="/terms" element={<TermsPage />} />
          <Route path="/privacy" element={<PrivacyPage />} />
          <Route path="/security" element={<SecurityPage />} />
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
