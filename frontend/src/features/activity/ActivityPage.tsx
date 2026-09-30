import { useState } from 'react'
import { Link } from 'react-router'
import { useActivity } from '../../api/queries'
import type { ActivityItem, Headline } from '../../api/types'
import { formatDateTime, formatTransition, formatValue, sourceLabel, statusLabel } from '../../shared/format'
import { EmptyState, ErrorState, Loading, PageHeader, Tabs, TechnicalDetails } from '../../shared/ui'

const ICON: Record<string, string> = {
  scenario_run: '◇', baseline_run: '◆', dataset_change: '↻', upload: '⤒', model_run: '▸', plan_run: '▦', governance: '⚑',
}

export function headlineText(h: Headline | null | undefined): string | null {
  if (!h) return null
  if (h.from !== undefined || h.to !== undefined) {
    return `${h.label}: ${formatTransition(h.from ?? null, h.to ?? null, h.unit)}`
  }
  return `${h.label}: ${formatValue(h.value ?? null, h.unit)}`
}

export function ActivityRow({ item }: { item: ActivityItem }) {
  const isChange = item.kind === 'dataset_change'
  const headline = isChange && item.headline
    ? `${formatTransition(item.headline.from ?? null, item.headline.to ?? null, item.headline.unit)}`
    : headlineText(item.headline)
  const technical: Record<string, string | null | undefined> = { ...item.technical }
  const via = isChange && item.via ? (item.via.kind === 'upload' ? `via ${item.via.file_name}` : sourceLabel(item.via.source_type)) : null
  const contextLine = [item.context, via].filter(Boolean).join(' · ')
  return (
    <li className="activity-item" data-testid="activity-item">
      <span className={`activity-icon ${item.kind}`} aria-hidden="true">{ICON[item.kind] ?? '•'}</span>
      <div>
        <div className="activity-title">
          {item.title}<span className="sep">·</span>{item.subject}
        </div>
        {contextLine ? <div className="activity-meta">{contextLine}</div> : null}
        <div className="activity-meta">{formatDateTime(item.occurred_at)} · {statusLabel(item.status)}</div>
        {headline ? <div className="activity-headline">{headline}</div> : null}
        {item.effect ? <div className="activity-headline muted">Result: {headlineText(item.effect)}</div> : null}
        {item.error ? <div className="activity-headline" style={{ color: 'var(--danger)' }}>{item.error}</div> : null}
        <div className="row" style={{ gap: 12 }}>
          {item.links.run_id ? <Link className="tiny" to={`/execution?run=${item.links.run_id}`}>Open run</Link> : null}
          <TechnicalDetails items={technical} />
        </div>
      </div>
    </li>
  )
}

type Filter = 'all' | 'runs' | 'changes' | 'uploads'

export function ActivityPage() {
  const activity = useActivity(80)
  const [filter, setFilter] = useState<Filter>('all')
  const items = (activity.data ?? []).filter((i) =>
    filter === 'all' ? true :
    filter === 'runs' ? ['scenario_run', 'baseline_run', 'model_run', 'plan_run'].includes(i.kind) :
    filter === 'changes' ? i.kind === 'dataset_change' :
    i.kind === 'upload' || i.via?.kind === 'upload',
  )
  return (
    <div className="stack-lg">
      <PageHeader title="Activity" description="What happened in this scope, newest first — described in business terms. Identifiers are under technical details." />
      <Tabs label="Filter activity" value={filter} onChange={setFilter} tabs={[
        { id: 'all', label: 'All' }, { id: 'runs', label: 'Runs' }, { id: 'changes', label: 'Dataset changes' }, { id: 'uploads', label: 'Uploads' },
      ]} />
      {activity.isLoading ? <Loading lines={6} /> : activity.error ? <ErrorState error={activity.error} onRetry={() => void activity.refetch()} /> :
        items.length === 0 ? <EmptyState title="No activity yet">Runs, dataset changes and uploads appear here as they happen.</EmptyState> : (
          <div className="card">
            <ul className="activity">{items.map((i) => <ActivityRow key={i.id} item={i} />)}</ul>
          </div>
        )}
    </div>
  )
}
