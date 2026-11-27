type InlineToken = { kind: 'text' | 'code' | 'bold'; text: string }

export function tokenizeInlineMarkdown(text: string): InlineToken[] {
  const tokens: InlineToken[] = []
  let offset = 0

  for (const match of text.matchAll(/`([^`\r\n]+)`|\*\*([^*\r\n]+)\*\*/g)) {
    if (match.index > offset) tokens.push({ kind: 'text', text: text.slice(offset, match.index) })
    tokens.push(match[1] === undefined
      ? { kind: 'bold', text: match[2] }
      : { kind: 'code', text: match[1] })
    offset = match.index + match[0].length
  }

  if (offset < text.length) tokens.push({ kind: 'text', text: text.slice(offset) })
  return tokens
}
