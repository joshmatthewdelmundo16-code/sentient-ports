/** Governance actions: approve an output for an audience, revoke, and the audit trail.
 *  Every rule is enforced by the server; the form only collects intent. */
import { useState, type FormEvent } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api, ApiError } from '../../api/client'
import { scoped, scopedGet, useCatalog, useInvalidateScope, useParticipants } from '../../api/queries'
import type { Approval } from '../../api/types'
import { kindLabel, useScope } from '../../app/scope'
import { formatDateTime, statusLabel } from '../../shared/format'
import { Button, LoadingButton } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { useToast } from '../../components/ui/Toast'
import { Badge, Callout, Card, EmptyState, Loading, TechnicalDetails } from '../../shared/ui'

export function useOrgNames(): (id: string | null | undefined) => string {
  const { session } = useScope()
  const orgs = new Map((session?.organizations ?? []).map((o) => [o.id, o.name]))
  return (id) => (id ? orgs.get(id) ?? 'Another organization' : 'Whole network')
}

export function NewApproval() {
  const { can, scope, session } = useScope()
  const participants = useParticipants()
  const catalog = useCatalog()
  const invalidate = useInvalidateScope()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ participant_id: '', dataset_id: '', field_name: '', audience: '', purpose: '', expires: '' })
  const create = useMutation({
    mutationFn: () => api<Approval>('/api/approved-outputs', {
      json: {
        participant_id: form.participant_id,
        dataset_id: form.dataset_id,
        field_name: form.field_name,
        purpose: form.purpose.trim(),
        audience_organization_id: form.audience || null,
        expires_at: form.expires ? new Date(`${form.expires}T23:59:59Z`).toISOString() : null,
      },
    }),
    onSuccess: () => {
      invalidate(); setOpen(false); setForm({ participant_id: '', dataset_id: '', field_name: '', audience: '', purpose: '', expires: '' })
      toast.success('Output Approved', 'Recorded in the audit trail.')
    },
  })
  if (!can('approver') || !scope) return null
  const mine = (participants.data ?? []).filter((p) => p.status === 'active')
  const datasets = catalog.data ?? []
  const fields = datasets.find((d) => d.id === form.dataset_id)?.fields ?? []
  const audiences = (session?.organizations ?? []).filter((o) => o.id !== scope.id)
  const ready = form.participant_id && form.dataset_id && form.field_name && form.purpose.trim()
  const submit = (e: FormEvent) => { e.preventDefault(); if (ready && !create.isPending) create.mutate() }

  if (!open) {
    return (
      <div className="row">
        <button className="btn btn-primary" onClick={() => setOpen(true)} data-testid="open-approval">Approve an output for sharing</button>
        <span className="small muted">Requires the approver role. Every approval is recorded in the audit trail.</span>
      </div>
    )
  }
  return (
    <Card title="Approve an output for sharing" subtitle="Share exactly one field, with one audience, for a stated purpose.">
      <form className="stack" onSubmit={submit}>
        <div className="grid-3">
          <div className="field">
            <label htmlFor="ap-participant">Approving participant</label>
            <select id="ap-participant" className="input" value={form.participant_id} onChange={(e) => setForm({ ...form, participant_id: e.target.value })}>
              <option value="">Choose…</option>
              {mine.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ap-dataset">Dataset</label>
            <select id="ap-dataset" className="input" value={form.dataset_id} onChange={(e) => setForm({ ...form, dataset_id: e.target.value, field_name: '' })}>
              <option value="">Choose…</option>
              {datasets.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ap-field">Field</label>
            <select id="ap-field" className="input" value={form.field_name} onChange={(e) => setForm({ ...form, field_name: e.target.value })} disabled={!form.dataset_id}>
              <option value="">Choose…</option>
              {fields.map((f) => <option key={f.name} value={f.name}>{f.label}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ap-audience">Shared with</label>
            <select id="ap-audience" className="input" value={form.audience} onChange={(e) => setForm({ ...form, audience: e.target.value })}>
              <option value="">Whole network</option>
              {audiences.map((o) => <option key={o.id} value={o.id}>{o.name} · {kindLabel(o.kind)}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ap-purpose">Purpose</label>
            <input id="ap-purpose" className="input" value={form.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value })} placeholder="For example: regional capacity planning" />
          </div>
          <div className="field">
            <label htmlFor="ap-expires">Expires (optional)</label>
            <input id="ap-expires" type="date" className="input" value={form.expires} onChange={(e) => setForm({ ...form, expires: e.target.value })} />
          </div>
        </div>
        {create.error ? <Callout tone="danger">{(create.error as Error).message}</Callout> : null}
        <div className="row">
          <LoadingButton variant="primary" type="submit" loading={create.isPending} loadingLabel="Approving..." disabled={!ready}>Approve</LoadingButton>
          <button className="btn" type="button" onClick={() => setOpen(false)}>Cancel</button>
        </div>
      </form>
    </Card>
  )
}

export function ApprovalActions({ approval, active }: { approval: Approval; active: boolean }) {
  const { can } = useScope()
  const invalidate = useInvalidateScope()
  const toast = useToast()
  const [confirming, setConfirming] = useState(false)
  const revoke = useMutation({
    mutationFn: () => api(`/api/approved-outputs/${approval.id}/revoke`, { method: 'POST' }),
    onSuccess: () => { invalidate(); toast.success('Approval Revoked', 'This output is no longer shared.') },
    onError: (e) => toast.error('Revoke Failed', e.message),
  })
  if (!active || !can('approver')) return null
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setConfirming(true)} title="Stop sharing this output now">Revoke</Button>
      <ConfirmDialog
        open={confirming}
        tone="danger"
        title="Revoke Approval?"
        confirmLabel="Revoke Approval"
        busyLabel="Revoking..."
        onConfirm={() => revoke.mutateAsync()}
        onClose={() => setConfirming(false)}
      >
        This output will stop being shared with its audience immediately. The revocation is recorded in the audit trail.
      </ConfirmDialog>
    </>
  )
}

interface AuditEvent {
  id: string
  occurred_at: string
  action: string
  outcome: string
  actor_label: string | null
  target_type: string | null
  target_id: string | null
  summary: string | null
  detail: Record<string, unknown> | null
}

const ACTION: Record<string, string> = {
  'auth.login': 'Signed in',
  'auth.login_failed': 'Sign-in failed',
  'auth.logout': 'Signed out',
  'approval.created': 'Approval granted',
  'approval.revoked': 'Approval revoked',
  'approval.denied': 'Approval refused',
  'exposure.denied': 'Exposure denied',
  'exposure.granted': 'Exposure granted',
  'ingestion.committed': 'Workbook committed',
  'ingestion.rejected': 'Workbook rejected',
  'scenario.executed': 'Scenario run',
  'baseline.executed': 'Baseline run',
  'share.materialized': 'Shared outputs refreshed',
  'case.created': 'Collaboration case opened',
  'case.member_added': 'Case member added',
  'case.output_shared': 'Output shared into case',
  'case.closed': 'Collaboration case closed',
  'connector.run': 'Connector run',
  'webhook.rejected': 'Webhook rejected',
  'access.denied': 'Access denied',
  'plan.evaluated': 'Plan evaluated',
  'optimization.run': 'Optimization run',
  'member.added': 'Member added',
}

export function AuditLog() {
  const auditGet = scopedGet<AuditEvent[]>('/api/audit?limit=60')
  const audit = useQuery({
    queryKey: scoped('audit'),
    queryFn: async () => {
      try { return await auditGet() } catch (e) {
        if (e instanceof ApiError && (e.status === 404 || e.status === 403)) return null
        throw e
      }
    },
  })
  if (audit.data === null) return null
  return (
    <Card title="Audit trail" subtitle="Append-only record of governance and security events in this scope, including denials.">
      {audit.isLoading ? <Loading /> : !audit.data?.length ? <EmptyState title="No audit events yet" /> : (
        <div className="table-wrap">
          <table className="table" data-testid="audit-table">
            <thead><tr><th>When</th><th>Event</th><th>By</th><th>Outcome</th><th>Details</th></tr></thead>
            <tbody>
              {audit.data.map((e) => (
                <tr key={e.id}>
                  <td className="small nowrap">{formatDateTime(e.occurred_at)}</td>
                  <td><strong>{ACTION[e.action] ?? e.action}</strong>{e.summary ? <div className="tiny muted">{e.summary}</div> : null}</td>
                  <td className="small">{e.actor_label ?? 'System'}</td>
                  <td><Badge tone={e.outcome === 'success' ? 'success' : e.outcome === 'denied' ? 'danger' : 'warning'}>{statusLabel(e.outcome)}</Badge></td>
                  <td><TechnicalDetails items={{ Target: e.target_type ? `${e.target_type} ${e.target_id ?? ''}` : null, Event: e.id, ...Object.fromEntries(Object.entries(e.detail ?? {}).map(([k, v]) => [k, typeof v === 'string' || typeof v === 'number' ? v : JSON.stringify(v)])) }} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
