import { Fragment, useMemo, useState } from 'react'
import {
  useExplanation,
  useFederationMap,
  useRemoveOverride,
  useRunScenario,
  useSetOverrides,
} from '../../api/queries'
import type { CatalogDataset, FieldMeta, Metric } from '../../api/types'
import { useScope } from '../../app/scope'
import { useSelection } from '../../app/selection'
import { IndexedBars } from '../../components/charts/IndexedBars'
import { deltaClass, directionPill } from '../../components/data/delta'
import { Button, LoadingButton } from '../../components/ui/Button'
import { Icon } from '../../components/ui/Icon'
import { Pill } from '../../components/ui/Pill'
import { Switch } from '../../components/ui/Switch'
import { useToast } from '../../components/ui/Toast'
import { formatDelta, formatPct, formatValue, prettyUnit } from '../../shared/format'
import { sourcesFor } from '../../shared/ImpactGraph'
import { Callout, Card, EmptyState, ErrorState, Loading, PageHeader } from '../../shared/ui'
import { NewScenarioDrawer } from './NewScenarioDrawer'
import { ScenarioList } from './ScenarioList'
import { barScale, groupMetrics, indexBars } from './scenarioModel'

function validate(field: FieldMeta, raw: string): { value?: number; error?: string } {
  if (raw.trim() === '') return { error: 'Enter a value.' }
  const n = Number(raw)
  if (!Number.isFinite(n)) return { error: 'Enter a number.' }
  if (field.min !== null && n < field.min) return { error: `Must be at least ${formatValue(field.min, field.unit)}.` }
  if (field.max !== null && n > field.max) return { error: `Must be at most ${formatValue(field.max, field.unit)}.` }
  return { value: n }
}

