/** Pure helpers for Scenario Comparison. Everything is derived from the recorded results of the
 *  two runs (and the scenario's own overrides); nothing is estimated. */
import type { Metric, OverrideView } from '../../api/types'

export interface IndexBar {
  key: string
  label: string
  unit: string | null
  baseline: number
  scenario: number
  /** Scenario as a percentage of the baseline (baseline = 100). */
  index: number
}

/** Headline numeric results that moved, indexed against their own baseline. A result whose
 *  baseline is zero (or not a positive number) has no meaningful index and is counted as skipped
 *  rather than drawn misleadingly. */
export function indexBars(metrics: Metric[]): { bars: IndexBar[]; skipped: number } {
  const moved = metrics.filter((m) => m.terminal && m.changed && m.kind === 'numeric').sort((a, b) => a.field_order - b.field_order)
  const bars: IndexBar[] = []
  let skipped = 0
  for (const m of moved) {
    if (typeof m.baseline !== 'number' || typeof m.scenario !== 'number' || !(m.baseline > 0) || m.scenario < 0) {
      skipped += 1
      continue
    }
    bars.push({ key: `${m.dataset_id}:${m.field}`, label: m.field_label, unit: m.unit, baseline: m.baseline, scenario: m.scenario, index: (m.scenario / m.baseline) * 100 })
  }
  return { bars, skipped }
}

export interface BarScale {
  /** Top of the axis, as an index (baseline = 100). Never below 125 so the baseline bar has headroom. */
  max: number
  /** Heights as a share of the plot (0–1). */
  baseline: number
  heights: { key: string; scenario: number }[]
}

export function barScale(bars: IndexBar[]): BarScale {
  const peak = Math.max(100, ...bars.map((b) => b.index))
  const max = Math.max(125, Math.ceil((peak * 1.1) / 25) * 25)
  return { max, baseline: 100 / max, heights: bars.map((b) => ({ key: b.key, scenario: b.index / max })) }
}

export function groupMetrics(metrics: Metric[], showAll: boolean): [string, Metric[]][] {
  const shown = showAll ? metrics : metrics.filter((m) => m.changed)
  const groups = new Map<string, Metric[]>()
  for (const m of shown) groups.set(m.dataset_label, [...(groups.get(m.dataset_label) ?? []), m])
  return [...groups.entries()]
}

/** Relative change of an override against the value it replaces, when both are positive-baseline numbers. */
export function overrideChange(o: OverrideView): { rel: number | null; known: boolean } {
  const b = o.baseline_value
  const s = o.value
  if (!o.baseline_value_known || typeof b !== 'number' || typeof s !== 'number' || b === 0) return { rel: null, known: o.baseline_value_known }
  return { rel: (s - b) / b, known: true }
}
