import { Link } from 'react-router'
import type { BaselineView, ScenarioView } from '../../api/types'
import { AssumptionCell } from '../../components/data/AssumptionCell'
import { buttonClass } from '../../components/ui/Button'
import { Icon } from '../../components/ui/Icon'
import { formatDateTime, formatPct, formatTransition } from '../../shared/format'
import { Card, StatusBadge } from '../../shared/ui'

function relative(from: unknown, to: unknown): number | null {
  return typeof from === 'number' && typeof to === 'number' && from !== 0 ? (to - from) / from : null
}

function RunRow({ label, run }: { label: string; run: BaselineView['run'] }) {
  return (
    <div className="mrow">
      <span className="mk">{label}</span>
      <span className="mv" style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
        {run ? <><StatusBadge status={run.status} /><span className="num">{formatDateTime(run.finished_at)}</span></> : <span className="muted">Not Run Yet</span>}
      </span>
    </div>
  )
}

/** Left panel: what is being compared and which assumptions the scenario changes
 *  (reference "Decision Configuration"). Read-only here; editing happens on Scenario Comparison. */
export function ConfigPanel({ scopeName, baseline, scenario }: {
  scopeName: string
  baseline: BaselineView
  scenario: ScenarioView
}) {
  return (
    <Card title={<><Icon name="sliders" size={15} /> Decision Configuration</>} className="self-start">
      <div className="stack" style={{ gap: 15 }}>
        <div className="meta-list">
          <div className="mrow"><span className="mk">Scope</span><span className="mv">{scopeName}</span></div>
          <div className="mrow"><span className="mk">Baseline</span><span className="mv">{baseline.name}</span></div>
          <RunRow label="Baseline Run" run={baseline.run} />
          <div className="mrow"><span className="mk">Scenario</span><span className="mv">{scenario.name}</span></div>
          <RunRow label="Scenario Run" run={scenario.run} />
        </div>
        <div className="hr" />
        <div className="field">
          <span className="flabel">Changed Assumptions</span>
          {scenario.overrides.length ? (
            <div>
              {scenario.overrides.map((o) => {
                const rel = relative(o.baseline_value, o.value)
                return (
                  <AssumptionCell
                    key={o.id}
                    name={o.field_label}
                    sub={o.dataset_label}
                    value={formatTransition(o.baseline_value, o.value, o.unit)}
                    detail={rel !== null ? <span>{formatPct(rel)}</span> : undefined}
                  />
                )
              })}
            </div>
          ) : (
            <p className="muted small">This scenario does not change any assumption yet.</p>
          )}
        </div>
        <Link className={buttonClass('outline', 'sm', true)} to="/scenarios"><Icon name="sliders" size={14} /> Change Assumptions</Link>
      </div>
    </Card>
  )
}
