import { Link } from 'react-router'
import { ApiError } from '../../api/client'
import { useExplanation, useExposedOutputs, useFederationMap, useRunScenario } from '../../api/queries'
import type { Explanation } from '../../api/types'
import { useScope } from '../../app/scope'
import { useSelection } from '../../app/selection'
import { formatDateTime, formatPct, formatTransition, formatValue, sourceLabel } from '../../shared/format'
import { ImpactGraph, sourcesFor } from '../../shared/ImpactGraph'
import { Callout, Card, EmptyState, ErrorState, Loading, PageHeader, StatusBadge, TechnicalDetails } from '../../shared/ui'
import { headlineMetrics, joinNames, KpiCard } from '../common'

function relative(from: unknown, to: unknown): number | null {
  return typeof from === 'number' && typeof to === 'number' && from !== 0 ? (to - from) / from : null
}

function NarrativeSummary({ ex }: { ex: Explanation }) {
  const changes = ex.changes
  const changedOutputs = ex.comparison.metrics.filter((m) => m.changed && m.terminal)
  const unchangedOutputs = ex.comparison.metrics.filter((m) => !m.changed && m.terminal)
  if (!changes.length) return null

  const inputParts = changes.map((c) => {
    const rel = relative(c.baseline_value, c.scenario_value)
    return `${c.field_label} ${rel !== null && rel > 0 ? 'increased' : rel !== null && rel < 0 ? 'decreased' : 'changed'} ${rel !== null ? `by ${formatPct(Math.abs(rel))}` : ''} (${formatTransition(c.baseline_value, c.scenario_value, c.unit)})`
  })

  const movedParts = changedOutputs.map((m) =>
    `${m.field_label} ${m.direction === 'increase' ? 'increased' : m.direction === 'decrease' ? 'decreased' : 'changed'} from ${formatValue(m.baseline, m.unit)} to ${formatValue(m.scenario, m.unit)}`
  )

  const steadyNames = unchangedOutputs.map((m) => m.field_label)

  return (
    <Callout tone="neutral" title="Impact summary">
      <p style={{ margin: 0 }}>
        {joinNames(inputParts)}.{' '}
        {movedParts.length ? <>As a result, {joinNames(movedParts)}.</> : null}{' '}
        {steadyNames.length ? <>{joinNames(steadyNames)} {steadyNames.length === 1 ? 'remains' : 'remain'} unchanged.</> : null}
      </p>
    </Callout>
  )
}

export function WhySummary({ ex }: { ex: Explanation }) {
  const changed = ex.changes.map((c) => c.field_label)
  return (
    <div>
      {ex.path.map((step, i) => (
        <div key={step.version_id} className="why-step">
          <span className="why-num">{i + 1}</span>
          <div>
            <strong>{step.model_name}</strong> reads {joinNames(step.reads_changed.map((r) => r.field_label))}
            {step.outputs_changed.length ? (
              <>, so {joinNames(step.outputs_changed.map((o) => o.field_label))} changed.</>
            ) : (
              <>, but none of its results moved.</>
            )}
            {step.outputs_unchanged.length && step.outputs_changed.length ? (
              <div className="small muted">Unchanged: {joinNames(step.outputs_unchanged.map((o) => o.field_label))}.</div>
            ) : null}
          </div>
        </div>
      ))}
      {ex.unaffected.length ? (
        <p className="small muted" style={{ marginTop: 8 }}>
          {joinNames(ex.unaffected.map((u) => u.model_name))} {ex.unaffected.length === 1 ? 'does' : 'do'} not read {joinNames(changed)}, so {ex.unaffected.length === 1 ? 'its results are' : 'their results are'} unchanged by design.
        </p>
      ) : null}
    </div>
  )
}

