import { createHighlighterCore } from 'shiki/core'
import { createJavaScriptRegexEngine } from 'shiki/engine/javascript'
import python from 'shiki/langs/python.mjs'

export const highlighter = createHighlighterCore({
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
