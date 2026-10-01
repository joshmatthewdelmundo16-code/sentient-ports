import { useExecution } from '../../api/queries'
import type { MapModel } from '../../api/types'
import { Icon } from '../ui/Icon'
import { describeSteps, runSummary } from './runStepsModel'

/** Run-progress stepper (reference `.runlog`), driven by the run's recorded execution steps.
 *  While a run is in flight the platform does not report per-model progress, so only a single
 *  honest "Running Scenario..." step is shown — never a scripted sequence. */
export function RunSteps({ runId, running, models }: { runId: string | null | undefined; running: boolean; models: MapModel[] }) {
  const detail = useExecution(runId)
  const nameOf = (versionId: string | null) => models.find((m) => m.version_id === versionId)?.name ?? 'Model'

  if (running) {
    return (
      <div className="runlog" role="status" aria-label="Run Progress">
        <div className="runstep active"><span className="rs-ic"><span className="mini-spin" /></span>Running Scenario...</div>
      </div>
    )
  }
  if (!runId || detail.isLoading || detail.error || !detail.data) return null

  const views = describeSteps(detail.data.steps, nameOf)
  if (!views.length) return null
  const summary = runSummary(views)

  return (
    <div>
      <div className="sect-head" style={{ marginTop: 18 }}>Run Steps · {views.length} In Dependency Order</div>
      <div className="runlog" data-testid="run-steps">
        {views.map((v) => (
          <div key={v.id} className={`runstep ${v.state}`}>
            <span className="rs-ic">
              {v.state === 'done' ? <Icon name="check" size={12} /> : v.state === 'failed' ? <Icon name="x" size={12} /> : v.state === 'active' ? <span className="mini-spin" /> : <Icon name="dot" size={8} />}
            </span>
            <span>{v.label}{v.error ? <span className="rs-err"> — {v.error}</span> : null}</span>
            {v.duration ? <span className="rs-dur num">{v.duration}</span> : null}
          </div>
        ))}
      </div>
      <div className={`runsum ${summary.tone}`}>
        <Icon name={summary.tone === 'err' ? 'alert' : summary.tone === 'good' ? 'checkc' : 'clock'} size={15} /> {summary.text}
      </div>
    </div>
  )
}
