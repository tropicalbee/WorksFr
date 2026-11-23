import { useParams } from 'react-router'
import { ReportView } from '@/components/report/ReportView'
import { SiteHeader } from '@/components/SiteHeader'
import { useLiveCheck } from '@/live/useLiveCheck'

export default function CheckPage() {
  const { id = '' } = useParams<{ id: string }>()
  const state = useLiveCheck(id)

  if (state.status === 'ready') {
    return <ReportView report={state.check.report} liveState={state.check.live} />
  }

  const message = state.status === 'loading'
    ? 'Loading the check'
    : state.status === 'not_found'
      ? 'No check with this id'
      : state.httpStatus !== null
        ? `Couldn't load this check (HTTP ${state.httpStatus})`
        : "Couldn't load this check"

  return (
    <main className="report-page">
      <SiteHeader />
      <section className="failure-card" aria-live="polite">
        <p className="metadata">{message}</p>
      </section>
    </main>
  )
}
