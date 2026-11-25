import { createHighlighterCore } from 'shiki/core'
import { createJavaScriptRegexEngine } from 'shiki/engine/javascript'
import python from 'shiki/langs/python.mjs'
import type { EventBlock } from '@/api/types'

export const codeLanguageByKind = {
  python: 'python', repl: 'python', javascript: 'javascript', typescript: 'typescript', c: 'c', cpp: 'cpp',
  shell: undefined, output: undefined, other: undefined,
} as const satisfies Record<EventBlock['kind'], string | undefined>

type CodeLanguage = NonNullable<(typeof codeLanguageByKind)[EventBlock['kind']]>

const grammarLoaders = {
  javascript: () => import('shiki/langs/javascript.mjs'),
  typescript: () => import('shiki/langs/typescript.mjs'),
  c: () => import('shiki/langs/c.mjs'),
  cpp: () => import('shiki/langs/cpp.mjs'),
}

const highlighter = createHighlighterCore({
  engine: createJavaScriptRegexEngine(),
  langs: [python],
  themes: [{
    name: 'worksfr',
    type: 'dark',
    colors: { 'editor.background': '#0b0c0d', 'editor.foreground': '#d0d6e0' },
    tokenColors: [
      { scope: ['keyword', 'storage'], settings: { foreground: '#f7f8f8' } },
      { scope: ['string'], settings: { foreground: '#a7adb8' } },
      { scope: ['constant.numeric'], settings: { foreground: '#bfc4cc' } },
      { scope: ['comment'], settings: { foreground: '#62666d', fontStyle: 'italic' } },
    ],
  }],
})

export async function loadCodeHighlighter(language: CodeLanguage) {
  const instance = await highlighter
  if (language === 'python' || instance.getLoadedLanguages().includes(language)) return instance

  const { default: grammar } = await grammarLoaders[language]()
  if (!instance.getLoadedLanguages().includes(language)) instance.loadLanguageSync(grammar)
  return instance
}
