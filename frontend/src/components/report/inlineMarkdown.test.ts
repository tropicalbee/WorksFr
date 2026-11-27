import { describe, expect, it } from 'vitest'
import { tokenizeInlineMarkdown } from './inlineMarkdown'

describe('tokenizeInlineMarkdown', () => {
  it('recognizes code and bold in order', () => {
    expect(tokenizeInlineMarkdown('**API change**: use `concat()`')).toEqual([
      { kind: 'bold', text: 'API change' },
      { kind: 'text', text: ': use ' },
      { kind: 'code', text: 'concat()' },
    ])
  })

  it('keeps unmatched markers and unsupported Markdown as text', () => {
    const text = '`unclosed and **unfinished [link](https://example.com)'
    expect(tokenizeInlineMarkdown(text)).toEqual([{ kind: 'text', text }])
  })

  it('keeps HTML-looking content as text', () => {
    expect(tokenizeInlineMarkdown('<img> `</code>` **<script>**')).toEqual([
      { kind: 'text', text: '<img> ' },
      { kind: 'code', text: '</code>' },
      { kind: 'text', text: ' ' },
      { kind: 'bold', text: '<script>' },
    ])
  })
})
