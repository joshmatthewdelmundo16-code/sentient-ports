/** Helpers shared by several features. */
import { useMemo } from 'react'
import { useCatalog, useFederationMap } from '../api/queries'
import type { CatalogDataset, FieldMeta, Metric, Scalar } from '../api/types'
import { deltaClass } from '../components/data/delta'
import { Kpi } from '../components/ui/Kpi'
import { formatDelta, formatPct, formatTransition, formatValue } from '../shared/format'

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
  const numeric = metric.kind === 'numeric'
  const cls = deltaClass(metric.direction)
  const meta = !metric.changed ? (
    <span>Same As Baseline</span>
  ) : numeric ? (
    <>
      {metric.relative_delta !== null ? <span className={`delta ${cls}`}>{formatPct(metric.relative_delta)}</span> : null}
      <span className={`delta ${cls}`}>{formatDelta(metric.absolute_delta, metric.unit)}</span>
      <span>Baseline {formatValue(metric.baseline, metric.unit)}</span>
    </>
  ) : (
    <>
      <span className="delta up">Changed</span>
      <span>Baseline {formatValue(metric.baseline, metric.unit)}</span>
    </>
  )
  return <Kpi testId="kpi-card" label={metric.field_label} value={formatValue(metric.scenario, metric.unit)} meta={meta} />
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
