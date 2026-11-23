import { useEffect, useState } from 'react'
import { applyEvent, initialLiveCheck } from './reducer'
import type { CheckEvent, LiveCheck } from './reducer'

type CheckLoadState =
  | { status: 'loading' }
  | { status: 'not_found' }
  | { status: 'error'; httpStatus: number | null }
  | { status: 'ready'; check: LiveCheck }

const eventTypes = [
  'extracted', 'environment', 'step_started', 'step_result',
  'repair_attempt', 'step_final', 'done', 'failed',
] as const satisfies readonly CheckEvent['type'][]

export function useLiveCheck(id: string): CheckLoadState {
  const [loaded, setLoaded] = useState<{ id: string; result: CheckLoadState } | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let disposed = false
    let stream: EventSource | null = null
    let current: LiveCheck | null = null
    const url = `/api/checks/${encodeURIComponent(id)}`

    function publish(result: CheckLoadState) {
      if (!disposed) setLoaded({ id, result })
    }

    async function load() {
      try {
        const response = await fetch(url, { signal: controller.signal })
        if (disposed) return
        if (!response.ok) {
          publish(response.status === 404
            ? { status: 'not_found' }
            : { status: 'error', httpStatus: response.status })
          return
        }
        const report = await response.json() as LiveCheck['report']
        if (disposed) return
        current = initialLiveCheck(report)
        publish({ status: 'ready', check: current })
        if (report.status !== 'running') return

        stream = new EventSource(`${url}/events`)
        for (const type of eventTypes) {
          stream.addEventListener(type, (message: MessageEvent<string>) => {
            if (disposed || !current) return
            try {
              const seq = Number(message.lastEventId)
              if (!/^\d+$/.test(message.lastEventId) || !Number.isSafeInteger(seq)) {
                throw new Error('Invalid event sequence')
              }
              const event = JSON.parse(message.data) as CheckEvent
              if (!event || event.type !== type) throw new Error('Invalid event type')
              const next = applyEvent(current, seq, event)
              if (next === current) return
              current = next
              publish({ status: 'ready', check: current })
              if (current.report.status !== 'running') stream?.close()
            } catch {
              stream?.close()
              publish({ status: 'error', httpStatus: null })
            }
          })
        }
        stream.onerror = () => {
          // CONNECTING is transient: native EventSource reconnects with Last-Event-ID.
          if (stream?.readyState === EventSource.CLOSED) {
            publish({ status: 'error', httpStatus: null })
          }
        }
      } catch {
        if (!disposed) publish({ status: 'error', httpStatus: null })
      }
    }

    void load()
    return () => {
      disposed = true
      controller.abort()
      stream?.close()
    }
  }, [id])

  return loaded?.id === id ? loaded.result : { status: 'loading' }
}
