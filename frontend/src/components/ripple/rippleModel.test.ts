import { describe, expect, it } from 'vitest'
import type { BaselineView, CatalogDataset, Explanation, MapModel, Metric, ScenarioView } from '../../api/types'
import { buildNodeProvenance, buildRipple } from './rippleModel'
import { describeSteps, runSummary } from './runStepsModel'

const metric = (o: Partial<Metric>): Metric => ({
  dataset_id: 'out', field: 'f', kind: 'numeric', baseline: 100, scenario: 110, unit: null, absolute_delta: 10, relative_delta: 0.1,
  baseline_zero: false, direction: 'increase', changed: true, dataset_label: 'Results', field_label: 'Result', terminal: true, field_order: 0, ...o,
})
const ref = (dataset_id: string, field: string, field_label: string) => ({ dataset_id, field, field_label, dataset_label: dataset_id })

// storm_days (source) → Access model (reads it directly) → access → Jobs model (reads access) → jobs
const explanation = {
  scenario: { id: 's1', name: 'Storm', run_id: 'r2' },
  baseline: { id: 'b1', name: 'Today', run_id: 'r1' },
  changes: [{ dataset_id: 'nat', dataset_label: 'Natural conditions', field: 'storm_days', field_label: 'Storm days', unit: 'days', baseline_value: 12, baseline_value_known: true, scenario_value: 30 }],
  path: [
    { version_id: 'v-access', model_name: 'Access model', reads_changed: [ref('nat', 'storm_days', 'Storm days')], outputs_changed: [ref('acc', 'availability', 'Access availability')], outputs_unchanged: [] },
    { version_id: 'v-jobs', model_name: 'Jobs model', reads_changed: [ref('acc', 'availability', 'Access availability')], outputs_changed: [ref('jobs', 'total', 'Total jobs'), ref('jobs', 'gva', 'Gross value added')], outputs_unchanged: [] },
  ],
  unaffected: [{ version_id: 'v-cost', model_name: 'Cost model', reason: '' }],
  comparison: {
    base_run_id: 'r1', target_run_id: 'r2', changed_count: 3,
    metrics: [
      metric({ dataset_id: 'acc', field: 'availability', field_label: 'Access availability', baseline: 0.98, scenario: 0.578, relative_delta: -0.41, absolute_delta: -0.402, direction: 'decrease', terminal: false, field_order: 1 }),
      metric({ dataset_id: 'jobs', field: 'total', field_label: 'Total jobs', baseline: 9000, scenario: 6244, relative_delta: -0.306, absolute_delta: -2756, direction: 'decrease', unit: 'jobs', field_order: 2 }),
      metric({ dataset_id: 'jobs', field: 'gva', field_label: 'Gross value added', baseline: 240, scenario: 166, relative_delta: -0.3, absolute_delta: -74, direction: 'decrease', field_order: 3 }),
      metric({ dataset_id: 'jobs', field: 'unchanged', field_label: 'Port calls', changed: false, direction: 'none', relative_delta: 0, absolute_delta: 0, field_order: 4 }),
    ],
  },
} as unknown as Explanation

const models = [
  { version_id: 'v-access', semver: '1.2.0', owner: 'Port ops', formula: 'availability = 1 - storm_days / 365' },
  { version_id: 'v-jobs', semver: 'v2.0.1', owner: 'Economics', formula: null },
] as unknown as MapModel[]