function AssumptionEditor({ datasets }: { datasets: CatalogDataset[] }) {
  const { scenario } = useSelection()
  const { can } = useScope()
  const save = useSetOverrides()
  const remove = useRemoveOverride()
  const run = useRunScenario()
  const toast = useToast()
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
    save.mutate({ scenarioId: scenario.id, overrides: changes }, { onSuccess: () => { setDraft({}); toast.success('Assumptions Saved', scenario.name) } })

  const onRun = () =>
    run.mutate(scenario.id, { onSuccess: () => toast.success('Scenario Complete', `${scenario.name} · results updated`) })

  return (
    <Card
      flush
      title={<><Icon name="sliders" size={15} /> Assumptions In “{scenario.name}”</>}
      subtitle="Set a scenario value to override the baseline. Values are checked against the data contract before they are saved."
      actions={
        <>
          <LoadingButton loading={save.isPending} loadingLabel="Saving..." disabled={!editable || !dirty || invalid} onClick={onSave}>
            Save Assumptions
          </LoadingButton>
          <LoadingButton variant="primary" icon="play" loading={run.isPending} loadingLabel="Running Scenario..." disabled={!editable || dirty}
            onClick={onRun} title={dirty ? 'Save your changes first' : undefined}>
            Run Scenario
          </LoadingButton>
        </>
      }
    >
      {!editable || save.error || run.error ? (
        <div className="stack-sm" style={{ padding: 16, paddingBottom: 0 }}>
          {!editable ? <Callout tone="warning">Your role in this scope can view scenarios but not change or run them.</Callout> : null}
          {save.error ? <Callout tone="danger">{(save.error as Error).message}</Callout> : null}
          {run.error ? <Callout tone="danger">{(run.error as Error).message}</Callout> : null}
        </div>
      ) : null}
      <div className="impact-scroll" style={{ maxHeight: 520 }}>
        <table className="grid">
          <thead>
            <tr><th>Assumption</th><th className="right">Current Data</th><th style={{ width: 260 }}>Scenario Value</th><th /></tr>
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
                    <div className="cellmain">{f.label}</div>
                    <div className="sub">{d.label}{f.min !== null || f.max !== null ? ` · allowed ${f.min !== null ? `≥ ${formatValue(f.min, f.unit)}` : ''}${f.max !== null ? ` ≤ ${formatValue(f.max, f.unit)}` : ''}` : ''}</div>
                  </td>
                  <td className="right num">{formatValue(current ?? null, f.unit)}</td>
                  <td>
                    <div className="input-group">
                      <input className="input" inputMode="decimal" aria-label={`Scenario value for ${f.label}`} disabled={!editable}
                        value={shown} placeholder="Same as baseline" data-testid={`override-${f.name}`}
                        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
                      <span className="input-addon">{prettyUnit(f.unit) || '—'}</span>
                    </div>
                    {errors[key] ? <div className="tiny" style={{ color: 'var(--error-strong)', marginTop: 3 }}>{errors[key]}</div> : null}
                  </td>
                  <td>
                    {ov ? (
                      <div className="row" style={{ gap: 6 }}>
                        <Pill tone="info">Override</Pill>
                        {editable ? (
                          <LoadingButton variant="ghost" size="xs" loading={remove.isPending && remove.variables?.overrideId === ov.id} loadingLabel="Removing..."
                            onClick={() => remove.mutate({ scenarioId: scenario.id, overrideId: ov.id }, { onSuccess: () => toast.success('Override Removed', f.label) })}>
                            Remove
                          </LoadingButton>
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
      {dirty ? <p className="small muted" style={{ padding: '10px 16px' }}>{changes.length} unsaved change{changes.length === 1 ? '' : 's'}. Save, then run the scenario to see the effect.</p> : null}
    </Card>
  )
}

function ComparisonBars({ metrics }: { metrics: Metric[] }) {
  const { bars, skipped } = useMemo(() => indexBars(metrics), [metrics])
  if (!bars.length && !skipped) return null
  return (
    <Card
      title={<><Icon name="bars" size={15} /> Baseline Vs Scenario</>}
      subtitle="Indexed · Baseline = 100 · Results That Moved"
      actions={
        <div className="ripple-legend" style={{ margin: 0 }}>
          <span className="ll"><i style={{ background: 'var(--border-strong)', height: 9, width: 9, borderRadius: 2 }} /> Baseline</span>
          <span className="ll"><i style={{ background: 'var(--accent)', height: 9, width: 9, borderRadius: 2 }} /> Scenario</span>
        </div>
      }
    >
      {bars.length ? <IndexedBars bars={bars} scale={barScale(bars)} /> : null}
      {skipped ? <p className="small muted" style={{ marginTop: 10 }}>{skipped} result{skipped === 1 ? ' is' : 's are'} not drawn because {skipped === 1 ? 'its' : 'their'} baseline is zero or not a number — {skipped === 1 ? 'it is' : 'they are'} in the table below.</p> : null}
    </Card>
  )
}

function ComparisonTable({ metrics }: { metrics: Metric[] }) {
  const [showAll, setShowAll] = useState(false)
  const groups = useMemo(() => groupMetrics(metrics, showAll), [metrics, showAll])
  return (
    <Card
      flush
      title={<><Icon name="grid" size={15} /> Metric Detail</>}
      subtitle="Values come from the recorded results of the two exact runs. The platform describes change; it does not judge it."
      actions={<Switch checked={showAll} onChange={setShowAll} label="Show Unchanged" />}
    >
      {groups.length === 0 ? <p className="muted" style={{ padding: 16 }}>No metric changed. Turn on Show Unchanged to see every value.</p> : (
        <div className="impact-scroll" style={{ maxHeight: 560 }}>
          <table className="grid" data-testid="comparison-table">
            <thead><tr><th>Metric</th><th className="right">Baseline</th><th className="right">Scenario</th><th className="right">Delta</th><th>Impact</th></tr></thead>
            <tbody>
              {groups.map(([label, ms]) => (
                <Fragment key={label}>
                  <tr className="group-row"><td colSpan={5}>{label}</td></tr>
                  {ms.map((m) => {
                    const numeric = m.kind === 'numeric'
                    const pill = directionPill(m.direction, m.changed, numeric)
                    return (
                      <tr key={`${m.dataset_id}:${m.field}`} className={m.changed ? 'changed' : 'dim'}>
                        <td><div className="cellmain">{m.field_label}{m.terminal ? <> <span className="tag">Result</span></> : null}</div>{m.unit ? <div className="sub">{prettyUnit(m.unit)}</div> : null}</td>
                        <td className="right num">{formatValue(m.baseline, m.unit)}</td>
                        <td className="right num strong">{formatValue(m.scenario, m.unit)}</td>
                        <td className="right">
                          {numeric && m.changed ? (
                            <>
                              <span className={`delta ${deltaClass(m.direction)}`}>{m.baseline_zero || m.relative_delta === null ? 'n/a' : formatPct(m.relative_delta)}</span>
                              <div className="sub num">{formatDelta(m.absolute_delta, m.unit)}</div>
                            </>
                          ) : <span className="delta flat">{m.changed ? 'Changed' : '—'}</span>}
                        </td>
                        <td><Pill tone={pill.tone} dot>{pill.label}</Pill></td>
                      </tr>
                    )
                  })}
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
  const { baseline, scenario, scenariosForBaseline, workspace, loading, error, refetch, selectScenario } = useSelection()
  const map = useFederationMap()
  const ex = useExplanation(scenario?.run ? scenario.id : null)
  const [creating, setCreating] = useState(false)

  if (loading) return <Loading lines={6} />
  if (error) return <ErrorState error={error} onRetry={refetch} />
  if (!workspace || !baseline) {
    return (<div className="stack"><PageHeader title="Scenario Comparison" /><EmptyState title="No Baseline In This Scope Yet" /></div>)
  }
  const sourceIds = map.data ? sourcesFor(map.data, baseline.target_version_id) : new Set<string>()
  const datasets = workspace.assumptions.filter((d) => sourceIds.size === 0 || sourceIds.has(d.id))

  return (
    <div className="stack">
      <PageHeader
        title="Scenario Comparison"
        description={<>Compare <strong>{scenario?.name ?? 'a scenario'}</strong> with the baseline <strong>{baseline.name}</strong>. Scenario runs are read-only: they never change shared data or trigger downstream updates.</>}
        actions={<Button variant="primary" size="sm" icon="plus" onClick={() => setCreating(true)} data-testid="new-scenario">New Scenario</Button>}
      />
      <NewScenarioDrawer open={creating} onClose={() => setCreating(false)} baselineId={baseline.id} baselineName={baseline.name} onCreated={selectScenario} />

      <ScenarioList scenarios={scenariosForBaseline} selectedId={scenario?.id} onSelect={selectScenario} onNew={() => setCreating(true)} />

      {scenario ? (
        <>
          <AssumptionEditor datasets={datasets} />
          {ex.isLoading ? <Loading /> : ex.error && scenario.run ? <ErrorState error={ex.error} /> : ex.data ? (
            <>
              <ComparisonBars metrics={ex.data.comparison.metrics} />
              <ComparisonTable metrics={ex.data.comparison.metrics} />
            </>
          ) : (
            <EmptyState title="Not Run Yet">Save the assumptions you want to test, then run the scenario.</EmptyState>
          )}
        </>
      ) : null}
    </div>
  )
}
