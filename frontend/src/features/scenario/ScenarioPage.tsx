import { Fragment, useMemo, useState, type FormEvent } from 'react'
import {
  useCreateScenario,
  useExplanation,
  useFederationMap,
  useRemoveOverride,
  useRunScenario,
  useSetOverrides,
} from '../../api/queries'
import type { CatalogDataset, FieldMeta, Metric } from '../../api/types'
import { useScope } from '../../app/scope'
import { useSelection } from '../../app/selection'
import { ChangeBars } from '../../shared/charts'
import { formatDelta, formatPct, formatValue, prettyUnit } from '../../shared/format'
import { sourcesFor } from '../../shared/ImpactGraph'
import { Badge, Callout, Card, EmptyState, ErrorState, Loading, PageHeader, StatusBadge } from '../../shared/ui'

function validate(field: FieldMeta, raw: string): { value?: number; error?: string } {
  if (raw.trim() === '') return { error: 'Enter a value.' }
  const n = Number(raw)
  if (!Number.isFinite(n)) return { error: 'Enter a number.' }
  if (field.min !== null && n < field.min) return { error: `Must be at least ${formatValue(field.min, field.unit)}.` }
  if (field.max !== null && n > field.max) return { error: `Must be at most ${formatValue(field.max, field.unit)}.` }
  return { value: n }
}

function NewScenario({ baselineId, onCreated }: { baselineId: string; onCreated: (id: string) => void }) {
  const create = useCreateScenario()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    create.mutate(
      { baseline_id: baselineId, name: name.trim(), description: description.trim() || undefined },
      { onSuccess: (s) => { setName(''); setDescription(''); onCreated(s.id) } },
    )
  }
  return (
    <form onSubmit={submit} className="row" style={{ alignItems: 'flex-end' }}>
      <div className="field" style={{ minWidth: 220 }}>
        <label htmlFor="new-scenario-name">Scenario name</label>
        <input id="new-scenario-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="For example: Two new berths" />
      </div>
      <div className="field" style={{ flex: 1, minWidth: 220 }}>
        <label htmlFor="new-scenario-desc">Description (optional)</label>
        <input id="new-scenario-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <button className="btn" type="submit" disabled={!name.trim() || create.isPending}>Create scenario</button>
      {create.error ? <span className="small" style={{ color: 'var(--danger)' }}>{(create.error as Error).message}</span> : null}
    </form>
  )
}

