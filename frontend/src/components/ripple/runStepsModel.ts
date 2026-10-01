import type { ExecutionStep } from '../../api/types'
import { durationMs, formatDuration } from '../../shared/format'

export type StepState = 'done' | 'failed' | 'active' | 'queued' | 'skipped'

export interface StepView {
  id: string
  state: StepState
  label: string
  duration: string | null
  error: string | null
}

function stateOf(status: string): StepState {
  switch (status) {
    case 'succeeded': return 'done'
    case 'failed': return 'failed'
    case 'running': return 'active'
    case 'skipped': return 'skipped'
    default: return 'queued'
  }
}

/** The run's recorded steps, in dependency order, described for display. Only recorded facts are
 *  shown: a step is "active" only if the platform recorded it as running. */
export function describeSteps(steps: ExecutionStep[], modelName: (versionId: string | null) => string): StepView[] {
  return [...steps]
    .sort((a, b) => a.step_order - b.step_order)
    .map((s) => {
      const state = stateOf(s.status)
      const name = modelName(s.model_version_id)
      const label =
        state === 'done' ? `Ran ${name}` :
        state === 'failed' ? `${name} Failed` :
        state === 'active' ? `Running ${name}...` :
        state === 'skipped' ? `${name} Skipped` :
        `${name} Queued`
      return {
        id: s.id,
        state,
        label,
        duration: state === 'done' || state === 'failed' ? formatDuration(durationMs(s.started_at, s.finished_at)) : null,
        error: s.error_message,
      }
    })
}

export function runSummary(views: StepView[]): { text: string; tone: 'good' | 'err' | 'neutral' } {
  const failed = views.find((v) => v.state === 'failed')
  if (failed) return { text: `Run Failed · ${failed.label}`, tone: 'err' }
  if (views.length && views.every((v) => v.state === 'done' || v.state === 'skipped')) {
    const n = views.filter((v) => v.state === 'done').length
    return { text: `Run Complete · ${n} Model${n === 1 ? '' : 's'} Recomputed`, tone: 'good' }
  }
  return { text: 'Run In Progress', tone: 'neutral' }
}
