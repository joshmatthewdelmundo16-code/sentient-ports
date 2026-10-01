import { describe, expect, it } from 'vitest'
import type { Metric, OverrideView } from '../../api/types'
import { barScale, groupMetrics, indexBars, overrideChange } from './scenarioModel'

const m = (o: Partial<Metric>): Metric => ({
  dataset_id: 'd', field: 'f', kind: 'numeric', baseline: 100, scenario: 110, unit: null, absolute_delta: 10, relative_delta: 0.1,
  baseline_zero: false, direction: 'increase', changed: true, dataset_label: 'Results', field_label: 'F', terminal: true, field_order: 0, ...o,
})

describe('indexBars', () => {
  it('indexes each moved headline result against its own baseline', () => {
    const { bars } = indexBars([m({ field: 'a', baseline: 200, scenario: 100 }), m({ field: 'b', baseline: 50, scenario: 75 })])
    expect(bars.map((b) => Math.round(b.index))).toEqual([50, 150])
  })
  it('keeps contract order', () => {
    const { bars } = indexBars([m({ field: 'b', field_order: 2 }), m({ field: 'a', field_order: 1 })])
    expect(bars.map((b) => b.key)).toEqual(['d:a', 'd:b'])
  })
  it('ignores unchanged, non-headline and non-numeric results', () => {
    const { bars, skipped } = indexBars([m({ changed: false }), m({ terminal: false }), m({ kind: 'boolean' })])
    expect(bars).toHaveLength(0)
    expect(skipped).toBe(0)
  })
  it('skips a zero baseline instead of drawing a misleading bar, and says so', () => {
    const { bars, skipped } = indexBars([m({ baseline: 0, scenario: 5 }), m({ field: 'ok' })])
    expect(bars.map((b) => b.key)).toEqual(['d:ok'])
    expect(skipped).toBe(1)
  })
  it('skips non-numeric values and negative scenarios', () => {
    expect(indexBars([m({ baseline: null }), m({ scenario: -3 })]).skipped).toBe(2)
  })
  it('draws a result that fell to zero as an empty bar (index 0), not a skip', () => {
    const { bars, skipped } = indexBars([m({ baseline: 100, scenario: 0 })])
    expect(bars[0]?.index).toBe(0)
    expect(skipped).toBe(0)
  })
})

describe('barScale', () => {
  it('gives the baseline headroom even when nothing exceeds it', () => {
    const s = barScale(indexBars([m({ baseline: 100, scenario: 60 })]).bars)
    expect(s.max).toBe(125)
    expect(s.baseline).toBeCloseTo(0.8)
    expect(s.heights[0]?.scenario).toBeCloseTo(0.48)
  })
  it('stretches the axis for large increases so no bar overflows', () => {
    const bars = indexBars([m({ baseline: 10, scenario: 40 })]).bars
    const s = barScale(bars)
    expect(s.max).toBeGreaterThanOrEqual(400)
    expect(s.heights[0]?.scenario).toBeLessThanOrEqual(1)
    expect(s.baseline).toBeLessThan(0.5)
  })
  it('handles an empty chart', () => {
    expect(barScale([])).toMatchObject({ max: 125, heights: [] })
  })
})

describe('groupMetrics', () => {
  const list = [m({ dataset_label: 'A', field: '1' }), m({ dataset_label: 'B', field: '2', changed: false }), m({ dataset_label: 'A', field: '3' })]
  it('shows only changed metrics by default, grouped by dataset in first-seen order', () => {
    const g = groupMetrics(list, false)
    expect(g.map(([k, v]) => [k, v.map((x) => x.field)])).toEqual([['A', ['1', '3']]])
  })
  it('shows everything when asked', () => {
    expect(groupMetrics(list, true).map(([k]) => k)).toEqual(['A', 'B'])
  })
})

describe('overrideChange', () => {
  const o = (b: unknown, v: unknown, known = true) => ({ baseline_value: b, value: v, baseline_value_known: known }) as unknown as OverrideView
  it('computes the relative change against the replaced value', () => {
    expect(overrideChange(o(12, 30)).rel).toBeCloseTo(1.5)
    expect(overrideChange(o(10, 5)).rel).toBeCloseTo(-0.5)
  })
  it('is unknown when there is no baseline value, a zero baseline, or non-numbers', () => {
    expect(overrideChange(o(null, 5, false))).toEqual({ rel: null, known: false })
    expect(overrideChange(o(0, 5)).rel).toBeNull()
    expect(overrideChange(o('x', 5)).rel).toBeNull()
  })
})