function AssumptionEditor({ datasets }: { datasets: CatalogDataset[] }) {
  const { scenario } = useSelection()
  const { can } = useScope()
  const save = useSetOverrides()
  const remove = useRemoveOverride()
  const run = useRunScenario()
  const [draft, setDraft] = useState<Record<string, string>>({})
  const overrides = new Map((scenario?.overrides ?? []).map((o) => [`${o.dataset_id}:${o.field}`, o]))
  const editable = can('analyst')

  if (!scenario) return null
  const rows = datasets.flatMap((d) => d.fields.filter((f) => f.type === 'number' || f.type === 'integer').map((f) => ({ d, f })))
  const errors: Record<string, string> = {}
  const changes: { dataset_id: string; field_name: string; value: number }[] = []
  for (const { d, f } of rows) {
    const key = `${d.id}:${f.name}`
    if (draft[key] === undefined) continue
    const res = validate(f, draft[key] as string)
    if (res.error) errors[key] = res.error
    else changes.push({ dataset_id: d.id, field_name: f.name, value: res.value as number })
  }
  const dirty = changes.length > 0
  const invalid = Object.keys(errors).length > 0

  const onSave = () =>
    save.mutate({ scenarioId: scenario.id, overrides: changes }, { onSuccess: () => setDraft({}) })

  return (
    <Card
      title={`Assumptions in “${scenario.name}”`}
      subtitle="Set a scenario value to override the baseline. Values are checked against the data contract before they are saved."
      actions={
        <>
          <button className="btn" disabled={!editable || !dirty || invalid || save.isPending} onClick={onSave}>
            {save.isPending ? 'Saving…' : 'Save assumptions'}
          </button>
          <button className="btn btn-primary" disabled={!editable || dirty || run.isPending} onClick={() => run.mutate(scenario.id)}
            title={dirty ? 'Save your changes first' : undefined}>
            {run.isPending ? 'Running…' : 'Run scenario'}
          </button>
        </>
      }
    >
      {!editable ? <Callout tone="warning">Your role in this scope can view scenarios but not change or run them.</Callout> : null}
      {save.error ? <Callout tone="danger">{(save.error as Error).message}</Callout> : null}
      {run.error ? <Callout tone="danger">{(run.error as Error).message}</Callout> : null}
      {run.data ? <Callout tone="success">Scenario run finished. Results below are from the new run.</Callout> : null}
      <div className="table-wrap" style={{ marginTop: 8 }}>
        <table className="table">
          <thead>
            <tr><th>Assumption</th><th className="num">Current data</th><th style={{ width: 260 }}>Scenario value</th><th /></tr>
          </thead>
          <tbody>
            {rows.map(({ d, f }) => {
              const key = `${d.id}:${f.name}`
              const ov = overrides.get(key)
              const current = d.value?.[f.name]
              const shown = draft[key] ?? (ov ? String(ov.value) : '')
              return (
                <tr key={key} className={ov ? 'changed' : ''}>
                  <td>
                    <strong>{f.label}</strong>
                    <div className="tiny muted">{d.label}{f.min !== null || f.max !== null ? ` · allowed ${f.min !== null ? `≥ ${formatValue(f.min, f.unit)}` : ''}${f.max !== null ? ` ≤ ${formatValue(f.max, f.unit)}` : ''}` : ''}</div>
                  </td>
                  <td className="num">{formatValue(current ?? null, f.unit)}</td>
                  <td>
                    <div className="input-group">
                      <input className="input" inputMode="decimal" aria-label={`Scenario value for ${f.label}`} disabled={!editable}
                        value={shown} placeholder="Same as baseline" data-testid={`override-${f.name}`}
                        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
                      <span className="input-addon">{prettyUnit(f.unit) || '—'}</span>
                    </div>
                    {errors[key] ? <div className="tiny" style={{ color: 'var(--danger)' }}>{errors[key]}</div> : null}
                  </td>
                  <td>
                    {ov ? (
                      <div className="row" style={{ gap: 6 }}>
                        <Badge tone="info">Override</Badge>
                        {editable ? (
                          <button className="btn btn-sm btn-ghost" onClick={() => remove.mutate({ scenarioId: scenario.id, overrideId: ov.id })}>Remove</button>
                        ) : null}
                      </div>
                    ) : null}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {dirty ? <p className="small muted" style={{ marginTop: 8 }}>{changes.length} unsaved change{changes.length === 1 ? '' : 's'}. Save, then run the scenario to see the effect.</p> : null}
    </Card>
  )
}

function ComparisonTable({ metrics }: { metrics: Metric[] }) {
  const [showAll, setShowAll] = useState(false)
  const shown = showAll ? metrics : metrics.filter((m) => m.changed)
  const groups = useMemo(() => {
    const g = new Map<string, Metric[]>()
    for (const m of shown) g.set(m.dataset_label, [...(g.get(m.dataset_label) ?? []), m])
    return [...g.entries()]
  }, [shown])
  return (
    <Card
      title="Every metric"
      subtitle="Values come from the recorded results of the two exact runs. The platform describes change; it does not judge it."
      actions={
        <label className="row small" style={{ gap: 6 }}>
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show unchanged
        </label>
      }
    >
      {shown.length === 0 ? <p className="muted">No metric changed. Tick “Show unchanged” to see every value.</p> : (
        <div className="table-wrap">
          <table className="table" data-testid="comparison-table">
            <thead><tr><th>Metric</th><th className="num">Baseline</th><th className="num">Scenario</th><th className="num">Change</th><th className="num">%</th></tr></thead>
            <tbody>
              {groups.map(([label, ms]) => (
                <Fragment key={label}>
                  <tr><td colSpan={5} className="section-title" style={{ background: 'var(--surface-2)' }}>{label}</td></tr>
                  {ms.map((m) => (
                    <tr key={`${m.dataset_id}:${m.field}`} className={m.changed ? 'changed' : 'dim'}>
                      <td>{m.field_label}</td>
                      <td className="num">{formatValue(m.baseline, m.unit)}</td>
                      <td className="num">{formatValue(m.scenario, m.unit)}</td>
                      <td className="num">{m.kind === 'numeric' ? formatDelta(m.absolute_delta, m.unit) : m.changed ? 'Changed' : 'No change'}</td>
                      <td className="num">{m.kind === 'numeric' ? (m.baseline_zero ? 'n/a' : formatPct(m.relative_delta)) : ''}</td>
                    </tr>
                  ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

export function ScenarioPage() {
  const { baseline, scenario, workspace, loading, error, refetch, selectScenario } = useSelection()
  const map = useFederationMap()
  const ex = useExplanation(scenario?.run ? scenario.id : null)

  if (loading) return <Loading lines={6} />
  if (error) return <ErrorState error={error} onRetry={refetch} />
  if (!workspace || !baseline) {
    return (<div className="stack-lg"><PageHeader title="Scenario comparison" /><EmptyState title="No baseline in this scope yet" /></div>)
  }
  const sourceIds = map.data ? sourcesFor(map.data, baseline.target_version_id) : new Set<string>()
  const datasets = workspace.assumptions.filter((d) => sourceIds.size === 0 || sourceIds.has(d.id))
  const changed = ex.data ? ex.data.comparison.metrics.filter((m) => m.changed && m.kind === 'numeric' && m.terminal) : []

  return (
    <div className="stack-lg">
      <PageHeader
        title="Scenario comparison"
        description={<>Compare <strong>{scenario?.name ?? 'a scenario'}</strong> with the baseline <strong>{baseline.name}</strong>. Scenario runs are read-only: they never change shared data or trigger downstream updates.</>}
      />
      <Card title="New scenario" subtitle={`Derived from the baseline “${baseline.name}”.`}>
        <NewScenario baselineId={baseline.id} onCreated={selectScenario} />
      </Card>
      {scenario ? (
        <>
          <div className="row small muted">
            <span>Scenario status:</span> <StatusBadge status={scenario.run?.status ?? scenario.status} />
          </div>
          <AssumptionEditor datasets={datasets} />
          {ex.isLoading ? <Loading /> : ex.error && scenario.run ? <ErrorState error={ex.error} /> : ex.data ? (
            <>
              {changed.length ? (
                <Card title="Change magnitude" subtitle="Relative change of each result that moved, scenario against baseline.">
                  <ChangeBars bars={changed.map((m) => ({
                    key: `${m.dataset_id}:${m.field}`, label: m.field_label, sub: m.dataset_label, relative: m.relative_delta,
                    detail: `${formatValue(m.baseline, m.unit)} → ${formatValue(m.scenario, m.unit)}`,
                  }))} />
                </Card>
              ) : null}
              <ComparisonTable metrics={ex.data.comparison.metrics} />
            </>
          ) : (
            <EmptyState title="Not run yet">Save the assumptions you want to test, then run the scenario.</EmptyState>
          )}
        </>
      ) : (
        <EmptyState title="No scenario yet">Create a scenario above to test a change against this baseline.</EmptyState>
      )}
    </div>
  )
}