describe('buildRipple', () => {
  const stages = buildRipple(explanation, models)
  it('orders the stages Input → Affected Models → Downstream → Decision Impact', () => {
    expect(stages.map((s) => s.label)).toEqual(['Input', 'Affected Models', 'Downstream', 'Decision Impact'])
  })
  it('puts only the model that reads a changed source directly in "Affected"', () => {
    expect(stages[1]?.nodes.map((n) => n.name)).toEqual(['Access model'])
    expect(stages[2]?.nodes.map((n) => n.name)).toEqual(['Jobs model'])
  })
  it('never places the unaffected model on the path', () => {
    expect(stages.flatMap((s) => s.nodes).some((n) => n.name === 'Cost model')).toBe(false)
  })
  it('shows only changed headline results as impact, never unchanged ones', () => {
    expect(stages[3]?.nodes.map((n) => n.name)).toEqual(['Total jobs', 'Gross value added'])
  })
  it('states the input change as a percentage with the transition underneath', () => {
    const n = stages[0]?.nodes[0]
    expect(n?.delta).toMatchObject({ text: '+150.0%', cls: 'up' })
    expect(n?.delta?.label).toBe('12 → 30 days')
  })
  it('uses neutral direction classes (decrease = down, not "bad")', () => {
    expect(stages[3]?.nodes[0]?.delta?.cls).toBe('down')
    expect(stages.flatMap((s) => s.nodes).every((n) => !n.delta || ['up', 'down', 'flat'].includes(n.delta.cls))).toBe(true)
  })
  it('summarises a model by its first changed result, and counts the rest', () => {
    const jobs = stages[2]?.nodes[0]
    expect(jobs?.delta?.text).toBe('−30.6%')
    expect(jobs?.sub).toContain('+1 More Result')
    expect(jobs?.sub).toContain('Reads Access availability')
  })
  it('prefixes versions with a single "v"', () => {
    expect(stages[1]?.nodes[0]?.sub.startsWith('v1.2.0')).toBe(true)
    expect(stages[2]?.nodes[0]?.sub.startsWith('v2.0.1')).toBe(true)
  })
  it('drops empty stages instead of drawing blank ones', () => {
    const noImpact = { ...explanation, comparison: { ...explanation.comparison, metrics: [] } } as unknown as Explanation
    expect(buildRipple(noImpact, models).map((s) => s.kind)).toEqual(['input', 'affected', 'downstream'])
    const nothing = { ...explanation, path: [], comparison: { ...explanation.comparison, metrics: [] } } as unknown as Explanation
    expect(buildRipple(nothing, models).map((s) => s.kind)).toEqual(['input'])
  })
  it('gives every node a unique id', () => {
    const ids = stages.flatMap((s) => s.nodes.map((n) => n.id))
    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe('buildNodeProvenance', () => {
  const scenario = { id: 's1', name: 'Storm', run: { id: 'bbbbbbbb-1', status: 'succeeded', executor: 'in_process', finished_at: null, started_at: null }, overrides: [] } as unknown as ScenarioView
  const baseline = { id: 'b1', name: 'Today', run: { id: 'aaaaaaaa-1', status: 'succeeded', executor: 'in_process', finished_at: null, started_at: null } } as unknown as BaselineView
  const sources = [{ id: 'nat', label: 'Natural conditions', current_source: { source_type: 'excel', at: '2026-09-30T08:00:00Z' } }] as unknown as CatalogDataset[]
  const ctx = { explanation, scenario, baseline, sources, models }
  const stages = buildRipple(explanation, models)

  it('input node: names the source of the baseline value and who reads it', () => {
    const p = buildNodeProvenance(stages[0]!.nodes[0]!, ctx)
    expect(p.steps.map((s) => s.key)).toEqual(['Assumption', 'Baseline → Scenario', 'Source Of The Baseline Value', 'Read By', 'Scenario'])
    expect(p.steps[1]?.value).toBe('12 → 30 days')
    expect(p.steps[2]?.value).toBe('Excel workbook upload')
    expect(p.steps[3]?.value).toBe('Access model')
  })
  it('model node: shows version, owner and the real formula', () => {
    const p = buildNodeProvenance(stages[1]!.nodes[0]!, ctx)
    expect(p.steps[0]).toMatchObject({ key: 'Model', value: 'Access model', meta: 'v1.2.0 · Owner Port ops' })
    expect(p.steps[1]).toMatchObject({ key: 'Formula', value: 'availability = 1 - storm_days / 365' })
    expect(p.subtitle).toContain('Directly Affected')
  })
  it('model node without a formula omits that step rather than inventing one', () => {
    const p = buildNodeProvenance(stages[2]!.nodes[0]!, ctx)
    expect(p.steps.map((s) => s.key)).not.toContain('Formula')
    expect(p.steps.find((s) => s.key === 'Results Changed')?.meta).toContain('Total jobs: 9,000 → 6,244 jobs')
  })
  it('result node: names the model that produced it and the inputs that drove it', () => {
    const p = buildNodeProvenance(stages[3]!.nodes[0]!, ctx)
    expect(p.steps.find((s) => s.key === 'Produced By')?.value).toBe('Jobs model')
    expect(p.steps.find((s) => s.key === 'Driven By')?.value).toBe('Storm days')
    expect(p.steps.find((s) => s.key === 'Scenario Run')?.value).toBe('Run bbbbbbbb…')
  })
})

describe('describeSteps / runSummary', () => {
  const step = (id: string, order: number, status: string, extra: Record<string, unknown> = {}) => ({
    id, run_id: 'r', model_version_id: id, step_order: order, status, input_snapshot: null, error_message: null,
    started_at: '2026-10-01T12:00:00Z', finished_at: '2026-10-01T12:00:02Z', ...extra,
  })
  const name = (v: string | null) => `Model ${v}`
  it('orders steps by dependency order and labels what was recorded', () => {
    const v = describeSteps([step('b', 1, 'succeeded'), step('a', 0, 'succeeded')] as never, name)
    expect(v.map((x) => x.label)).toEqual(['Ran Model a', 'Ran Model b'])
    expect(v[0]?.duration).toBe('2.0 s')
  })
  it('reports a failure with its error and does not call the run complete', () => {
    const v = describeSteps([step('a', 0, 'succeeded'), step('b', 1, 'failed', { error_message: 'boom' })] as never, name)
    expect(v[1]).toMatchObject({ state: 'failed', error: 'boom', label: 'Model b Failed' })
    expect(runSummary(v)).toEqual({ text: 'Run Failed · Model b Failed', tone: 'err' })
  })
  it('only marks a step active when it was recorded as running', () => {
    const v = describeSteps([step('a', 0, 'running', { finished_at: null }), step('b', 1, 'requested', { finished_at: null })] as never, name)
    expect(v.map((x) => x.state)).toEqual(['active', 'queued'])
    expect(v.every((x) => x.duration === null)).toBe(true)
    expect(runSummary(v).text).toBe('Run In Progress')
  })
  it('summarises a clean run with the number of models recomputed', () => {
    expect(runSummary(describeSteps([step('a', 0, 'succeeded'), step('b', 1, 'succeeded')] as never, name))).toEqual({ text: 'Run Complete · 2 Models Recomputed', tone: 'good' })
    expect(runSummary(describeSteps([step('a', 0, 'succeeded')] as never, name)).text).toBe('Run Complete · 1 Model Recomputed')
  })
})
