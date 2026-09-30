/** Helpers shared by several features. */
import { useMemo } from 'react'
import { useCatalog, useFederationMap } from '../api/queries'
import type { CatalogDataset, FieldMeta, Metric, Scalar } from '../api/types'
import { formatPct, formatTransition, formatValue } from '../shared/format'

export interface CatalogIndex {
  datasets: Map<string, CatalogDataset>
  field: (datasetId: string, field: string) => FieldMeta | undefined
  datasetLabel: (datasetId: string | null | undefined) => string
  modelName: (versionId: string | null | undefined) => string
  formula: (versionId: string | null | undefined) => string | null
}

export function useCatalogIndex(): { index: CatalogIndex | null; loading: boolean; error: unknown } {
  const catalog = useCatalog()
  const map = useFederationMap()
  const index = useMemo<CatalogIndex | null>(() => {
    if (!catalog.data || !map.data) return null
    const datasets = new Map(catalog.data.map((d) => [d.id, d]))
    const models = new Map(map.data.models.map((m) => [m.version_id, m]))
    return {
      datasets,
      field: (d, f) => datasets.get(d)?.fields.find((x) => x.name === f),
      datasetLabel: (d) => (d ? datasets.get(d)?.label ?? 'Unknown dataset' : 'Unknown dataset'),
      modelName: (v) => (v ? models.get(v)?.name ?? 'Unknown model' : 'Unknown model'),
      formula: (v) => (v ? models.get(v)?.formula ?? null : null),
    }
  }, [catalog.data, map.data])
  return { index, loading: catalog.isLoading || map.isLoading, error: catalog.error ?? map.error }
}

export function KpiCard({ metric }: { metric: Metric }) {
  const moved = metric.changed
  const dir = metric.direction === 'increase' ? 'up' : metric.direction === 'decrease' ? 'down' : 'none'
  return (
    <div className={`card card-tight kpi ${moved ? 'changed' : ''}`} data-testid="kpi-card">
      <div className="kpi-label">{metric.field_label}</div>
      <div className="kpi-value">{formatValue(metric.scenario, metric.unit)}</div>
      <div className="kpi-from">
        {moved ? <>Baseline {formatValue(metric.baseline, metric.unit)}</> : <>Same as baseline</>}
      </div>
      {metric.kind === 'numeric' ? (
        <span className={`kpi-delta ${moved ? dir : 'none'}`}>
          {moved ? (
            <>
              <span aria-hidden="true">{dir === 'up' ? '▲' : '▼'}</span>
              {metric.relative_delta !== null ? formatPct(metric.relative_delta) : 'Changed'}
            </>
          ) : (
            'No change'
          )}
        </span>
      ) : moved ? (
        <span className="kpi-delta up">Changed</span>
      ) : null}
    </div>
  )
}

/** Terminal numeric/boolean metrics in contract order — what a decision-maker reads first. */
export function headlineMetrics(metrics: Metric[]): Metric[] {
  const terminal = metrics.filter((m) => m.terminal)
  const pool = terminal.length ? terminal : metrics
  return [...pool].sort((a, b) => a.field_order - b.field_order)
}

export function transitionText(from: Scalar | undefined, to: Scalar | undefined, unit: string | null | undefined) {
  return formatTransition(from, to, unit)
}

export function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? ''
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}
