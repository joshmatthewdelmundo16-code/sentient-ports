/** Port network — D27. Hub views built only from outputs another organization actively
 *  approved: never a private figure, never an assumption. A hub aggregates its members'
 *  approved indicators and shows exactly who reported, so a missing figure reads as
 *  "not shared", never as zero. */
import { ApiError } from '../../api/client'
import { useHubView, useMaterializeHub, useNetworkHierarchy, useSharedWithMe } from '../../api/queries'
import type { HierarchyNode, HubMember, SharedValue } from '../../api/types'
import { kindLabel, useScope } from '../../app/scope'
import { formatDateTime, formatValue } from '../../shared/format'
import { Badge, Callout, Card, EmptyState, ErrorState, Loading, PageHeader, TechnicalDetails } from '../../shared/ui'

function HierarchyTree({ nodes }: { nodes: HierarchyNode[] }) {
  return (
    <ul className="hierarchy">
      {nodes.map((n) => (
        <li key={n.id}>
          <Badge tone={n.kind === 'port' ? 'private' : 'network'}>{kindLabel(n.kind)}</Badge>{' '}
          <strong>{n.name}</strong>{n.synthetic ? <span className="tiny muted"> · synthetic</span> : null}
          {n.children.length ? <HierarchyTree nodes={n.children} /> : null}
        </li>
      ))}
    </ul>
  )
}

function audienceLabel(v: SharedValue): string {
  if (v.audience === 'you') return 'Shared with you'
  if (v.audience === 'case') return 'Shared into a case'
  return 'Whole network'
}

