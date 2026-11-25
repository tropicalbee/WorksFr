import type { RecentCheck } from '@/api/checkSource'

export type FeaturedExample = { check_id: string; label: string; kind: string }

export type TutorialCaseStudy = {
  check_id: string
  title: string
  site: string
  language: string
  url: string
  reach: string
  cause: string
  cause_plain?: string
  broken: number
  fixed: number
  fixed_unverified: number
  unverified_reason?: string | null
  cant_verify: number
  hit_time_limit: boolean
}

export type CaseStudyData = {
  run_date: string
  selection?: string
  reach_note?: string
  featured_examples?: FeaturedExample[]
  versions: { pandas: string }
  tutorials: TutorialCaseStudy[]
  tutorial_totals: { count: number; sites: number; languages: number; library_breaks: number; fixed_steps: number }
  agent_test: {
    model: string
    snippets: number
    broke: number
    fixed: number
    hidden_removed_api: number
    cant_verify?: number
    broken_apis: string[]
  }
  mcp_agent: {
    agent: string
    model: string
    check_id: string
    prompt: string
    setup_line: string
    tool_calls: number
    seconds: number
    result: string
    without_setup_line: string
    replacement_check_ids?: [first: string, second: string]
  }
}

export function formatCaseStudyStats(
  tutorials: Pick<CaseStudyData['tutorial_totals'], 'library_breaks' | 'count' | 'fixed_steps'>,
  agent: Pick<CaseStudyData['agent_test'], 'broke' | 'hidden_removed_api' | 'snippets'>,
) {
  return {
    tutorialsBroken: `${tutorials.library_breaks} of ${tutorials.count}`,
    stepsFixed: String(tutorials.fixed_steps),
    snippetsUsingRemovedApis: `${agent.broke + agent.hidden_removed_api} of ${agent.snippets}`,
  }
}

function sourceHost(source: string): string {
  return URL.canParse(source) ? new URL(source).hostname.replace(/^www\./, '') : ''
}

function exampleKind(check: RecentCheck): string {
  if (check.source_kind === 'paste') return 'Paste'
  if (check.source_kind === 'notebook') return 'Notebook'
  const host = sourceHost(check.source)
  return host === 'youtu.be' || /(^|\.)youtube\.com$/.test(host) ? 'YouTube' : 'Article'
}

export function selectFeaturedExamples(featured: FeaturedExample[] | undefined, recent: RecentCheck[]): FeaturedExample[] {
  if (featured) return featured.slice(0, 4)
  return recent.slice(0, 4).map(check => {
    const title = check.title || check.source
    const label = check.showcase_label?.trim() || (title.length > 40 ? `${title.slice(0, 39).trimEnd()}…` : title)
    return { check_id: check.id, label, kind: exampleKind(check) }
  })
}

export function formatCheckCount(count?: number): string {
  return count ? String(count) : '–'
}

function relativeTime(iso: string, now: number): string {
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return ''
  const seconds = Math.max(0, Math.round((now - then) / 1000))
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.floor(hours / 24)
  return days === 1 ? 'Yesterday' : `${days} days ago`
}

export function formatRecentCheck(check: RecentCheck, now: number) {
  const { score, status, source, source_kind: sourceKind } = check
  return {
    title: check.showcase_label?.trim() || check.title || 'Untitled tutorial',
    host: sourceKind === 'notebook' ? 'uploaded notebook' : sourceKind === 'paste' ? 'pasted code' : sourceHost(source) || source,
    works: formatCheckCount(score?.works),
    broken: formatCheckCount(score?.broken),
    fixed: formatCheckCount(score?.fixed),
    unverified: score?.fixed_unverified ?? 0,
    cantVerify: formatCheckCount(score?.cant_verify),
    when: relativeTime(check.created_at, now),
    status: !score && status === 'running' ? 'Running' : !score && status === 'failed' ? 'Stopped' : '',
  }
}

export async function loadCaseStudies(signal: AbortSignal): Promise<CaseStudyData> {
  const response = await fetch('/showcase/case-studies.json', { signal })
  if (!response.ok) throw new Error(String(response.status))
  return response.json() as Promise<CaseStudyData>
}
