import type { components } from '@/api/schema'

export const TRANSFER_LIMIT = 200_000
const TITLE_LIMIT = 512
const URL_LIMIT = 2048
const BLOCK_LIMIT = 100_000
const BLOCK_COUNT_LIMIT = 150

export type ImportPayload = { title: string; url: string; blocks: string[] }
export type ImportResult =
  | { payload: ImportPayload }
  | { error: 'empty' | 'invalid' | 'oversized' }

function isSourceUrl(value: unknown): value is string {
  if (typeof value !== 'string' || value.length > URL_LIMIT || !/^https?:\/\//i.test(value)) {
    return false
  }
  try {
    return !/\s/.test(value) && Boolean(new URL(value).hostname)
  } catch {
    return false
  }
}

export function parseImport(hash: string): ImportResult {
  const fragment = hash.replace(/^#/, '')
  if (!fragment) return { error: 'empty' }
  if (fragment.length > TRANSFER_LIMIT) return { error: 'oversized' }
  try {
    const value: unknown = JSON.parse(decodeURIComponent(fragment))
    if (!value || typeof value !== 'object' || Object.keys(value).length !== 3 ||
      !('title' in value) || typeof value.title !== 'string' || value.title.length > TITLE_LIMIT ||
      !('url' in value) || !isSourceUrl(value.url) ||
      !('blocks' in value) || !Array.isArray(value.blocks) ||
      value.blocks.length > BLOCK_COUNT_LIMIT ||
      value.blocks.some(block => typeof block !== 'string' || block.length > BLOCK_LIMIT || !block.trim())
    ) return { error: 'invalid' }
    if (!value.blocks.length) return { error: 'empty' }
    return { payload: { title: value.title, url: value.url, blocks: value.blocks } }
  } catch {
    return { error: 'invalid' }
  }
}

export function readImportFragment(
  location: Pick<Location, 'hash' | 'pathname' | 'search'>,
  history: Pick<History, 'state' | 'replaceState'>,
): ImportResult {
  const { hash, pathname, search } = location
  if (hash) history.replaceState(history.state, '', pathname + search)
  return parseImport(hash)
}

function fencedBlock(code: string): string {
  let backticks = 3
  let tildes = 3
  for (const [run] of code.matchAll(/`+|~+/g)) {
    if (run[0] === '`') backticks = Math.max(backticks, run.length + 1)
    else tildes = Math.max(tildes, run.length + 1)
  }
  const fence = backticks <= tildes ? '`'.repeat(backticks) : '~'.repeat(tildes)
  return `${fence}\n${code}\n${fence}`
}

export function importSubmission(
  payload: ImportPayload, selected: number[],
): components['schemas']['CodeSubmission'] {
  return {
    title: payload.title,
    source_url: payload.url,
    code: selected.map(index => fencedBlock(payload.blocks[index])).join('\n\n'),
  }
}

export function createBookmarklet(origin: string): string {
  const destination = JSON.stringify(new URL('/send', origin).href + '#')
  // Keep this standalone so it runs on the tutorial page without the WorksFr bundle.
  return `javascript:(()=>{const blocks=[],seen=new Set;
for(const element of document.querySelectorAll("pre,code")){
  const code=element.textContent;
  if(!code||!code.trim()||seen.has(element.closest("pre")))continue;
  if(element.tagName==="PRE")seen.add(element);
  blocks.push(code);
}
const url=location.href;
const payload=encodeURIComponent(JSON.stringify({title:document.title.slice(0,${TITLE_LIMIT}),url,blocks}));
if(payload.length>${TRANSFER_LIMIT}||url.length>${URL_LIMIT}||blocks.length>${BLOCK_COUNT_LIMIT}||blocks.some(code=>code.length>${BLOCK_LIMIT})){
  window.alert("This page exceeds the transfer limit. Copy fewer blocks and paste them into WorksFr.");
  return;
}
window.open(${destination}+payload,"_blank","noopener,noreferrer");
})();`.replace(/\n\s*/g, '')
}
