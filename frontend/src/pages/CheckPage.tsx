import { useEffect, useState } from 'react'
import { useParams } from 'react-router'
import { ReportView } from '@/components/report/ReportView'
import { SiteHeader } from '@/components/SiteHeader'
import { useLiveCheck } from '@/live/useLiveCheck'

function RateLimitCountdown({ endsAt }: { endsAt: number }) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => {
      const current = Date.now()
      setNow(current)
      if (current >= endsAt) window.clearInterval(timer)
    }, 250)
    return () => window.clearInterval(timer)
  }, [endsAt])

  const seconds = Math.max(0, Math.ceil((endsAt - now) / 1000))
  if (seconds === 0) return null

  return (
    <div className="quiet mb-6 text-[13px] lg:ml-52">
      Waiting {seconds} s for the AI provider&apos;s rate limit
    </div>
  )
}

export default function CheckPage() {
  const { id = '' } = useParams<{ id: string }>()
  const state = useLiveCheck(id)

  if (state.status === 'ready') {
    const wait = state.check.wait
    return (
      <ReportView
        report={state.check.report}
        liveState={state.check.live}
        notice={wait && <RateLimitCountdown key={wait.endsAt} endsAt={wait.endsAt} />}
      />
    )
  }

  const message = state.status === 'loading'
    ? 'Loading the check'
    : state.status === 'not_found'
      ? 'No check with this id'
      : state.httpStatus !== null
        ? `Couldn't load this check (HTTP ${state.httpStatus})`
        : "Couldn't load this check"

  return (
    <>
      <SiteHeader width="report" />
      <main className="report-page">
        <section className="failure-card" aria-live="polite">
          <p className="metadata">{message}</p>
        </section>
      </main>
    </>
  )
}
