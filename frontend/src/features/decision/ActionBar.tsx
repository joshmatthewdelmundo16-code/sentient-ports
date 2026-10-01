import { Link } from 'react-router'
import type { RunBrief } from '../../api/types'
import { buttonClass, LoadingButton } from '../../components/ui/Button'
import { Icon } from '../../components/ui/Icon'
import { executorLabel, formatDateTime } from '../../shared/format'
import { StatusBadge } from '../../shared/ui'

/** Sticky bar at the foot of the page (reference): run state on the left, actions on the right. */
export function ActionBar({ run, running, canRun, onRun }: {
  run: RunBrief | null
  running: boolean
  canRun: boolean
  onRun: () => void
}) {
  return (
    <div className="panel action-bar" role="region" aria-label="Run Controls">
      <div className="panel-body">
        <div className="ab-status" aria-live="polite">
          {running ? (
            <div className="stat-line"><span className="spin" aria-hidden="true" /> Running Scenario...</div>
          ) : run ? (
            <div className="stat-line"><StatusBadge status={run.status} /> Last Run {formatDateTime(run.finished_at ?? run.started_at)} · {executorLabel(run.executor)}</div>
          ) : (
            <div className="stat-line">Not Run Yet · Run The Scenario To See Its Impact</div>
          )}
        </div>
        <Link className={buttonClass('outline')} to="/scenarios"><Icon name="layers" size={15} /> Compare Baseline</Link>
        <LoadingButton
          variant="primary"
          icon="play"
          loading={running}
          loadingLabel="Running Scenario..."
          disabled={!canRun}
          title={canRun ? 'Scenario runs are read-only: they never change shared data' : 'Your role in this scope can view scenarios but not run them'}
          onClick={onRun}
          data-testid="run-scenario"
        >
          Run Scenario
        </LoadingButton>
      </div>
    </div>
  )
}
