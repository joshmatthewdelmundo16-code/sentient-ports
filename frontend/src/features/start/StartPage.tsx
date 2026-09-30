import { Link } from 'react-router'
import { useActivity, useBuildInfo, useGovernanceSummary } from '../../api/queries'
import { kindLabel, useScope } from '../../app/scope'
import { useSelection } from '../../app/selection'
import { formatDateTime, formatTransition, statusLabel } from '../../shared/format'
import { Badge, Callout, Card, Loading, PageHeader, StatusBadge } from '../../shared/ui'

interface Step {
  label: string
  to: string
  done: boolean
  detail: string
}

export function StartPage() {
  const { scope, session } = useScope()
  const { workspace, baseline, scenario, loading } = useSelection()
  const activity = useActivity(40)
  const gov = useGovernanceSummary()
  const build = useBuildInfo()

  const items = activity.data ?? []
  const upload = items.find((i) => i.kind === 'dataset_change' && i.via?.kind === 'upload')
  const propagated = items.find((i) => i.kind === 'dataset_change' && i.links.run_id)
  const override = scenario?.overrides[0]

  const steps: Step[] = [
    { label: 'Choose scope', to: '/', done: Boolean(scope) || session?.auth_mode === 'local',
      detail: scope ? `${scope.name} · ${kindLabel(scope.kind)}` : 'Local workspace' },
    { label: 'Baseline', to: '/decision', done: Boolean(baseline?.run),
      detail: baseline ? `${baseline.name} · ${baseline.run ? statusLabel(baseline.run.status) : 'not run yet'}` : 'None yet' },
    { label: 'Scenario', to: '/scenarios', done: Boolean(scenario?.run),
      detail: scenario ? `${scenario.name} · ${scenario.run ? statusLabel(scenario.run.status) : 'not run yet'}` : 'None yet' },
    { label: 'Assumption', to: '/scenarios', done: Boolean(override),
      detail: override ? `${override.field_label}: ${formatTransition(override.baseline_value, override.value, override.unit)}` : 'No change yet' },
    { label: 'Excel / source', to: '/excel', done: Boolean(upload),
      detail: upload ? `${upload.via?.file_name ?? 'Workbook'} · ${formatDateTime(upload.occurred_at)}` : 'No workbook uploaded yet' },
    { label: 'Propagate', to: '/activity', done: Boolean(propagated),
      detail: propagated ? `${propagated.subject} · ${statusLabel(propagated.status)}` : 'Nothing propagated yet' },
    { label: 'Compare', to: '/scenarios', done: Boolean(baseline?.run && scenario?.run), detail: 'Every metric, side by side' },
    { label: 'Why', to: '/impact', done: Boolean(scenario?.run), detail: 'Field-by-field causal path' },
    { label: 'Provenance', to: '/sources', done: Boolean(workspace?.assumptions.length), detail: 'Where each value came from' },
    { label: 'Execution', to: '/execution', done: Boolean(workspace?.counts.runs), detail: `${workspace?.counts.runs ?? 0} runs recorded` },
    { label: 'Governance', to: '/governance', done: Boolean(gov.data?.effective_approvals),
      detail: gov.data ? `${gov.data.effective_approvals} outputs approved for sharing` : '—' },
    { label: 'Network', to: '/network', done: false, detail: 'Hub views of approved outputs' },
  ]

  return (
    <div className="stack-lg">
      <PageHeader
        title="Start here"
        description="Link port models so a change in one assumption flows through every model that depends on it — and see exactly why. Private work stays in your organization's zone; only outputs someone explicitly approves are shared."
      />

      <Card title="Where you are" subtitle="Everything below is read from the platform's records, not from this page.">
        {loading ? <Loading /> : (
          <div className="grid-4">
            <div>
              <div className="section-title">Scope</div>
              <div style={{ fontWeight: 600 }}>{scope?.name ?? 'Local workspace'}</div>
              <div className="small muted">{scope ? kindLabel(scope.kind) : 'Single-developer mode'}</div>
            </div>
            <div>
              <div className="section-title">Baseline</div>
              <div style={{ fontWeight: 600 }}>{baseline?.name ?? 'None yet'}</div>
              <div className="small muted">{baseline?.run ? `Run ${formatDateTime(baseline.run.finished_at)}` : 'Not run yet'}</div>
            </div>
            <div>
              <div className="section-title">Scenario</div>
              <div style={{ fontWeight: 600 }}>{scenario?.name ?? 'None yet'}</div>
              <div className="small muted">
                {scenario ? `${scenario.overrides.length} assumption${scenario.overrides.length === 1 ? '' : 's'} changed` : 'Create one to test a change'}
              </div>
            </div>
            <div>
              <div className="section-title">Data store</div>
              <div style={{ fontWeight: 600 }}>{build.data ? (build.data.database === 'sqlite' ? 'Local SQLite' : 'Shared PostgreSQL') : '—'}</div>
              <div className="small muted">{build.data?.environment ?? ''}</div>
            </div>
          </div>
        )}
      </Card>

      <Card title="The decision workflow" subtitle="Green steps already have real data behind them. Select any step to open it.">
        <ol className="stepper" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
          {steps.map((s, i) => (
            <li key={s.label} className={`step ${s.done ? 'done' : ''}`}>
              <span className="step-marker" aria-hidden="true">{s.done ? '✓' : i + 1}</span>
              <div className="step-body">
                <Link to={s.to} className="step-title">{s.label}</Link>
                <div className="small muted">{s.detail}</div>
              </div>
            </li>
          ))}
        </ol>
      </Card>

      <div className="grid-3">
        <Card title="Baseline and scenario">
          <p className="small">A <strong>baseline</strong> is the authoritative run of your models on current data. A <strong>scenario</strong> changes one or more assumptions and runs the same models <em>without</em> touching shared data, so you can compare the two safely.</p>
        </Card>
        <Card title="Excel is upload-based">
          <p className="small">Workbooks are read only when someone uploads one. Nothing is synchronised automatically. Formulas are never evaluated, macros never run and external links are never followed — only cached cell values are read, validated against the data contract, and committed.</p>
        </Card>
        <Card title="Private by default">
          <p className="small">Your models, data and scenarios stay in your organization's private zone. A hub sees only fields that someone with approval rights has explicitly shared with it — and a scenario result is never shared automatically.</p>
        </Card>
      </div>

      {build.data?.demo_seed_enabled ? (
        <Callout tone="synthetic" title="Demonstration data.">
          The ports, organizations, models and values in this workspace are synthetic and illustrative. They are not real port data and the models are not calibrated. <Link to="/capabilities">See what is implemented</Link>.
        </Callout>
      ) : null}
      {activity.data && activity.data[0] ? (
        <Card title="Latest activity" actions={<Link to="/activity" className="btn btn-sm">All activity</Link>}>
          <div className="row">
            <strong>{activity.data[0].title}</strong><span className="muted">·</span><span>{activity.data[0].subject}</span>
            <StatusBadge status={activity.data[0].status} />
            <span className="small muted">{formatDateTime(activity.data[0].occurred_at)}</span>
          </div>
        </Card>
      ) : null}
      <div className="small muted">
        <Badge>Tip</Badge> The scope, baseline and scenario you are working with are always shown at the top of the page.
      </div>
    </div>
  )
}
