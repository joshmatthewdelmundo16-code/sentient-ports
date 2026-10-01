import { describe, expect, it } from 'vitest'
import type { BaselineView, CatalogDataset, Explanation, ScenarioView } from '../../api/types'
import { deltaClass, directionPill } from '../../components/data/delta'
import { executorLabel } from '../../shared/format'
import { buildProvenance } from './provenance'

const run = (id: string) => ({ id, status: 'succeeded', executor: 'in_process', finished_at: '2026-10-01T12:00:00Z', started_at: '2026-10-01T11:59:00Z' })

const baseline = { id: 'b1', name: 'Port city today', run: run('aaaaaaaa-1111') } as unknown as BaselineView
const scenario = {
  id: 's1', name: 'Storm season', run: run('bbbbbbbb-2222'),
  overrides: [{ id: 'o1', dataset_id: 'd1', dataset_label: 'Natural conditions', field: 'storm_days', field_label: 'Storm days', unit: 'days', value: 30, baseline_value: 12, baseline_value_known: true }],
} as unknown as ScenarioView
const explanation = {
  path: [{ version_id: 'v1', model_name: 'Access model', reads_changed: [], outputs_changed: [], outputs_unchanged: [] }],
  unaffected: [{ version_id: 'v2', model_name: 'Cost model', reason: '' }],
} as unknown as Explanation
const sources = [{ id: 'd1', label: 'Natural conditions', current_source: { source_type: 'excel', at: '2026-09-30T08:00:00Z' } }] as unknown as CatalogDataset[]

describe('buildProvenance', () => {
  const steps = buildProvenance({ scenario, baseline, explanation, sources })
  it('walks result → models → inputs → sources → runs, in that order', () => {
    expect(steps.map((s) => s.key)).toEqual(['Result', 'Models On The Change Path', 'Changed Inputs', 'Data Sources', 'Scenario Run', 'Baseline Run'])
  })
  it('names the scenario and what it was compared with', () => {
    expect(steps[0]).toMatchObject({ value: 'Storm season · Scenario Results', meta: 'Compared With Port city today' })
  })
  it('counts models on and off the change path', () => {
    expect(steps[1]).toMatchObject({ value: 'Access model', meta: '1 On The Path · 1 Unaffected' })
  })
  it('states each changed input as baseline → scenario', () => {
    expect(steps[2]?.value).toBe('Storm days')
    expect(steps[2]?.meta).toContain('Storm days: 12 → 30 days')
  })
  it('reports where each source value came from', () => {
    expect(steps[3]?.value).toBe('1 Source Dataset')
    expect(steps[3]?.meta).toContain('Natural conditions: Excel workbook upload')
  })
  it('identifies both runs by short id and executor', () => {
    expect(steps[4]?.value).toBe('Run bbbbbbbb…')
    expect(steps[4]?.meta).toContain('In-Process Executor · Succeeded')
    expect(steps[5]?.value).toBe('Run aaaaaaaa…')
  })
  it('degrades honestly when records are missing', () => {
    const s = buildProvenance({
      scenario: { ...scenario, run: null, overrides: [] } as unknown as ScenarioView,
      baseline: { ...baseline, run: null } as unknown as BaselineView,
      explanation: { path: [], unaffected: [] } as unknown as Explanation,
      sources: [],
    })
    expect(s[1]?.value).toBe('No Model Is Affected')
    expect(s[2]?.value).toBe('None')
    expect(s[3]?.value).toBe('None Recorded')
    expect(s[4]).toMatchObject({ value: 'Run —', meta: 'Not run yet' })
  })
})

describe('direction helpers', () => {
  it('uses neutral classes — no good/bad semantics', () => {
    expect(deltaClass('increase')).toBe('up')
    expect(deltaClass('decrease')).toBe('down')
    expect(deltaClass('none')).toBe('flat')
    expect(deltaClass(null)).toBe('flat')
  })
  it('labels what happened without judging it', () => {
    expect(directionPill('increase', true, true)).toEqual({ tone: 'info', label: 'Increased' })
    expect(directionPill('decrease', true, true)).toEqual({ tone: 'accent', label: 'Decreased' })
    expect(directionPill(null, true, false)).toEqual({ tone: 'info', label: 'Changed' })
    expect(directionPill('none', false, true)).toEqual({ tone: 'neutral', label: 'No Change' })
    for (const tone of ['info', 'accent', 'neutral']) expect(['good', 'warn', 'err']).not.toContain(tone)
  })
})

describe('executorLabel', () => {
  it('names the executors in Title Case', () => {
    expect(executorLabel('in_process')).toBe('In-Process Executor')
    expect(executorLabel('airflow')).toBe('Airflow (External Executor)')
    expect(executorLabel('dagster')).toBe('Dagster (External Executor)')
    expect(executorLabel(null)).toBe('Unknown Executor')
  })
})
