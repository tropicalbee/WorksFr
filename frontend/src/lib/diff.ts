export type DiffLine = { kind: 'context' | 'removed' | 'added'; text: string; oldLine?: number; newLine?: number }

// Match common lines, then keep one line of context around each changed region.
export function diffLines(original: string, replacement: string): DiffLine[][] {
  const before = original.split('\n')
  const after = replacement.split('\n')
  // Bound the table for unusually large tutorial cells; the fallback is still exact.
  if (before.length * after.length > 250_000) {
    return [[...before.map((text, i): DiffLine => ({ kind: 'removed', text, oldLine: i + 1 })), ...after.map((text, i): DiffLine => ({ kind: 'added', text, newLine: i + 1 }))]]
  }
  const lengths = Array.from({ length: before.length + 1 }, () => new Uint32Array(after.length + 1))
  for (let i = before.length - 1; i >= 0; i--) {
    for (let j = after.length - 1; j >= 0; j--) lengths[i][j] = before[i] === after[j] ? 1 + lengths[i + 1][j + 1] : Math.max(lengths[i + 1][j], lengths[i][j + 1])
  }
  const lines: DiffLine[] = []
  let i = 0, j = 0
  while (i < before.length || j < after.length) {
    if (i < before.length && j < after.length && before[i] === after[j]) {
      lines.push({ kind: 'context', text: before[i], oldLine: ++i, newLine: ++j })
    } else if (i < before.length && (j === after.length || lengths[i + 1][j] >= lengths[i][j + 1])) {
      lines.push({ kind: 'removed', text: before[i], oldLine: ++i })
    } else lines.push({ kind: 'added', text: after[j], newLine: ++j })
  }
  const groups: DiffLine[][] = []
  let group: DiffLine[] = []
  lines.forEach((line, index) => {
    if (
      line.kind !== 'context' ||
      (lines[index - 1]?.kind !== 'context' && index > 0) ||
      (lines[index + 1]?.kind !== 'context' && index + 1 < lines.length)
    ) group.push(line)
    else if (group.length) { groups.push(group); group = [] }
  })
  if (group.length) groups.push(group)
  return groups
}
