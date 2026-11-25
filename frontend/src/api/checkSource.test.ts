import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.resetModules()
})

describe('check data sources', () => {
  it('uses only static JSON with submissions and streaming disabled in a showcase', async () => {
    vi.stubEnv('VITE_SHOWCASE', '1')
    const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json([]))
    const EventSource = vi.fn()
    vi.stubGlobal('fetch', fetch)
    vi.stubGlobal('EventSource', EventSource)
    const { createCheck, fetchCheckReport, loadRecentChecks, openCheckEvents } = await import('./checkSource')
    const { signal } = new AbortController()

    await loadRecentChecks(signal)
    await fetchCheckReport('sample id', signal)
    expect(openCheckEvents('sample id')).toBeNull()
    await expect(createCheck('url', {})).rejects.toThrow('Showcase is read-only')
    expect(EventSource).not.toHaveBeenCalled()
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      '/showcase/index.json', '/showcase/checks/sample%20id.json',
    ])
  })

  it('keeps the live collection, detail, submission and event routes in a normal build', async () => {
    vi.stubEnv('VITE_SHOWCASE', undefined)
    const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json([]))
    const EventSource = vi.fn(function EventSource() {})
    vi.stubGlobal('fetch', fetch)
    vi.stubGlobal('EventSource', EventSource)
    const { createCheck, fetchCheckReport, loadRecentChecks, openCheckEvents } = await import('./checkSource')
    const { signal } = new AbortController()

    await loadRecentChecks(signal)
    await fetchCheckReport('sample id', signal)
    await createCheck('url', {})
    await createCheck('code', {})
    await createCheck('notebook', {})
    openCheckEvents('sample id')
    expect(fetch.mock.calls).toEqual([
      ['/api/checks', { signal }], ['/api/checks/sample%20id', { signal }],
      ['/api/checks', { method: 'POST' }], ['/api/checks/code', { method: 'POST' }],
      ['/api/checks/notebook', { method: 'POST' }],
    ])
    expect(EventSource).toHaveBeenCalledWith('/api/checks/sample%20id/events')
  })
})
