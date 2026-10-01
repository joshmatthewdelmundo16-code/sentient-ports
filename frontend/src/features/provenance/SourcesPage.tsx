import { useState } from 'react'
import { useCatalog, useExecution, useRunLineage } from '../../api/queries'
import type { CatalogDataset } from '../../api/types'
import { useSelection } from '../../app/selection'
import { formatDateTime, formatValue, shortId, sourceLabel } from '../../shared/format'
import { Badge, Card, EmptyState, ErrorState, Loading, PageHeader, Tabs, TechnicalDetails } from '../../shared/ui'
import { useCatalogIndex } from '../common'

function DatasetCard({ d }: { d: CatalogDataset }) {
  const src = d.current_source
  return (
    <Card
      tight
      title={d.label}
      subtitle={d.role === 'source' ? `Source data · read by ${d.consumed_by.map((c) => c.model_name).join(', ') || 'no model'}` : `Result of ${d.produced_by.map((p) => p.model_name).join(', ')}`}
      actions={<Badge tone={d.role === 'source' ? 'info' : 'neutral'}>{d.role === 'source' ? 'Source' : 'Model result'}</Badge>}
    >
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Field</th><th className="num">Current value</th><th>Contract</th></tr></thead>
          <tbody>
            {d.fields.map((f) => (
              <tr key={f.name}>
                <td>{f.label}</td>
                <td className="num">{formatValue(d.value?.[f.name] ?? null, f.unit)}</td>
                <td className="small muted">
                  {f.type}{f.nullable ? ', may be empty' : ''}
                  {f.min !== null ? `, ≥ ${formatValue(f.min, f.unit)}` : ''}{f.max !== null ? `, ≤ ${formatValue(f.max, f.unit)}` : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="small" style={{ marginTop: 10 }}>
        <strong>Current value came from:</strong>{' '}
        {src ? <>{sourceLabel(src.source_type)}{src.source_ref && src.source_type !== 'model_output' ? ` · ${src.source_ref}` : ''} · {formatDateTime(src.at)}</> : 'No value recorded yet'}
      </div>
      <TechnicalDetails items={{
        Dataset: d.id, Name: d.name, 'Value hash': d.value_hash, Contract: d.contract ? `${d.contract.id} (v${d.contract.semver})` : null,
        'Change event': src?.change_event_id, 'Produced by run': src?.produced_by_run_id, 'Triggered by': src?.triggered_by,
      }} />
    </Card>
  )
}

function Lineage({ runId, title }: { runId: string; title: string }) {
  const detail = useExecution(runId)
  const lineage = useRunLineage(runId)
  const { index } = useCatalogIndex()
  if (detail.isLoading || lineage.isLoading || !index) return <Loading />
  if (detail.error) return <ErrorState error={detail.error} />
  const stepModel = new Map((detail.data?.steps ?? []).map((s) => [s.id, index.modelName(s.model_version_id)]))
  const edges = lineage.data ?? []
  return (
    <Card title={title} subtitle={`${edges.length} lineage edge${edges.length === 1 ? '' : 's'} recorded by this run.`}>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>From</th><th>Through model</th><th>To</th><th>Origin</th></tr></thead>
          <tbody>
            {edges.map((e) => (
              <tr key={e.id}>
                <td>{index.datasetLabel(e.source_dataset_id)}</td>
                <td>{stepModel.get(e.step_id) ?? '—'}</td>
                <td>{index.datasetLabel(e.target_dataset_id)}</td>
                <td className="small muted">
                  {e.source_change_event_id ? <>Recorded change <span className="mono">{shortId(e.source_change_event_id)}</span></> : e.source_result_id ? 'Earlier step in this run' : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

export function SourcesPage() {
  const catalog = useCatalog()
  const { baseline, scenario } = useSelection()
  const [tab, setTab] = useState<'sources' | 'results' | 'lineage'>('sources')
  const data = catalog.data ?? []
  const sources = data.filter((d) => d.role === 'source')
  const results = data.filter((d) => d.role === 'model_output')

  return (
    <div className="stack-lg">
      <PageHeader title="Sources & Provenance" description="Every value, where it came from, when, and which run or upload produced it." />
      <Tabs label="Provenance views" value={tab} onChange={setTab} tabs={[
        { id: 'sources', label: `Source data (${sources.length})` },
        { id: 'results', label: `Model results (${results.length})` },
        { id: 'lineage', label: 'Lineage' },
      ]} />
      {catalog.isLoading ? <Loading lines={6} /> : catalog.error ? <ErrorState error={catalog.error} /> : tab === 'lineage' ? (
        <div className="stack">
          {baseline?.run ? <Lineage runId={baseline.run.id} title={`Baseline run · ${baseline.name}`} /> : null}
          {scenario?.run ? <Lineage runId={scenario.run.id} title={`Scenario run · ${scenario.name}`} /> : null}
          {!baseline?.run && !scenario?.run ? <EmptyState title="No runs to trace yet" /> : null}
        </div>
      ) : (
        <div className="grid-2">
          {(tab === 'sources' ? sources : results).map((d) => <DatasetCard key={d.id} d={d} />)}
          {(tab === 'sources' ? sources : results).length === 0 ? <EmptyState title="Nothing here yet" /> : null}
        </div>
      )}
    </div>
  )
}
