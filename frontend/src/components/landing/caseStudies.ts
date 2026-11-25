export type TutorialCaseStudy = {
  check_id: string
  title: string
  site: string
  language: string
  url: string
  reach: string
  cause: string
  broken: number
  fixed: number
  fixed_unverified: number
  cant_verify: number
  hit_time_limit: boolean
}

export type CaseStudyData = {
  run_date: string
  versions: { pandas: string }
  tutorials: TutorialCaseStudy[]
  tutorial_totals: { count: number; sites: number; languages: number; library_breaks: number; fixed_steps: number }
  agent_test: {
    model: string
    snippets: number
    broke: number
    fixed: number
    hidden_removed_api: number
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

export async function loadCaseStudies(signal: AbortSignal): Promise<CaseStudyData> {
  const response = await fetch('/showcase/case-studies.json', { signal })
  if (!response.ok) throw new Error(String(response.status))
  return response.json() as Promise<CaseStudyData>
}
