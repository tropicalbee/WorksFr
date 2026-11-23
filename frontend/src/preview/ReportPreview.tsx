import { CircleCheck, CircleX, LoaderCircle } from 'lucide-react'
import { useSearchParams } from 'react-router'
import { ReportView } from '@/components/report/ReportView'
import { previewState } from './sampleReport'

const states = [
  { value: 'done', label: 'Completed', Icon: CircleCheck },
  { value: 'running', label: 'Running', Icon: LoaderCircle },
  { value: 'failed', label: 'Failed', Icon: CircleX },
]

export default function ReportPreview() {
  const [params] = useSearchParams()
  const requestedState = params.get('state')
  const state = states.some(item => item.value === requestedState) ? requestedState : 'done'

  return (
    <ReportView
      {...previewState(state)}
      sample
      navLabel="Preview state"
      nav={states.map(({ value, label, Icon }) => ({
        label, to: `?state=${value}`, icon: Icon, current: state === value,
      }))}
    />
  )
}
