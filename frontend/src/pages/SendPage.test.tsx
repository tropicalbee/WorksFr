import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { BookmarkletSetup } from '@/components/BookmarkletSetup'
import { SendPreview } from './SendPage'

describe('bookmarklet setup', () => {
  it('offers a draggable link, copy button and manual setup locally', () => {
    const markup = renderToStaticMarkup(<BookmarkletSetup showcase={false} origin="http://localhost:5173" />)
    expect(markup).toContain('draggable="true"')
    expect(markup).toContain('Copy link')
    expect(markup).toContain('Set it up without dragging')
    expect(markup).toContain('<ol')
  })

  it('hides the bookmarklet on the showcase', () => {
    const markup = renderToStaticMarkup(<BookmarkletSetup showcase origin="https://showcase.test" />)
    expect(markup).toContain('a local WorksFr')
    expect(markup).not.toContain('draggable')
    expect(markup).not.toContain('Copy link')
  })
})

it('renders an opt-in table and keeps source and code as literal text', () => {
  const onCheck = vi.fn()
  const markup = renderToStaticMarkup(<SendPreview
    payload={{
      title: '<b>Tutorial</b>',
      url: 'https://example.test/?a=1&b=2',
      blocks: ['<script>doSomething()</script>', 'line1\nline2'],
    }}
    onCheck={onCheck}
  />)
  expect(markup).toContain('aria-label="Include block 1"')
  expect(markup).toContain('aria-label="Include block 2"')
  expect(markup).toContain('&lt;script&gt;doSomething()&lt;/script&gt;')
  expect(markup).toContain('&lt;b&gt;Tutorial&lt;/b&gt;')
  expect(markup).toContain('https://example.test/?a=1&amp;b=2')
  expect(markup).not.toContain('href=')
  expect(markup).toContain('Check selected code')
  expect(onCheck).not.toHaveBeenCalled()
})
