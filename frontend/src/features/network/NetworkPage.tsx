/** Port network — D27. Hub views built only from outputs another organization actively
 *  approved: never a private figure, never an assumption. A hub aggregates its members'
 *  approved indicators and shows exactly who reported, so a missing figure reads as
 *  "not shared", never as zero. */
import { useMemo } from 'react'
import { ApiError } from '../../api/client'
import { useHubView, useMaterializeHub, useNetworkHierarchy, useSharedWithMe } from '../../api/queries'
import type { HubMember, SharedValue } from '../../api/types'
import { kindLabel, useScope } from '../../app/scope'
import { LoadingButton } from '../../components/ui/Button'
import { Kpi, KpiRow } from '../../components/ui/Kpi'
import { Pill } from '../../components/ui/Pill'
import { useToast } from '../../components/ui/Toast'
import { formatDateTime, formatValue } from '../../shared/format'
import { Badge, Callout, Card, EmptyState, ErrorState, Loading, PageHeader, TechnicalDetails } from '../../shared/ui'
import { NetworkMap } from './NetworkMap'

function audienceLabel(v: SharedValue): string {
  if (v.audience === 'you') return 'Shared With You'
  if (v.audience === 'case') return 'Shared Into A Case'
  return 'Whole Network'
}

function SharedWithMeTable({ items }: { items: SharedValue[] }) {
  if (!items.length) {
    return <EmptyState title="Nothing Has Been Shared With You Yet">Other organizations' outputs appear here only after they approve a field for your organization, your network, or a case you both belong to.</EmptyState>
  }
  return (
    <div className="table-wrap">
      <table className="grid">
        <thead><tr><th>From</th><th>Output</th><th className="right">Value</th><th>Audience</th><th>Purpose</th><th>Expires</th></tr></thead>
        <tbody>
          {items.map((v) => (
            <tr key={v.approval_id}>
              <td><div className="cellmain">{v.provider_org_name}</div><div className="sub">{kindLabel(v.provider_kind)}</div></td>
              <td><div className="cellmain">{v.field_label}</div><div className="sub">{v.dataset_label}{v.value_source === 'run' ? ' · from a specific run' : ''}</div></td>
              <td className="right num">{v.value_present ? formatValue(v.value, v.unit) : 'Not available'}</td>
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
      <td><div className="cellmain">{member.name}</div><div className="sub">{kindLabel(member.kind)}</div></td>
      {indicatorKeys.map((key) => {
        const cell = member.indicators.find((c) => c.indicator === key)
        if (!cell) return <td key={key} className="right num">—</td>
        return (
          <td key={key} className="right num">
            {cell.shared ? (
              <span title={cell.purpose ?? undefined}>{formatValue(cell.value)}</span>
            ) : (
              <Pill tone="neutral">Not Shared</Pill>
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
  const toast = useToast()

  if (hub.isLoading) return <Card title="Hub View"><Loading /></Card>
  if (hub.error) {
    const status = hub.error instanceof ApiError ? hub.error.status : 0
    if (status === 404) {
      return (
        <Callout tone="neutral" title="This Scope Is Not A Hub.">
          A hub view aggregates the approved outputs of the ports and hubs beneath an organization in the hierarchy.
          The organization you are scoped to has no members beneath it, so there is nothing to aggregate here — see
          “Shared With You” below for outputs shared directly with this organization instead.
        </Callout>
      )
    }
    return <Card title="Hub View"><ErrorState error={hub.error} onRetry={() => void hub.refetch()} /></Card>
  }
  const overview = hub.data
  if (!overview) return null
  const noMembers = overview.members.length === 0 && overview.sub_hubs.length === 0

  return (
    <>
      <Card
        title={`${overview.hub.name} — Hub Aggregate`}
        subtitle="Computed only from active, unexpired approvals from active participants. A port that has not shared a figure is counted as missing, never as zero."
        actions={
          <LoadingButton size="sm" loading={materialize.isPending} loadingLabel="Publishing..."
            title="Write this aggregate into the hub's own dataset and propagate it through the graph"
            onClick={() => materialize.mutate(undefined, {
              onSuccess: (r) => toast.success('Aggregate Published', r.changed ? 'The hub dataset changed and was propagated.' : 'The aggregate was unchanged; nothing to propagate.'),
            })}>
            Publish Aggregate
          </LoadingButton>
        }
      >
        {noMembers ? (
          <EmptyState title="No Member Ports Or Hubs Beneath This Organization" />
        ) : (
          <KpiRow>
            {overview.aggregates.map((a) => (
              <Kpi
                key={a.key}
                testId="hub-kpi"
                label={a.label}
                value={a.value === null ? '—' : formatValue(a.value, a.unit)}
                meta={`${a.reporting} Of ${a.in_scope} Member${a.in_scope === 1 ? '' : 's'} Reporting`}
              />
            ))}
            {Array.from({ length: (4 - (overview.aggregates.length % 4)) % 4 }, (_, i) => <div key={`pad-${i}`} className="kpi pad" aria-hidden="true" />)}
          </KpiRow>
        )}
        {materialize.error ? <Callout tone="danger">{(materialize.error as Error).message}</Callout> : null}
      </Card>

      {overview.members.length ? (
        <Card flush title="Member Ports" subtitle="What each member has approved for sharing with this hub.">
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>Port</th>
                  {overview.indicators.map((i) => <th key={i.key} className="right">{i.label}</th>)}
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
        <Card title="Sub-Hubs" subtitle="Hubs beneath this one, and what they have shared directly.">
          <div className="meta-list">
            {overview.sub_hubs.map((h) => (
              <div key={h.id} className="mrow">
                <span className="mv" style={{ textAlign: 'left' }}>{h.name} <span className="muted small">{kindLabel(h.kind)}</span></span>
                <span className="mk num">{h.shared.length} Output{h.shared.length === 1 ? '' : 's'} Shared</span>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {overview.shared_from_outside.length ? (
        <Card flush title="Shared From Outside This Hierarchy" subtitle="Approved outputs visible to this hub from organizations that are not its members.">
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
  const hub = useHubView()

  const reporting = useMemo(() => {
    const m = new Map<string, { shared: number; total: number }>()
    const o = hub.data
    if (o) for (const member of o.members) m.set(member.id, { shared: member.indicators.filter((c) => c.shared).length, total: o.indicators.length })
    return m
  }, [hub.data])
  const sharedCounts = useMemo(() => {
    const m = new Map<string, number>()
    for (const v of sharedWithMe.data ?? []) m.set(v.provider_org_id, (m.get(v.provider_org_id) ?? 0) + 1)
    return m
  }, [sharedWithMe.data])

  return (
    <div className="stack">
      <PageHeader
        title="Port Network"
        description="Hub views built only from approved outputs — never a private figure, never an assumption. What you see here is exactly what other organizations chose to share, with whom, and why."
      />

      <Callout tone="neutral" title="How This Works.">
        An organization approves one field, for one audience — your organization, its whole network, or one collaboration
        case — on the <a href="/app/governance">Governance</a> page. Nothing crosses an organization boundary any other
        way. A hub then aggregates what its members shared; ports that reported nothing are counted as missing, never
        imputed to zero.
      </Callout>

      {hierarchy.isLoading ? <Card title="Network Map"><Loading lines={5} /></Card> : hierarchy.error ? (
        <Card title="Network Map"><ErrorState error={hierarchy.error} onRetry={() => void hierarchy.refetch()} /></Card>
      ) : hierarchy.data && hierarchy.data.length ? (
        <NetworkMap roots={hierarchy.data} scopeId={scope?.id} reporting={reporting} sharedWithYou={sharedCounts} />
      ) : <Card title="Network Map"><EmptyState title="No Organizations Registered" /></Card>}

      <HubSection />

      <Card flush title="Shared With You" subtitle="Every output approved for this organization, its network, or an open case it belongs to.">
        {sharedWithMe.isLoading ? <Loading /> : sharedWithMe.error ? <ErrorState error={sharedWithMe.error} onRetry={() => void sharedWithMe.refetch()} /> : (
          <SharedWithMeTable items={sharedWithMe.data ?? []} />
        )}
      </Card>

      <TechnicalDetails items={{ 'Viewing as': scope?.name, Scope: scope?.id }} />
    </div>
  )
}
