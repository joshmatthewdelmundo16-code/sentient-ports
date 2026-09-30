import { describe, expect, it } from 'vitest'
import {
  formatDateTime,
  formatDelta,
  formatPct,
  formatTransition,
  formatValue,
  sentenceCase,
  statusLabel,
} from './format'

describe('formatValue', () => {
  it('formats annual money compactly', () => {
    expect(formatValue(12_000_000, 'USD/year')).toBe('$12.0M')
    expect(formatValue(15_000_000, 'USD/year')).toBe('$15.0M')
  })
  it('formats unit prices with the denominator', () => {
    expect(formatValue(600, 'USD/t')).toBe('$600.00 / t')
    expect(formatValue(0.6, 'USD/TEU')).toBe('$0.60 / TEU')
  })
  it('formats annual quantities without repeating "/ year"', () => {
    expect(formatValue(20_000_000, 'TEU/year')).toBe('20.0M TEU')
    expect(formatValue(62_280, 'tCO2/year')).toBe('62,280 tCO₂')
    expect(formatValue(10_000, 'calls/year')).toBe('10,000 calls')
  })
  it('formats percentages and booleans', () => {
    expect(formatValue(38.0517, '%')).toBe('38.1%')
    expect(formatValue(true, 'bool')).toBe('Yes')
    expect(formatValue(null, 'USD/t')).toBe('—')
  })
  it('keeps small intensities readable', () => {
    expect(formatValue(0.003114, 'tCO2/TEU')).toBe('0.00311 tCO₂ / TEU')
  })
})

describe('transitions and deltas', () => {
  it('matches the activity format from the specification', () => {
    expect(formatTransition(12_000_000, 15_000_000, 'USD/year')).toBe('$12.0M → $15.0M')
    expect(formatTransition(2, 2.5, 'USD/t')).toBe('$2.00 → $2.50 / t')
    expect(formatTransition(20_000_000, 21_000_000, 'TEU/year')).toBe('20.0M → 21.0M TEU')
  })
  it('signs deltas and percentages', () => {
    expect(formatDelta(3_000_000, 'USD/year')).toBe('+$3.0M')
    expect(formatDelta(-150, 'USD/t')).toBe('−$150.00 / t')
    expect(formatPct(0.25)).toBe('+25.0%')
    expect(formatPct(-0.1)).toBe('−10.0%')
    expect(formatPct(null)).toBe('—')
  })
})

describe('language', () => {
  it('uses sentence case for statuses', () => {
    expect(statusLabel('succeeded')).toBe('Succeeded')
    expect(statusLabel('ingested')).toBe('Committed')
    expect(statusLabel('migrations_pending')).toBe('Migrations pending')
  })
  it('sentence-cases while preserving acronyms', () => {
    expect(sentenceCase('Decision Overview')).toBe('Decision overview')
    expect(sentenceCase('Annual TEU Forecast')).toBe('Annual TEU forecast')
  })
})

describe('dates', () => {
  it('treats naive server timestamps as UTC', () => {
    const now = new Date('2026-09-30T12:00:00Z')
    const a = formatDateTime('2026-09-29T21:51:00', now)
    const b = formatDateTime('2026-09-29T21:51:00+00:00', now)
    expect(a).toBe(b)
    expect(a).toMatch(/^Sep \d+, \d+:\d\d (AM|PM)$/)
  })
})
