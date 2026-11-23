import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router'

const CheckPage = lazy(() => import('./pages/CheckPage'))
const ReportPreview = import.meta.env.DEV ? lazy(() => import('./preview/ReportPreview')) : null

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<p className="loading">Loading report…</p>}>
        <Routes>
          <Route path="/checks/:id" element={<CheckPage />} />
          {ReportPreview && (
            <>
              <Route path="/" element={<Navigate to="/preview/report" replace />} />
              <Route path="/preview/report" element={<ReportPreview />} />
            </>
          )}
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
