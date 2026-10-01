/** Turns a real scenario Explanation into the reference's four-stage ripple:
 *    Input  →  Directly Affected  →  Downstream  →  Decision Impact
 *  Everything is derived from the platform's own records (declared model inputs and the recorded
 *  results of the two runs). Nothing is written by hand and nothing is invented. */
import type { BaselineView, CatalogDataset, Explanation, MapModel, Metric, PathStep, ScenarioView } from '../../api/types'
import { executorLabel, formatDateTime, formatDelta, formatPct, formatTransition, shortId, sourceLabel, statusLabel } from '../../shared/format'
import { deltaClass } from '../data/delta'
import type { ProvStep } from '../data/ProvenanceSteps'
import type { IconName } from '../ui/Icon'

export type RippleKind = 'input' | 'affected' | 'downstream' | 'impact'

export type NodeRef =
  | { type: 'input'; datasetId: string; field: string }
  | { type: 'model'; versionId: string }
  | { type: 'result'; datasetId: string; field: string }

export interface RippleDelta {
  text: string
  cls: 'up' | 'down' | 'flat'
  /** Small caption under the value. */
  label: string
}

export interface RippleNode {
  id: string
  kind: RippleKind
  icon: IconName
  name: string
  sub: string
  delta?: RippleDelta
  ref: NodeRef
}

export interface RippleStage {
  kind: RippleKind
  label: string
  nodes: RippleNode[]
}

const key = (datasetId: string, field: string) => `${datasetId}:${field}`

function joinLabels(labels: string[]): string {
  return labels.join(', ')
}

function versionLabel(semver: string | undefined): string {
  if (!semver) return ''
  return semver.startsWith('v') ? semver : `v${semver}`
}

function metricDelta(m: Metric): RippleDelta {
  if (m.kind !== 'numeric') return { text: m.changed ? 'Changed' : 'No Change', cls: 'flat', label: m.field_label }
  const text = m.relative_delta !== null ? formatPct(m.relative_delta) : formatDelta(m.absolute_delta, m.unit)
  return { text, cls: deltaClass(m.direction), label: formatTransition(m.baseline, m.scenario, m.unit) }
}

function inputDelta(c: Explanation['changes'][number]): RippleDelta {
  const b = c.baseline_value
  const s = c.scenario_value
  const label = formatTransition(b, s, c.unit)
  if (typeof b === 'number' && typeof s === 'number') {
    if (b !== 0) {
      const rel = (s - b) / b
      return { text: formatPct(rel), cls: rel > 0 ? 'up' : rel < 0 ? 'down' : 'flat', label }
    }
    return { text: formatDelta(s - b, c.unit), cls: s > b ? 'up' : s < b ? 'down' : 'flat', label }
  }
  return { text: 'Changed', cls: 'flat', label }
}

