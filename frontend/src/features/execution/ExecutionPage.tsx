import { useSearchParams } from 'react-router'
import { useExecution, useExecutions, useExposedOutputs, useRunResults } from '../../api/queries'
import type { ExecutionRun } from '../../api/types'
import { useSelection } from '../../app/selection'
import { durationMs, formatDateTime, formatDuration, formatValue, statusLabel } from '../../shared/format'
import { Badge, Callout, Card, EmptyState, ErrorState, Loading, PageHeader, StatusBadge, TechnicalDetails } from '../../shared/ui'
import { useCatalogIndex, type CatalogIndex } from '../common'

function describeRun(r: ExecutionRun, index: CatalogIndex | null, names: { scenarios: Map<string, string>; baselines: Map<string, string> }) {
  if (r.scenario_id) return { kind: 'Scenario run', subject: names.scenarios.get(r.scenario_id) ?? 'Scenario' }
  if (r.trigger_type === 'baseline') return { kind: 'Baseline run', subject: names.baselines.get(r.target_version_id ?? '') ?? 'Baseline' }
  if (r.trigger_type === 'dataset_change') return { kind: 'Propagation run', subject: r.triggered_by?.startsWith('ingestion:') ? 'After a workbook upload' : 'After a dataset change' }
  if (r.trigger_type === 'plan') return { kind: 'Plan period run', subject: index?.modelName(r.target_version_id) ?? 'Plan' }
  return { kind: 'Model run', subject: index?.modelName(r.target_version_id) ?? 'Model' }
}

function executorLabel(e: string) {
  return e === 'in_process' ? 'In-process executor' : e === 'airflow' ? 'Airflow (external executor)' : e
}

function RunDetail({ runId, index }: { runId: string; index: CatalogIndex | null }) {
  const detail = useExecution(runId)
  const results = useRunResults(runId)
  const exposed = useExposedOutputs()
  if (detail.isLoading || !index) return <Loading lines={5} />
  if (detail.error) return <ErrorState error={detail.error} />
  const run = detail.data!.run
  const steps = detail.data!.steps
  const produced = new Set((results.data ?? []).map((r) => r.dataset_id))
  const shared = (exposed.data ?? []).filter((x) => produced.has(x.dataset_id))
  return (
    <div className="stack">
      <Card title="Steps" subtitle={`${steps.length} model${steps.length === 1 ? '' : 's'} in dependency order · ${executorLabel(run.executor)}`}>
        <div className="table-wrap">
          <table className="table" data-testid="steps-table">
            <thead><tr><th>#</th><th>Model</th><th>Status</th><th className="num">Duration</th></tr></thead>
            <tbody>
              {steps.map((s) => (
                <tr key={s.id}>
                  <td className="num">{s.step_order + 1}</td>
                  <td>{index.modelName(s.model_version_id)}{s.error_message ? <div className="tiny" style={{ color: 'var(--danger)' }}>{s.error_message}</div> : null}</td>
                  <td><StatusBadge status={s.status} /></td>
                  <td className="num">{formatDuration(durationMs(s.started_at, s.finished_at))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {run.error_message ? <Callout tone="danger" title="Run failed.">{run.error_message}</Callout> : null}
      </Card>
      <Card title="Results recorded">
        {results.isLoading ? <Loading /> : (results.data ?? []).length === 0 ? <p className="muted small">No results were recorded.</p> : (
          <div className="stack-sm">
            {(results.data ?? []).map((r) => {
              const value = JSON.parse(r.value_json) as Record<string, unknown>
              return (
                <div key={r.id} className="small">
                  <strong>{index.datasetLabel(r.dataset_id)}</strong>
                  <div className="muted">
                    {Object.entries(value).map(([k, v]) => {
                      const f = index.field(r.dataset_id, k)
                      return `${f?.label ?? k}: ${formatValue(v as never, f?.unit)}`
                    }).join(' · ')}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Card>
      <Card title="Governance for this run">
        {run.scenario_id ? (
          <p className="small">This is a <strong>read-only scenario run</strong>. Its results were recorded with lineage but were not published to any dataset, and they are not shared with anyone.</p>
        ) : (
          <p className="small">This run published its results to the datasets. A published value leaves this scope only where an explicit, unexpired approval exists.</p>
        )}
        {!run.scenario_id ? (
          shared.length ? (
            <div className="pill-list" style={{ marginTop: 8 }}>
              {shared.map((s) => <Badge key={`${s.dataset_id}:${s.field_name}`} tone="shared">{index.field(s.dataset_id, s.field_name)?.label ?? s.field_name} shared · {s.participant_key}</Badge>)}
            </div>
          ) : <p className="small muted" style={{ marginTop: 8 }}>None of this run's results are currently approved for sharing.</p>
        ) : null}
      </Card>
      <TechnicalDetails items={{ Run: run.id, Trigger: run.trigger_type, 'Triggered by': run.triggered_by, Target: run.target_version_id, Scenario: run.scenario_id }} />
    </div>
  )
}

export function ExecutionPage() {
  const runs = useExecutions(40)
  const [params, setParams] = useSearchParams()
  const { index } = useCatalogIndex()
  const { workspace } = useSelection()
  const names = {
    scenarios: new Map((workspace?.scenarios ?? []).map((s) => [s.id, s.name])),
    baselines: new Map((workspace?.baselines ?? []).filter((b) => b.target_version_id).map((b) => [b.target_version_id as string, b.name])),
  }
  const selected = params.get('run') ?? runs.data?.[0]?.id ?? null

  return (
    <div className="stack-lg">
      <PageHeader title="Execution & governance" description="Every run the platform recorded: which models ran, in what order, on which executor, what they produced — and whether any of it is shared." />
      {runs.isLoading ? <Loading lines={6} /> : runs.error ? <ErrorState error={runs.error} /> : !runs.data?.length ? (
        <EmptyState title="No runs yet">Run a baseline or a scenario to see it here.</EmptyState>
      ) : (
        <div className="grid-2" style={{ gridTemplateColumns: 'minmax(0, 5fr) minmax(0, 7fr)' }}>
          <Card title="Runs" tight>
            <ul className="activity">
              {runs.data.map((r) => {
                const d = describeRun(r, index, names)
                return (
                  <li key={r.id} className="activity-item" style={{ cursor: 'pointer', background: r.id === selected ? 'var(--accent-weak)' : undefined, borderRadius: 8, paddingInline: 6 }}
                    onClick={() => setParams({ run: r.id })}>
                    <span className="activity-icon" aria-hidden="true">{r.scenario_id ? '◇' : r.trigger_type === 'baseline' ? '◆' : '▸'}</span>
                    <div>
                      <button type="button" className="btn-ghost" style={{ border: 0, background: 'none', padding: 0, textAlign: 'left', cursor: 'pointer' }}
                        onClick={() => setParams({ run: r.id })}>
                        <span className="activity-title">{d.kind}<span className="sep">·</span>{d.subject}</span>
                      </button>
                      <div className="activity-meta">{formatDateTime(r.finished_at ?? r.started_at)} · {statusLabel(r.status)} · {formatDuration(durationMs(r.started_at, r.finished_at))}</div>
                    </div>
                  </li>
                )
              })}
            </ul>
          </Card>
          <div>{selected ? <RunDetail runId={selected} index={index} /> : null}</div>
        </div>
      )}
    </div>
  )
}
