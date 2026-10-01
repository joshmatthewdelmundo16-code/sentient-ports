import type { ProvStep } from '../../components/data/ProvenanceSteps'
import type { BaselineView, CatalogDataset, Explanation, ScenarioView } from '../../api/types'
import { executorLabel, formatDateTime, formatTransition, shortId, sourceLabel, statusLabel } from '../../shared/format'

/** The lineage of the scenario result on screen, built only from records the platform holds:
 *  result → models on the change path → changed inputs → data sources → the two runs. */
export function buildProvenance({ scenario, baseline, explanation, sources }: {
  scenario: ScenarioView
  baseline: BaselineView
  explanation: Explanation
  sources: CatalogDataset[]
}): ProvStep[] {
  const models = explanation.path.map((p) => p.model_name)
  const sourceLines = sources.map((d) =>
    d.current_source ? `${d.label}: ${sourceLabel(d.current_source.source_type)} · ${formatDateTime(d.current_source.at)}` : `${d.label}: No recorded source`,
  )
  const runLine = (r: ScenarioView['run']) =>
    r ? `${executorLabel(r.executor)} · ${statusLabel(r.status)} · ${formatDateTime(r.finished_at ?? r.started_at)}` : 'Not run yet'

  return [
    { key: 'Result', value: `${scenario.name} · Scenario Results`, meta: `Compared With ${baseline.name}` },
    {
      key: 'Models On The Change Path',
      value: models.length ? models.join(', ') : 'No Model Is Affected',
      meta: `${explanation.path.length} On The Path · ${explanation.unaffected.length} Unaffected`,
    },
    {
      key: 'Changed Inputs',
      value: scenario.overrides.length ? scenario.overrides.map((o) => o.field_label).join(', ') : 'None',
      meta: scenario.overrides.map((o) => `${o.field_label}: ${formatTransition(o.baseline_value, o.value, o.unit)}`).join('\n') || undefined,
    },
    { key: 'Data Sources', value: sources.length ? `${sources.length} Source Dataset${sources.length === 1 ? '' : 's'}` : 'None Recorded', meta: sourceLines.join('\n') || undefined },
    { key: 'Scenario Run', value: `Run ${shortId(scenario.run?.id)}`, meta: runLine(scenario.run) },
    { key: 'Baseline Run', value: `Run ${shortId(baseline.run?.id)}`, meta: runLine(baseline.run) },
  ]
}