export function buildRipple(ex: Explanation, models: MapModel[] = []): RippleStage[] {
  const metrics = new Map(ex.comparison.metrics.map((m) => [key(m.dataset_id, m.field), m]))
  const changed = new Set(ex.changes.map((c) => key(c.dataset_id, c.field)))
  const version = new Map(models.map((m) => [m.version_id, m.semver]))

  const modelNode = (step: PathStep, kind: 'affected' | 'downstream'): RippleNode => {
    const first = step.outputs_changed[0]
    const m = first ? metrics.get(key(first.dataset_id, first.field)) : undefined
    const more = step.outputs_changed.length - 1
    const v = versionLabel(version.get(step.version_id))
    const sub = [v, `Reads ${joinLabels(step.reads_changed.map((r) => r.field_label))}`, more > 0 ? `+${more} More Result${more === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ')
    return {
      id: `model:${step.version_id}`,
      kind,
      icon: 'cpu',
      name: step.model_name,
      sub,
      delta: m ? metricDelta(m) : { text: 'No Change', cls: 'flat', label: 'Results' },
      ref: { type: 'model', versionId: step.version_id },
    }
  }

  const direct = ex.path.filter((s) => s.reads_changed.some((r) => changed.has(key(r.dataset_id, r.field))))
  const directIds = new Set(direct.map((s) => s.version_id))
  const downstream = ex.path.filter((s) => !directIds.has(s.version_id))

  const impacts = ex.comparison.metrics
    .filter((m) => m.terminal && m.changed)
    .sort((a, b) => a.field_order - b.field_order)

  const stages: RippleStage[] = [
    {
      kind: 'input',
      label: 'Input',
      nodes: ex.changes.map((c) => ({
        id: `input:${key(c.dataset_id, c.field)}`,
        kind: 'input' as const,
        icon: 'sliders' as const,
        name: c.field_label,
        sub: `Assumption · ${c.dataset_label}`,
        delta: inputDelta(c),
        ref: { type: 'input' as const, datasetId: c.dataset_id, field: c.field },
      })),
    },
    { kind: 'affected', label: 'Affected Models', nodes: direct.map((s) => modelNode(s, 'affected')) },
    { kind: 'downstream', label: 'Downstream', nodes: downstream.map((s) => modelNode(s, 'downstream')) },
    {
      kind: 'impact',
      label: 'Decision Impact',
      nodes: impacts.map((m) => ({
        id: `result:${key(m.dataset_id, m.field)}`,
        kind: 'impact' as const,
        icon: 'target' as const,
        name: m.field_label,
        sub: m.dataset_label,
        delta: metricDelta(m),
        ref: { type: 'result' as const, datasetId: m.dataset_id, field: m.field },
      })),
    },
  ]
  return stages.filter((s) => s.nodes.length > 0)
}

export interface NodeProvenanceContext {
  explanation: Explanation
  scenario: ScenarioView
  baseline: BaselineView
  sources: CatalogDataset[]
  models: MapModel[]
}

export interface NodeProvenance {
  title: string
  subtitle: string
  steps: ProvStep[]
}

const runMeta = (r: ScenarioView['run']) =>
  r ? `${executorLabel(r.executor)} · ${statusLabel(r.status)} · ${formatDateTime(r.finished_at ?? r.started_at)}` : 'Not run yet'

/** Lineage for one node of the ripple — what the reference opens when a node is clicked. */
export function buildNodeProvenance(node: RippleNode, ctx: NodeProvenanceContext): NodeProvenance {
  const { explanation: ex, scenario, baseline, sources, models } = ctx
  const metrics = new Map(ex.comparison.metrics.map((m) => [key(m.dataset_id, m.field), m]))
  const ref = node.ref

  if (ref.type === 'input') {
    const c = ex.changes.find((x) => x.dataset_id === ref.datasetId && x.field === ref.field)
    const dataset = sources.find((d) => d.id === ref.datasetId)
    const readers = ex.path.filter((s) => s.reads_changed.some((r) => r.dataset_id === ref.datasetId && r.field === ref.field)).map((s) => s.model_name)
    const d = node.delta
    return {
      title: node.name,
      subtitle: `Assumption · ${scenario.name}`,
      steps: [
        { key: 'Assumption', value: node.name, meta: c?.dataset_label },
        { key: 'Baseline → Scenario', value: c ? formatTransition(c.baseline_value, c.scenario_value, c.unit) : '—', meta: d && d.text !== 'Changed' ? d.text : undefined },
        {
          key: 'Source Of The Baseline Value',
          value: dataset?.current_source ? sourceLabel(dataset.current_source.source_type) : 'No Recorded Source',
          meta: dataset?.current_source ? formatDateTime(dataset.current_source.at) : undefined,
        },
        { key: 'Read By', value: readers.length ? readers.join(', ') : 'No Model On The Change Path', meta: readers.length ? `${readers.length} Model${readers.length === 1 ? '' : 's'} Read This Input` : undefined },
        { key: 'Scenario', value: scenario.name, meta: 'Applied in memory for the scenario run only. Never written to the dataset.' },
      ],
    }
  }

  if (ref.type === 'model') {
    const step = ex.path.find((s) => s.version_id === ref.versionId)
    const model = models.find((m) => m.version_id === ref.versionId)
    const outputs = (step?.outputs_changed ?? []).map((o) => {
      const m = metrics.get(key(o.dataset_id, o.field))
      return m ? `${o.field_label}: ${formatTransition(m.baseline, m.scenario, m.unit)}${m.relative_delta !== null ? ` (${formatPct(m.relative_delta)})` : ''}` : o.field_label
    })
    const steps: ProvStep[] = [
      { key: 'Model', value: node.name, meta: [versionLabel(model?.semver), model?.owner ? `Owner ${model.owner}` : ''].filter(Boolean).join(' · ') || undefined },
    ]
    if (model?.formula) steps.push({ key: 'Formula', value: model.formula })
    steps.push(
      { key: 'Reads (Changed)', value: step ? joinLabels(step.reads_changed.map((r) => r.field_label)) : '—', meta: step ? joinLabels([...new Set(step.reads_changed.map((r) => r.dataset_label))]) : undefined },
      { key: 'Results Changed', value: outputs.length ? `${outputs.length} Result${outputs.length === 1 ? '' : 's'}` : 'None', meta: outputs.join('\n') || undefined },
    )
    if (step && step.outputs_unchanged.length) steps.push({ key: 'Results Unchanged', value: joinLabels(step.outputs_unchanged.map((o) => o.field_label)) })
    steps.push({ key: 'Scenario Run', value: `Run ${shortId(scenario.run?.id)}`, meta: runMeta(scenario.run) })
    return { title: node.name, subtitle: `${node.kind === 'affected' ? 'Directly Affected' : 'Downstream'} Model · ${scenario.name}`, steps }
  }

  const m = metrics.get(key(ref.datasetId, ref.field))
  const producer = ex.path.find((s) => s.outputs_changed.some((o) => o.dataset_id === ref.datasetId && o.field === ref.field))
  const producerModel = producer ? models.find((x) => x.version_id === producer.version_id) : undefined
  return {
    title: node.name,
    subtitle: `Decision Impact · ${scenario.name}`,
    steps: [
      { key: 'Result', value: node.name, meta: m?.dataset_label },
      { key: 'Baseline → Scenario', value: m ? formatTransition(m.baseline, m.scenario, m.unit) : '—', meta: m && m.kind === 'numeric' ? [m.relative_delta !== null ? formatPct(m.relative_delta) : '', formatDelta(m.absolute_delta, m.unit)].filter(Boolean).join(' · ') : undefined },
      { key: 'Produced By', value: producer?.model_name ?? 'Recorded Result', meta: producer ? [versionLabel(producerModel?.semver), producerModel?.owner ? `Owner ${producerModel.owner}` : ''].filter(Boolean).join(' · ') || undefined : undefined },
      { key: 'Driven By', value: joinLabels(ex.changes.map((c) => c.field_label)) || 'None', meta: ex.changes.map((c) => `${c.field_label}: ${formatTransition(c.baseline_value, c.scenario_value, c.unit)}`).join('\n') || undefined },
      { key: 'Scenario Run', value: `Run ${shortId(scenario.run?.id)}`, meta: runMeta(scenario.run) },
      { key: 'Baseline Run', value: `Run ${shortId(baseline.run?.id)}`, meta: runMeta(baseline.run) },
    ],
  }
}
