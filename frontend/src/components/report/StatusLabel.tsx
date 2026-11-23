import { Circle, CircleCheck, CircleDashed, CircleX, LoaderCircle, TriangleAlert } from 'lucide-react'
import type { Label } from '@/api/types'

const statuses = {
  works: { text: 'Works', Icon: CircleCheck },
  deprecated: { text: 'Deprecated', Icon: TriangleAlert },
  broken: { text: 'Broken', Icon: CircleX },
  cant_verify: { text: "Can't verify", Icon: CircleDashed },
  running: { text: 'Running in the sandbox', Icon: LoaderCircle },
  waiting: { text: 'Waiting', Icon: Circle },
  not_run: { text: 'Not run', Icon: CircleDashed },
}

export function StatusLabel({ status, count }: { status: Label | 'running' | 'waiting' | 'not_run'; count?: number }) {
  const { text, Icon } = statuses[status]
  return <span className={`status status-${status}`}><Icon aria-hidden="true" className={status === 'running' ? 'spin' : undefined} />{count !== undefined && `${count} `}{text}</span>
}
