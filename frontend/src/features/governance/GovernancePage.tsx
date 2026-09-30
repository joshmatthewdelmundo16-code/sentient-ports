import { useApprovals, useExposedOutputs, useGovernanceSummary, useParticipants } from '../../api/queries'
import type { Approval, Participant } from '../../api/types'
import { formatDateTime, formatValue, parseServerTime } from '../../shared/format'
import { Badge, Callout, Card, EmptyState, ErrorState, Loading, PageHeader, StatusBadge, SyntheticBadge, TechnicalDetails } from '../../shared/ui'
import { useCatalogIndex } from '../common'
import { AuditLog, ApprovalActions, NewApproval, useOrgNames } from './GovernanceActions'

function effective(a: Approval, participants: Map<string, Participant>): string {
  if (a.status === 'revoked') return 'revoked'
  const exp = parseServerTime(a.expires_at)
  if (exp && exp.getTime() <= Date.now()) return 'expired'
  const p = participants.get(a.participant_id)
  if (p && p.status !== 'active') return 'inactive'
  return 'active'
}

const EFFECT_LABEL: Record<string, string> = {
  active: 'Shared', revoked: 'Revoked', expired: 'Expired', inactive: 'Participant inactive',
}

export function GovernancePage() {
  const summary = useGovernanceSummary()
  const participants = useParticipants()
  const approvals = useApprovals()
  const exposed = useExposedOutputs()
  const { index } = useCatalogIndex()
  const orgName = useOrgNames()
  const pmap = new Map((participants.data ?? []).map((p) => [p.id, p]))
  const s = summary.data

  return (
    <div className="stack-lg">
      <PageHeader title="Governance" description="Who has approved which outputs to leave this scope, for what purpose, until when — and everything that was granted, revoked or denied." />

      <div className="grid-4">
        <div className="card card-tight kpi"><div className="kpi-label">Participants</div><div className="kpi-value">{s ? s.active_participants : '—'}</div><div className="kpi-from">{s ? `${s.participants} registered` : ''}</div></div>
        <div className="card card-tight kpi"><div className="kpi-label">Outputs shared now</div><div className="kpi-value">{exposed.data ? exposed.data.length : '—'}</div><div className="kpi-from">Approved, active and unexpired</div></div>
        <div className="card card-tight kpi"><div className="kpi-label">Approvals</div><div className="kpi-value">{s ? s.effective_approvals : '—'}</div><div className="kpi-from">{s ? `${s.approvals} recorded in total` : ''}</div></div>
        <div className="card card-tight kpi"><div className="kpi-label">Revoked</div><div className="kpi-value">{s ? s.revoked_approvals : '—'}</div><div className="kpi-from">No longer shared</div></div>
      </div>

      <Callout tone="neutral" title="The exposure boundary.">
        A field leaves this scope only if an approval exists for exactly that field, it has not been revoked or expired, and the approving participant is active. Anything else — including every scenario result — stays private. Hubs see only what is on this list.
      </Callout>

      <NewApproval />

      <Card title="Shared outputs" subtitle="What the boundary lets out right now, with the current value of each field.">
        {exposed.isLoading || !index ? <Loading /> : exposed.error ? <ErrorState error={exposed.error} /> : exposed.data && exposed.data.length ? (
          <div className="table-wrap">
            <table className="table" data-testid="exposed-table">
              <thead><tr><th>Output</th><th>Shared by</th><th>Shared with</th><th>Purpose</th><th className="num">Value</th><th>Expires</th></tr></thead>
              <tbody>
                {exposed.data.map((x) => {
                  const f = index.field(x.dataset_id, x.field_name)
                  const ext = x as typeof x & { audience_organization_id?: string | null; source_run_id?: string | null }
                  return (
                    <tr key={`${x.participant_id}:${x.dataset_id}:${x.field_name}`}>
                      <td><strong>{f?.label ?? x.field_name}</strong><div className="tiny muted">{index.datasetLabel(x.dataset_id)}{ext.source_run_id ? ' · from a specific run' : ''}</div></td>
                      <td>{pmap.get(x.participant_id)?.name ?? x.participant_key}</td>
                      <td>{ext.audience_organization_id ? <Badge tone="shared">{orgName(ext.audience_organization_id)}</Badge> : <Badge tone="network">Whole network</Badge>}</td>
                      <td className="small">{x.purpose ?? '—'}</td>
                      <td className="num">{x.value_present ? formatValue(x.value, f?.unit) : 'Not available'}</td>
                      <td className="small">{x.expires_at ? formatDateTime(x.expires_at) : 'No expiry'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : <EmptyState title="Nothing is shared from this scope">Every output here is private until someone approves it.</EmptyState>}
      </Card>

      <Card title="Approvals" subtitle="Every approval ever recorded here, including revoked and expired ones.">
        {approvals.isLoading || !index ? <Loading /> : approvals.error ? <ErrorState error={approvals.error} /> : approvals.data && approvals.data.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Output</th><th>Approved by</th><th>Audience</th><th>Purpose</th><th>Status</th><th>Approved</th><th /></tr></thead>
              <tbody>
                {approvals.data.map((a) => {
                  const eff = effective(a, pmap)
                  const f = index.field(a.dataset_id, a.field_name)
                  return (
                    <tr key={a.id} className={eff === 'active' ? '' : 'dim'}>
                      <td><strong>{f?.label ?? a.field_name}</strong><div className="tiny muted">{index.datasetLabel(a.dataset_id)}</div>
                        <TechnicalDetails items={{ Approval: a.id, Dataset: a.dataset_id, Field: a.field_name, Run: a.source_run_id }} /></td>
                      <td>{pmap.get(a.participant_id)?.name ?? 'Unknown participant'}</td>
                      <td className="small">{a.audience_organization_id ? orgName(a.audience_organization_id) : 'Whole network'}{a.collaboration_case_id ? ' · case only' : ''}</td>
                      <td className="small">{a.purpose ?? '—'}</td>
                      <td><Badge tone={eff === 'active' ? 'shared' : 'warning'}>{EFFECT_LABEL[eff]}</Badge></td>
                      <td className="small">{formatDateTime(a.approved_at)}{a.expires_at ? <div className="tiny muted">Expires {formatDateTime(a.expires_at)}</div> : null}</td>
                      <td><ApprovalActions approval={a} active={eff === 'active'} /></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : <EmptyState title="No approvals recorded" />}
      </Card>

      <Card title="Participants" subtitle="Governance actors that approve outputs on behalf of an organization.">
        {participants.isLoading ? <Loading /> : participants.error ? <ErrorState error={participants.error} /> : participants.data && participants.data.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Participant</th><th>Status</th><th>Description</th></tr></thead>
              <tbody>
                {participants.data.map((p) => (
                  <tr key={p.id}>
                    <td><strong>{p.name}</strong> {p.metadata && (p.metadata as { synthetic?: boolean }).synthetic ? <SyntheticBadge /> : null}<div className="tiny muted mono">{p.participant_key}</div></td>
                    <td><StatusBadge status={p.status} /></td>
                    <td className="small">{p.description ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <EmptyState title="No participants registered" />}
      </Card>

      <AuditLog />
    </div>
  )
}
