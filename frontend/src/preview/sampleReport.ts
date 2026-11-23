import type { CheckReport, ErrorInfo, RepairAttempt, StepReport } from '@/api/types'
import type { ReportLiveState } from '@/components/report/ReportView'

function step(idx: number, original_code: string, label: StepReport['label'], extra: Partial<StepReport> = {}): StepReport {
  return { idx, kind: 'python', original_code, final_code: original_code, label, reason: '', explanation: '', output: '', ...extra }
}

function attempt(number: number, provider: string, patched_code: string, explanation: string, apis_changed: string[], error?: Pick<ErrorInfo, 'type' | 'message'>): RepairAttempt {
  return {
    attempt: number, provider, patched_code, explanation, apis_changed,
    verdict: error ? 'failed' : 'fixed', decision: { label: error ? 'broken' : 'works', reason: '' },
    result: { id: `attempt-${number}`, status: error ? 'error' : 'ok', stdout: '', stderr: '', duration_ms: 0, error: error ? { ...error, traceback: '' } : null },
  }
}

const row = 'new_row = {"city": "Kyoto", "temp": 22}'
const appendExplanation = 'DataFrame.append was deprecated in pandas 1.4 and removed in 2.0. pd.concat joins frames instead, so the new row has to become a one-row DataFrame first.'
const appendApis = ['DataFrame.append', 'pandas.concat']
const appendPatch = `${row}\ndf = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)`
const mapExplanation = 'pandas 2.1 renamed DataFrame.applymap to DataFrame.map. The old name still runs but warns, and a later release will remove it.'
const mapApis = ['DataFrame.applymap', 'DataFrame.map']
const mapPatch = 'df[["temp"]].map(lambda x: x * 2)'
const meanExplanation = 'Before pandas 2.0, groupby().mean() silently skipped text columns. It now raises instead. numeric_only=True averages only the number columns, which matches the old result.'
const meanApis = ['DataFrameGroupBy.mean']
const meanPatch = 'df.groupby("city").mean(numeric_only=True)'

export const sampleReport: CheckReport = {
  id: 'sample', source_kind: 'url', source: 'https://example.com/pandas-tutorial',
  title: 'Pandas basics: building and reshaping a DataFrame', published: '2019-03-14', date_source: 'page', status: 'done',
  versions: { pandas: '2.2.3', numpy: '2.1.3', matplotlib: '3.9.2' }, failed_packages: [],
  score: { works: 3, deprecated: 1, broken: 2, cant_verify: 1, fixed: 2, fixed_unverified: 0, still_run: 4, checked: 6, ratio: 4 / 6 },
  steps: [
    step(0, 'pip install pandas==0.24.2 matplotlib', null, { kind: 'shell', reason: "Install commands don't run. WorksFr installed the current versions listed under Checked against." }),
    step(1, 'import pandas as pd\nimport numpy as np', 'works'),
    step(2, 'df = pd.DataFrame({"city": ["Pune", "Oslo", "Lima"], "temp": [31, 4, 19]})\ndf.head()', 'works', { output: '   city  temp\n0  Pune    31\n1  Oslo     4\n2  Lima    19' }),
    step(3, `${row}\ndf = df.append(new_row, ignore_index=True)`, 'broken', {
      error: { type: 'AttributeError', message: "'DataFrame' object has no attribute 'append'", traceback: '' },
      verdict: 'fixed', final_code: appendPatch, explanation: appendExplanation, apis_changed: appendApis,
      repair_attempts: [
        attempt(1, 'nvidia', `${row}\ndf = pd.concat([df, new_row], ignore_index=True)`, appendExplanation, appendApis, { type: 'TypeError', message: "cannot concatenate object of type '<class 'dict'>'; only Series and DataFrame objs are valid" }),
        attempt(2, 'groq', appendPatch, appendExplanation, appendApis),
      ],
    }),
    step(4, 'df[["temp"]].applymap(lambda x: x * 2)', 'deprecated', {
      warnings: [{ category: 'FutureWarning', message: 'DataFrame.applymap has been deprecated. Use DataFrame.map instead.', filename: '', module: '' }],
      verdict: 'fixed', final_code: mapPatch, explanation: mapExplanation, apis_changed: mapApis,
      repair_attempts: [attempt(1, 'groq', mapPatch, mapExplanation, mapApis)],
    }),
    step(5, 'df.groupby("city").mean()', 'broken', {
      error: { type: 'TypeError', message: 'agg function failed [how->mean,dtype->object]', traceback: '' },
      verdict: 'fixed', final_code: meanPatch, explanation: meanExplanation, apis_changed: meanApis,
      repair_attempts: [attempt(1, 'gemini', meanPatch, meanExplanation, meanApis)],
    }),
    step(6, 'sales = pd.read_csv("sales_2018.csv")', 'cant_verify', { reason: "Reads sales_2018.csv, a local file the tutorial doesn't include." }),
    step(7, 'df.plot(kind="bar", x="city", y="temp")', 'works'),
  ],
}

export function previewState(state: string | null): { report: CheckReport; liveState?: ReportLiveState } {
  if (state === 'failed') return { report: { ...sampleReport, status: 'failed', steps: [], score: null, versions: {}, error: "The sandbox couldn't install pandas==2.2.3: pip ran out of time after 300 seconds." } }
  if (state === 'running') return {
    report: { ...sampleReport, status: 'running', steps: sampleReport.steps?.slice(0, 4), score: null },
    liveState: { runningStepIndex: 4, pendingBlocks: (sampleReport.steps ?? []).slice(4).map(s => ({ idx: s.idx, kind: s.kind, code: s.original_code })) },
  }
  return { report: sampleReport }
}
