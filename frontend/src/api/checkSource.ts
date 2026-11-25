import type { CheckReport } from './types'

export const isShowcase = import.meta.env.VITE_SHOWCASE === '1'
export type RecentCheck = CheckReport & { showcase_label?: string }

export async function loadRecentChecks(signal: AbortSignal): Promise<RecentCheck[]> {
  const path = isShowcase ? '/showcase/index.json' : '/api/checks'
  const response = await fetch(path, { signal })
  if (!response.ok) throw new Error(String(response.status))
  return response.json() as Promise<RecentCheck[]>
}

export function fetchCheckReport(id: string, signal: AbortSignal): Promise<Response> {
  const encoded = encodeURIComponent(id)
  const path = isShowcase ? `/showcase/checks/${encoded}.json` : `/api/checks/${encoded}`
  return fetch(path, { signal })
}

export function openCheckEvents(id: string): EventSource | null {
  return isShowcase ? null : new EventSource(`/api/checks/${encodeURIComponent(id)}/events`)
}

export function createCheck(kind: 'url' | 'code' | 'notebook', init: RequestInit): Promise<Response> {
  if (isShowcase) return Promise.reject(new Error('Showcase is read-only'))
  const path = kind === 'url' ? '/api/checks' : `/api/checks/${kind}`
  return fetch(path, { ...init, method: 'POST' })
}