export function DecisionPage() {
  const { baseline, scenario, workspace, loading, error, refetch } = useSelection()
  const { scope } = useScope()
  const ex = useExplanation(scenario?.run ? scenario.id : null)
  const run = useRunScenario()
  const exposed = useExposedOutputs()
  const map = useFederationMap()

  if (loading) return <Loading lines={6} />
  if (error) return <ErrorState error={error} onRetry={refetch} />
  if (!workspace || !baseline) {
    return (
      <div className="stack-lg">
        <PageHeader title="Decision Overview" />
        <EmptyState title="No baseline in this scope yet">
          A baseline is an authoritative run of this organization's models. Once one exists, this page compares scenarios against it.
        </EmptyState>
      </div>
    )
  }

  const metrics = ex.data ? headlineMetrics(ex.data.comparison.metrics) : []
  const notRun = !scenario?.run || (ex.error instanceof ApiError && ex.error.status === 409)
  const upstream = map.data ? sourcesFor(map.data, baseline.target_version_id) : null
  const sources = workspace.assumptions.filter((d) => !upstream || upstream.has(d.id))

  return (
    <div className="stack-lg">
      <PageHeader
        title="Decision Overview"
        description="The baseline you are comparing against, the scenario you are evaluating, what changed, and what that did to the results."
        actions={<Link className="btn" to="/scenarios">Change assumptions</Link>}
      />

      <Card>
        <div className="grid-3">
          <div>
            <div className="section-title">Scope</div>
            <div style={{ fontWeight: 600 }}>{scope?.name ?? 'Local workspace'}</div>
          </div>
          <div>
            <div className="section-title">Baseline</div>
            <div style={{ fontWeight: 600 }}>{baseline.name}</div>
            <div className="row small muted" style={{ gap: 6 }}>
              {baseline.run ? <><StatusBadge status={baseline.run.status} /> {formatDateTime(baseline.run.finished_at)}</> : 'Not run yet'}
            </div>
          </div>
          <div>
            <div className="section-title">Scenario</div>
            <div style={{ fontWeight: 600 }}>{scenario?.name ?? 'None yet'}</div>
            <div className="row small muted" style={{ gap: 6 }}>
              {scenario?.run ? <><StatusBadge status={scenario.run.status} /> {formatDateTime(scenario.run.finished_at)}</> : scenario ? 'Not run yet' : ''}
            </div>
          </div>
        </div>
      </Card>

      {!scenario ? (
        <EmptyState title="No scenario for this baseline" action={<Link className="btn btn-primary" to="/scenarios">Create a scenario</Link>}>
          A scenario changes one or more assumptions and runs the same models without changing shared data.
        </EmptyState>
      ) : (
        <>
          <Card title="What changed" subtitle="Assumptions this scenario overrides. The baseline value is what the baseline run actually used.">
            {scenario.overrides.length ? (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Assumption</th><th>Dataset</th><th className="num">Baseline → scenario</th><th className="num">Change</th></tr></thead>
                  <tbody>
                    {scenario.overrides.map((o) => (
                      <tr key={o.id}>
                        <td><strong>{o.field_label}</strong></td>
                        <td className="muted">{o.dataset_label}</td>
                        <td className="num">{formatTransition(o.baseline_value, o.value, o.unit)}</td>
                        <td className="num">{formatPct(relative(o.baseline_value, o.value))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="muted">This scenario does not change any assumption yet.</p>}
          </Card>

          {notRun ? (
            <EmptyState title="Run the scenario to see its impact"
              action={<button className="btn btn-primary" disabled={run.isPending} onClick={() => run.mutate(scenario.id)}>{run.isPending ? 'Running…' : 'Run scenario'}</button>}>
              Scenario runs never change shared data and never trigger downstream updates.
            </EmptyState>
          ) : ex.isLoading ? <Loading lines={5} /> : ex.error ? <ErrorState error={ex.error} onRetry={() => void ex.refetch()} /> : ex.data ? (
            <>
              <section aria-labelledby="impact-h">
                <div className="row-between" style={{ marginBottom: 12 }}>
                  <h2 id="impact-h">Resulting impact</h2>
                  <span className="small muted">{ex.data.comparison.changed_count} of {ex.data.comparison.metrics.length} recorded results changed</span>
                </div>
                <NarrativeSummary ex={ex.data} />
                <div className="grid-4" style={{ marginTop: 12 }}>
                  {metrics.map((m) => <KpiCard key={`${m.dataset_id}:${m.field}`} metric={m} />)}
                </div>
              </section>

              {map.data ? (
                <section aria-labelledby="ripple-h" className="ripple-canvas">
                  <div className="row-between" style={{ marginBottom: 12 }}>
                    <h2 id="ripple-h">How The Change Rippled Through The Models</h2>
                    <span className="small muted">Source data → models → results · the highlighted path is what this change touched.</span>
                  </div>
                  <ImpactGraph map={map.data} focusVersionId={baseline.target_version_id} explanation={ex.data} />
                </section>
              ) : null}

              <div className="grid-2">
                <Card title="Why this changed" actions={<Link className="btn btn-sm" to="/impact">See the path</Link>}>
                  <WhySummary ex={ex.data} />
                </Card>
                <Card title="Where the data came from" actions={<Link className="btn btn-sm" to="/sources">Sources & provenance</Link>}>
                  <div className="stack-sm">
                    {sources.map((d) => (
                      <div key={d.id}>
                        <strong>{d.label}</strong>
                        <div className="small muted">
                          {d.current_source ? `${sourceLabel(d.current_source.source_type)} · ${formatDateTime(d.current_source.at)}` : 'No recorded source'}
                        </div>
                      </div>
                    ))}
                    <Callout tone="neutral">Scenario values are applied in memory for the scenario run only. They are never written to these datasets.</Callout>
                  </div>
                </Card>
              </div>
              <div className="grid-2">
                <Card title="What happened during execution" actions={<Link className="btn btn-sm" to="/execution">Execution & governance</Link>}>
                  <div className="stack-sm small">
                    <div className="row"><StatusBadge status={baseline.run?.status} /> Baseline run · {baseline.run?.executor === 'in_process' ? 'In-process executor' : baseline.run?.executor} · {formatDateTime(baseline.run?.finished_at)}</div>
                    <div className="row"><StatusBadge status={scenario.run?.status} /> Scenario run · read-only · {formatDateTime(scenario.run?.finished_at)}</div>
                    <div className="muted">{ex.data.path.length} model{ex.data.path.length === 1 ? '' : 's'} on the change path, {ex.data.unaffected.length} unaffected.</div>
                  </div>
                  <TechnicalDetails items={{ 'Baseline run': baseline.run?.id, 'Scenario run': scenario.run?.id, Scenario: scenario.id, Baseline: baseline.id }} />
                </Card>
                <Card title="What is shared" actions={<Link className="btn btn-sm" to="/governance">Governance</Link>}>
                  <p className="small">
                    Scenario results are <strong>never</strong> shared automatically. Only fields that someone with approval rights has explicitly approved leave this scope.
                  </p>
                  <p className="small muted" style={{ marginTop: 8 }}>
                    {exposed.data ? `${exposed.data.length} approved output${exposed.data.length === 1 ? ' is' : 's are'} currently shared from this scope.` : 'Loading sharing status…'}
                  </p>
                </Card>
              </div>
            </>
          ) : null}
        </>
      )}
    </div>
  )
}
