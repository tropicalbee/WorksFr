import { runInNewContext } from 'node:vm'
import { describe, expect, it, vi } from 'vitest'
import {
  createBookmarklet, importSubmission, parseImport, readImportFragment, TRANSFER_LIMIT,
} from './bookmarklet'

const payload = {
  title: 'A tutorial', url: 'https://example.test/a', blocks: ['print(1)', '<b>code</b>'],
}
const fragment = (value: unknown) => '#' + encodeURIComponent(JSON.stringify(value))

describe('bookmarklet transfer', () => {
  it('parses a valid fragment and clears it while preserving the current history state', () => {
    const replaceState = vi.fn()
    const state = { key: 'abc', idx: 0 }
    expect(readImportFragment(
      { hash: fragment(payload), pathname: '/send', search: '?test=1' },
      { state, replaceState },
    )).toEqual({ payload })
    expect(replaceState).toHaveBeenCalledExactlyOnceWith(state, '', '/send?test=1')
  })

  it.each([
    { ...payload, url: 'javascript:alert(1)' },
    { ...payload, title: 123 },
    { ...payload, blocks: [5] },
    { ...payload, blocks: ['x'.repeat(100_001)] },
    { ...payload, blocks: ['  '] },
    { ...payload, extra: true },
  ])('rejects an invalid payload', (value) => {
    expect(parseImport(fragment(value))).toEqual({ error: 'invalid' })
  })

  it('rejects oversized fragments before decoding and clears malformed input', () => {
    expect(parseImport('#' + 'x'.repeat(TRANSFER_LIMIT + 1))).toEqual({ error: 'oversized' })
    const replaceState = vi.fn()
    expect(readImportFragment(
      { hash: '#%ZZ', pathname: '/send', search: '' }, { state: null, replaceState },
    )).toEqual({ error: 'invalid' })
    expect(replaceState).toHaveBeenCalledExactlyOnceWith(null, '', '/send')
  })

  it('submits selected blocks only, using a safe Markdown fence', () => {
    expect(importSubmission({ ...payload, blocks: ['discard', 'print("```")', 'keep'] }, [1, 2]))
      .toEqual({
        title: payload.title, source_url: payload.url,
        code: '~~~\nprint("```")\n~~~\n\n```\nkeep\n```',
      })
  })

  it('collects a nested pre and code once and opens the local preview privately', () => {
    const pre = { tagName: 'PRE', textContent: 'print(1)', closest: () => null }
    const nested = { tagName: 'CODE', textContent: 'print(1)', closest: () => pre }
    const code = { tagName: 'CODE', textContent: '<b>code</b>', closest: () => null }
    const empty = { tagName: 'CODE', textContent: '  ', closest: () => null }
    const open = vi.fn()
    const alert = vi.fn()
    const bookmark = createBookmarklet('http://localhost:5173')
    expect(new TextEncoder().encode(bookmark).length).toBeLessThan(2048)
    runInNewContext(bookmark.slice('javascript:'.length), {
      document: { title: payload.title, querySelectorAll: () => [pre, nested, code, empty] },
      location: { href: payload.url }, window: { open, alert },
    })
    expect(open).toHaveBeenCalledExactlyOnceWith(
      'http://localhost:5173/send' + fragment(payload), '_blank', 'noopener,noreferrer',
    )
    expect(alert).not.toHaveBeenCalled()
  })

  it('alerts on a transfer over the limit and opens nothing', () => {
    const bookmark = createBookmarklet('http://localhost:5173')
    const open = vi.fn()
    const alert = vi.fn()
    const code = '😀'.repeat(20_000)
    runInNewContext(bookmark.slice('javascript:'.length), {
      document: {
        title: payload.title,
        querySelectorAll: () => [{ tagName: 'PRE', textContent: code, closest: () => null }],
      },
      location: { href: payload.url }, window: { open, alert },
    })
    expect(alert).toHaveBeenCalledOnce()
    expect(open).not.toHaveBeenCalled()
  })
})