function SharedWithMeTable({ items }: { items: SharedValue[] }) {
  if (!items.length) {
    return <EmptyState title="Nothing has been shared with you yet">Other organizations' outputs appear here only after they approve a field for your organization, your network, or a case you both belong to.</EmptyState>
  }
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>From</th><th>Output</th><th className="num">Value</th><th>Audience</th><th>Purpose</th><th>Expires</th></tr></thead>
        <tbody>
          {items.map((v) => (
            <tr key={v.approval_id}>
              <td><strong>{v.provider_org_name}</strong><div className="tiny muted">{kindLabel(v.provider_kind)}</div></td>
              <td>{v.field_label}<div className="tiny muted">{v.dataset_label}{v.value_source === 'run' ? ' · from a specific run' : ''}</div></td>
              <td className="num">{v.value_present ? formatValue(v.value, v.unit) : 'Not available'}</td>
              <td><Badge tone={v.audience === 'you' ? 'shared' : v.audience === 'case' ? 'info' : 'network'}>{audienceLabel(v)}</Badge></td>
              <td className="small">{v.purpose ?? '—'}</td>
              <td className="small">{v.expires_at ? formatDateTime(v.expires_at) : 'No expiry'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MemberRow({ member, indicatorKeys }: { member: HubMember; indicatorKeys: string[] }) {
  return (
    <tr>
      <td><strong>{member.name}</strong><div className="tiny muted">{kindLabel(member.kind)}</div></td>
      {indicatorKeys.map((key) => {
        const cell = member.indicators.find((c) => c.indicator === key)
        if (!cell) return <td key={key} className="num">—</td>
        return (
          <td key={key} className="num">
            {cell.shared ? (
              <span title={cell.purpose ?? undefined}>{formatValue(cell.value)}</span>
            ) : (
              <span className="tiny muted">Not shared</span>
            )}
          </td>
        )
      })}
    </tr>
  )
}

function HubSection() {
  const hub = useHubView()
  const materialize = useMaterializeHub()

  if (hub.isLoading) return <Card title="Hub view"><Loading /></Card>
  if (hub.error) {
    const status = hub.error instanceof ApiError ? hub.error.status : 0
    if (status === 404) {
      return (
        <Callout tone="neutral" title="This scope is not a hub.">
          A hub view aggregates the approved outputs of the ports and hubs beneath an organization in the hierarchy.
          The organization you are scoped to has no members beneath it, so there is nothing to aggregate here — see
          “Shared with you” below for outputs shared directly with this organization instead.
        </Callout>
      )
    }
    return <Card title="Hub view"><ErrorState error={hub.error} onRetry={() => void hub.refetch()} /></Card>
  }
  const overview = hub.data
  if (!overview) return null
  const noMembers = overview.members.length === 0 && overview.sub_hubs.length === 0

  return (
    <>
      <Card
        title={`${overview.hub.name} — hub aggregate`}
        subtitle="Computed only from active, unexpired approvals from active participants. A port that has not shared a figure is counted as missing, never as zero."
        actions={<button className="btn btn-sm" disabled={materialize.isPending} onClick={() => materialize.mutate()}
          title="Write this aggregate into the hub's own dataset and propagate it through the graph">
          {materialize.isPending ? 'Publishing…' : 'Publish aggregate'}
        </button>}
      >
        {noMembers ? (
          <EmptyState title="No member ports or hubs beneath this organization" />
        ) : (
          <div className="grid-4">
            {overview.aggregates.map((a) => (
              <div key={a.key} className="card card-tight kpi">
                <div className="kpi-label">{a.label}</div>
                <div className="kpi-value">{a.value === null ? '—' : formatValue(a.value, a.unit)}</div>
                <div className="kpi-from">{a.reporting} of {a.in_scope} member{a.in_scope === 1 ? '' : 's'} reporting</div>
              </div>
            ))}
          </div>
        )}
        {materialize.error ? <Callout tone="danger">{(materialize.error as Error).message}</Callout> : null}
        {materialize.data ? (
          <Callout tone="success" title="Published.">
            {materialize.data.changed ? 'The hub dataset changed and was propagated.' : 'The aggregate was unchanged; nothing to propagate.'}
          </Callout>
        ) : null}
      </Card>

      {overview.members.length ? (
        <Card title="Member ports" subtitle="What each member has approved for sharing with this hub.">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Port</th>
                  {overview.indicators.map((i) => <th key={i.key} className="num">{i.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {overview.members.map((m) => (
                  <MemberRow key={m.id} member={m} indicatorKeys={overview.indicators.map((i) => i.key)} />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      {overview.sub_hubs.length ? (
        <Card title="Sub-hubs" subtitle="Hubs beneath this one, and what they have shared directly.">
          <div className="stack">
            {overview.sub_hubs.map((h) => (
              <div key={h.id} className="row-between">
                <div><strong>{h.name}</strong> <span className="tiny muted">{kindLabel(h.kind)}</span></div>
                <div className="small muted">{h.shared.length} output{h.shared.length === 1 ? '' : 's'} shared</div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {overview.shared_from_outside.length ? (
        <Card title="Shared from outside this hierarchy" subtitle="Approved outputs visible to this hub from organizations that are not its members.">
          <SharedWithMeTable items={overview.shared_from_outside} />
        </Card>
      ) : null}
    </>
  )
}

export function NetworkPage() {
  const { scope } = useScope()
  const hierarchy = useNetworkHierarchy()
  const sharedWithMe = useSharedWithMe()

  return (
    <div className="stack-lg">
      <PageHeader
        title="Port Network"
        description="Hub views built only from approved outputs — never a private figure, never an assumption. What you see here is exactly what other organizations chose to share, with whom, and why."
      />

      <Callout tone="neutral" title="How this works.">
        An organization approves one field, for one audience — your organization, its whole network, or one collaboration
        case — on the <a href="/app/governance">Governance</a> page. Nothing crosses an organization boundary any other
        way. A hub then aggregates what its members shared; ports that reported nothing are counted as missing, never
        imputed to zero.
      </Callout>

      <HubSection />

      <Card title="Shared with you" subtitle="Every output approved for this organization, its network, or an open case it belongs to.">
        {sharedWithMe.isLoading ? <Loading /> : sharedWithMe.error ? <ErrorState error={sharedWithMe.error} onRetry={() => void sharedWithMe.refetch()} /> : (
          <SharedWithMeTable items={sharedWithMe.data ?? []} />
        )}
      </Card>

      <Card title="Network hierarchy" subtitle="Organization names and structure only — never private data.">
        {hierarchy.isLoading ? <Loading /> : hierarchy.error ? <ErrorState error={hierarchy.error} onRetry={() => void hierarchy.refetch()} /> : hierarchy.data && hierarchy.data.length ? (
          <HierarchyTree nodes={hierarchy.data} />
        ) : <EmptyState title="No organizations registered" />}
        <TechnicalDetails items={{ 'Viewing as': scope?.name, Scope: scope?.id }} />
      </Card>
    </div>
  )
}
