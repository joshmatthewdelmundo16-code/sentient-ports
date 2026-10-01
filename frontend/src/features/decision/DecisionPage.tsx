import { useState } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../../api/client'
import { useExplanation, useExposedOutputs, useFederationMap, useRunScenario } from '../../api/queries'
import type { Explanation } from '../../api/types'
import { Button, buttonClass } from '../../components/ui/Button'
import { Icon } from '../../components/ui/Icon'
import { KpiRow } from '../../components/ui/Kpi'
import { Pill } from '../../components/ui/Pill'
import { useToast } from '../../components/ui/Toast'
import { useScope } from '../../app/scope'
import { useSelection } from '../../app/selection'
import { executorLabel, formatDateTime, formatPct, formatTransition, formatValue, sourceLabel } from '../../shared/format'
import { sourcesFor } from '../../shared/ImpactGraph'
import { RippleChain } from '../../components/ripple/RippleChain'
import { buildNodeProvenance, buildRipple, type RippleNode } from '../../components/ripple/rippleModel'
import { RunSteps } from '../../components/ripple/RunSteps'
import { Callout, Card, EmptyState, ErrorState, Loading, PageHeader, StatusBadge, TechnicalDetails } from '../../shared/ui'
import { headlineMetrics, joinNames, KpiCard } from '../common'
import { ActionBar } from './ActionBar'
import { ConfigPanel } from './ConfigPanel'
import { ImpactPanel } from './ImpactPanel'
import { ProvenanceDrawer, type ProvenanceView } from './ProvenanceDrawer'
import { buildProvenance } from './provenance'

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
    <Callout tone="neutral" title="Impact Summary">
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
  const { scope, can } = useScope()
  const ex = useExplanation(scenario?.run ? scenario.id : null)
  const run = useRunScenario()
  const exposed = useExposedOutputs()
  const map = useFederationMap()
  const toast = useToast()
  const [prov, setProv] = useState<ProvenanceView | null>(null)
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [pulse, setPulse] = useState(0)

  if (loading) return <Loading lines={6} />
  if (error) return <ErrorState error={error} onRetry={refetch} />
  if (!workspace || !baseline) {
    return (
      <div className="stack">
        <PageHeader title="Decision Overview" />
        <EmptyState title="No Baseline In This Scope Yet">
          A baseline is an authoritative run of this organization's models. Once one exists, this page compares scenarios against it.
        </EmptyState>
      </div>
    )
  }

  const data = ex.data
  const notRun = !scenario?.run || (ex.error instanceof ApiError && ex.error.status === 409)
  const upstream = map.data ? sourcesFor(map.data, baseline.target_version_id) : null
  const sources = workspace.assumptions.filter((d) => !upstream || upstream.has(d.id))
  const metrics = data ? headlineMetrics(data.comparison.metrics) : []
  const modelsInScope = data ? data.path.length + data.unaffected.length : 0

  const onRun = () => {
    if (!scenario) return
    run.mutate(scenario.id, {
      onSuccess: () => { toast.success('Scenario Complete', scenario.name); setPulse((p) => p + 1) },
      onError: (e) => toast.error('Scenario Run Failed', e.message),
    })
  }

  const mapModels = map.data?.models ?? []
  const stages = data ? buildRipple(data, mapModels) : []
  const closeProv = () => { setProv(null); setSelectedNode(null) }
  const openOverall = () => {
    if (!scenario || !data) return
    setSelectedNode(null)
    setProv({ title: 'Provenance', subtitle: `${scenario.name} · Compared With ${baseline.name}`, steps: buildProvenance({ scenario, baseline, explanation: data, sources }) })
  }
  const openNode = (node: RippleNode) => {
    if (!scenario || !data) return
    setSelectedNode(node.id)
    setProv(buildNodeProvenance(node, { explanation: data, scenario, baseline, sources: workspace.assumptions, models: mapModels }))
  }

  return (
    <div className="stack">
      <PageHeader
        title="Decision Overview"
        description={scenario ? (
          <>Evaluating <span className="hl-scenario">{scenario.name}</span> Against <strong>{baseline.name}</strong>{data ? <> · Across {modelsInScope} Federated Model{modelsInScope === 1 ? '' : 's'}</> : null}</>
        ) : 'Choose Or Create A Scenario To See Its Impact On The Baseline'}
        actions={
          <>
            <Link className={buttonClass('outline', 'sm')} to="/scenarios"><Icon name="sliders" size={14} /> Change Assumptions</Link>
            {data && scenario ? <Button variant="outline" size="sm" icon="branch" onClick={openOverall}>View Provenance</Button> : null}
            {data ? <Pill tone="accent"><Icon name="cpu" size={14} /> {modelsInScope} Model{modelsInScope === 1 ? '' : 's'} In Scope</Pill> : null}
          </>
        }
      />

      {!scenario ? (
        <EmptyState title="No Scenario For This Baseline" action={<Link className={buttonClass('primary')} to="/scenarios">Create A Scenario</Link>}>
          A scenario changes one or more assumptions and runs the same models without changing shared data.
        </EmptyState>
      ) : (
        <>
          {data ? (
            <KpiRow>
              {metrics.map((m) => <KpiCard key={`${m.dataset_id}:${m.field}`} metric={m} />)}
              {/* Filler cells keep the 1px rules continuous on the last row. */}
              {Array.from({ length: (4 - (metrics.length % 4)) % 4 }, (_, i) => <div key={`pad-${i}`} className="kpi pad" aria-hidden="true" />)}
            </KpiRow>
          ) : null}

          {data ? <NarrativeSummary ex={data} /> : null}

          <div className="grid-split">
            <ConfigPanel scopeName={scope?.name ?? 'Local Workspace'} baseline={baseline} scenario={scenario} />
            {notRun ? (
              <Card title={<><Icon name="gauge" size={15} /> Live Decision Impact</>}>
                <EmptyState title="Run The Scenario To See Its Impact">
                  Use Run Scenario below. Scenario runs never change shared data and never trigger downstream updates.
                </EmptyState>
              </Card>
            ) : ex.isLoading ? (
              <Card title={<><Icon name="gauge" size={15} /> Live Decision Impact</>}><Loading lines={5} /></Card>
            ) : ex.error ? (
              <Card title={<><Icon name="gauge" size={15} /> Live Decision Impact</>}><ErrorState error={ex.error} onRetry={() => void ex.refetch()} /></Card>
            ) : data ? (
              <ImpactPanel metrics={data.comparison.metrics} run={scenario.run} />
            ) : null}
          </div>

          {data ? (
            <>
              <Card
                title={<><Icon name="network" size={15} /> Change Propagation</>}
                subtitle="What Changed → Which Models → Downstream → Decision Impact"
                actions={<Button variant="ghost" size="xs" icon="refresh" onClick={() => setPulse((p) => p + 1)}>Replay</Button>}
              >
                {stages.length ? (
                  <RippleChain stages={stages} selectedId={selectedNode} onSelect={openNode} pulse={pulse} />
                ) : (
                  <p className="muted" data-testid="ripple-empty">No assumption was changed in this scenario, so there is nothing to propagate.</p>
                )}
                <RunSteps runId={scenario.run?.id} running={run.isPending} models={mapModels} />
              </Card>

              <div className="grid-gov">
                <Card title="Why This Changed" actions={<Link className={buttonClass('outline', 'xs')} to="/impact">See The Path</Link>}>
                  <WhySummary ex={data} />
                </Card>
                <Card title="Where The Data Came From" actions={<Link className={buttonClass('outline', 'xs')} to="/sources">Sources &amp; Provenance</Link>}>
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
              <div className="grid-gov">
                <Card title="What Happened During Execution" actions={<Link className={buttonClass('outline', 'xs')} to="/execution">Execution &amp; Governance</Link>}>
                  <div className="stack-sm small">
                    <div className="row"><StatusBadge status={baseline.run?.status} /> Baseline run · {executorLabel(baseline.run?.executor)} · {formatDateTime(baseline.run?.finished_at)}</div>
                    <div className="row"><StatusBadge status={scenario.run?.status} /> Scenario run · read-only · {formatDateTime(scenario.run?.finished_at)}</div>
                    <div className="muted">{data.path.length} model{data.path.length === 1 ? '' : 's'} on the change path, {data.unaffected.length} unaffected.</div>
                  </div>
                  <TechnicalDetails items={{ 'Baseline run': baseline.run?.id, 'Scenario run': scenario.run?.id, Scenario: scenario.id, Baseline: baseline.id }} />
                </Card>
                <Card title="What Is Shared" actions={<Link className={buttonClass('outline', 'xs')} to="/governance">Governance</Link>}>
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

          <ActionBar run={scenario.run} running={run.isPending} canRun={can('analyst')} onRun={onRun} />

          <ProvenanceDrawer view={prov} onClose={closeProv} />
        </>
      )}
    </div>
  )
}
