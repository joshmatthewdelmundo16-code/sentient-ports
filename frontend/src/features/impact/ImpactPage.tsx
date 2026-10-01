import { Link } from 'react-router'
import { useExplanation, useFederationMap } from '../../api/queries'
import { useSelection } from '../../app/selection'
import { formatPct, formatTransition } from '../../shared/format'
import { ImpactGraph } from '../../shared/ImpactGraph'
import { Callout, Card, EmptyState, ErrorState, Loading, PageHeader } from '../../shared/ui'
import { joinNames } from '../common'

export function ImpactPage() {
  const { baseline, scenario } = useSelection()
  const map = useFederationMap()
  const ex = useExplanation(scenario?.run ? scenario.id : null)
  const formula = (vid: string) => map.data?.models.find((m) => m.version_id === vid)?.formula ?? null
  const metric = (datasetId: string, field: string) =>
    ex.data?.comparison.metrics.find((m) => m.dataset_id === datasetId && m.field === field)

  return (
    <div className="stack-lg">
      <PageHeader
        title="Impact & Why"
        description="How a change travels through the models. Each step is derived from the models' declared inputs and the recorded results — not written by hand."
      />
      {map.isLoading ? <Loading lines={5} /> : map.error ? <ErrorState error={map.error} /> : map.data ? (
        <Card
          title="Dependency and impact path"
          subtitle={ex.data ? `Highlighted: the path from ${joinNames(ex.data.changes.map((c) => c.field_label))} to the results it changed.` : 'Every model the baseline depends on, in dependency order.'}
        >
          <ImpactGraph map={map.data} focusVersionId={baseline?.target_version_id} explanation={ex.data ?? null} />
          <div className="legend" style={{ marginTop: 10 }}>
            <span><span className="legend-swatch" style={{ background: 'var(--up)' }} />On the change path</span>
            <span><span className="legend-swatch" style={{ background: 'var(--line-strong)' }} />Not affected by this change</span>
          </div>
        </Card>
      ) : null}

      {!scenario?.run ? (
        <EmptyState title="Run a scenario to trace its impact" action={<Link className="btn" to="/scenarios">Scenario comparison</Link>} />
      ) : ex.isLoading ? <Loading /> : ex.error ? <ErrorState error={ex.error} /> : ex.data ? (
        <div className="grid-2">
          <Card title="Why, step by step">
            <div className="why-step">
              <span className="why-num">0</span>
              <div>
                <strong>The scenario changes</strong>{' '}
                {ex.data.changes.map((c, i) => (
                  <span key={c.field}>{i ? '; ' : ''}{c.field_label}: <span className="num">{formatTransition(c.baseline_value, c.scenario_value, c.unit)}</span></span>
                ))}.
              </div>
            </div>
            {ex.data.path.map((step, i) => (
              <div key={step.version_id} className="why-step">
                <span className="why-num">{i + 1}</span>
                <div className="stack-sm">
                  <div><strong>{step.model_name}</strong> reads {joinNames(step.reads_changed.map((r) => r.field_label))}.</div>
                  {formula(step.version_id) ? <div className="formula">{formula(step.version_id)}</div> : null}
                  {step.outputs_changed.map((o) => {
                    const m = metric(o.dataset_id, o.field)
                    return (
                      <div key={`${o.dataset_id}:${o.field}`} className="small">
                        → <strong>{o.field_label}</strong>{m ? <>: <span className="num">{formatTransition(m.baseline, m.scenario, m.unit)}</span> ({formatPct(m.relative_delta)})</> : null}
                      </div>
                    )
                  })}
                  {step.outputs_unchanged.length ? <div className="tiny muted">Unchanged: {joinNames(step.outputs_unchanged.map((o) => o.field_label))}</div> : null}
                </div>
              </div>
            ))}
          </Card>
          <div className="stack">
            <Card title="Not affected">
              {ex.data.unaffected.length ? (
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                  {ex.data.unaffected.map((u) => (
                    <li key={u.version_id}><strong>{u.model_name}</strong> does not read {joinNames(ex.data!.changes.map((c) => c.field_label))}.</li>
                  ))}
                </ul>
              ) : <p className="small muted">Every model in this graph is on the change path.</p>}
            </Card>
            <Callout tone="neutral" title="How this is derived.">
              A model is on the path only if its declared input binding passes a changed field. A result is attributed to it only if the two runs' recorded values actually differ.
            </Callout>
          </div>
        </div>
      ) : null}
    </div>
  )
}
