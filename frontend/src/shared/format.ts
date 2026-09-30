/**
 * Number, unit, date and status formatting — the one place raw values become text.
 *
 * Units come from data contracts ("USD/year", "USD/t", "TEU/year", "%", "tCO2/year"), so
 * formatting is driven by the unit the platform recorded, never by which screen is showing
 * the value.
 *
 *   formatValue(12_000_000, 'USD/year')   → "$12.0M"
 *   formatValue(600, 'USD/t')             → "$600.00 / t"
 *   formatValue(20_000_000, 'TEU/year')   → "20.0M TEU"
 *   formatTransition(2, 2.5, 'USD/t')     → "$2.00 → $2.50 / t"
 */
import type { Scalar } from '../api/types'

const compact = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})
const grouped0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const grouped1 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 })
const grouped3 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 })
const fixed2 = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const sig3 = new Intl.NumberFormat('en-US', { maximumSignificantDigits: 3 })

export function formatNumber(n: number): string {
  const a = Math.abs(n)
  if (a >= 100_000) return compact.format(n)
  if (a >= 1_000) return grouped0.format(n)
  if (a >= 100) return grouped1.format(n)
  if (a >= 1 || a === 0) return grouped3.format(n)
  return sig3.format(n)
}

interface UnitParts {
  money: boolean
  percent: boolean
  base: string // "TEU", "tCO₂", "" for money
  per: string | null // denominator shown after the value ("t", "TEU", "call"); never "year"
}

function parseUnit(unit: string | null | undefined): UnitParts | null {
  if (!unit || unit === 'bool') return null
  const u = unit.replace(/CO2/g, 'CO₂').trim()
  if (u === '%') return { money: false, percent: true, base: '', per: null }
  const i = u.indexOf('/')
  const num = (i < 0 ? u : u.slice(0, i)).trim()
  let per = i < 0 ? null : u.slice(i + 1).trim() || null
  if (per === 'year') per = null
  return { money: num === 'USD', percent: false, base: num === 'USD' ? '' : num, per }
}

function money(n: number): string {
  const a = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (a >= 100_000) return `${sign}$${compact.format(a)}`
  if (a >= 1_000) return `${sign}$${grouped0.format(a)}`
  return `${sign}$${fixed2.format(a)}`
}

/** The number alone, styled for its unit ($ prefix, % suffix) but without the base unit. */
function bare(n: number, parts: UnitParts | null): string {
  if (!parts) return formatNumber(n)
  if (parts.percent) return `${(Math.round(n * 10) / 10).toFixed(1)}%`
  if (parts.money) return money(n)
  return formatNumber(n)
}

function suffix(parts: UnitParts | null): string {
  if (!parts || parts.percent) return ''
  const base = parts.base ? ` ${parts.base}` : ''
  const per = parts.per ? ` / ${parts.per}` : ''
  return `${base}${per}`
}

export function prettyUnit(unit: string | null | undefined): string {
  if (!unit) return ''
  return unit.replace(/CO2/g, 'CO₂').replace(/\s*\/\s*/g, ' / ')
}

function scalarText(value: Scalar | undefined): string | null {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'string') return value
  if (!Number.isFinite(value)) return '—'
  return null
}

export function formatValue(value: Scalar | undefined, unit?: string | null): string {
  const text = scalarText(value)
  if (text !== null) return text
  const parts = parseUnit(unit)
  return `${bare(value as number, parts)}${suffix(parts)}`
}

/** "$12.0M → $15.0M", "$2.00 → $2.50 / t", "20.0M → 21.0M TEU" — the unit is stated once. */
export function formatTransition(from: Scalar | undefined, to: Scalar | undefined, unit?: string | null): string {
  const a = scalarText(from)
  const b = scalarText(to)
  const parts = parseUnit(unit)
  const left = a ?? bare(from as number, parts)
  const right = b ?? bare(to as number, parts)
  const tail = b === null ? suffix(parts) : ''
  return `${left} → ${right}${tail}`
}

export function formatPct(rel: number | null | undefined, digits = 1): string {
  if (rel === null || rel === undefined || !Number.isFinite(rel)) return '—'
  const pct = rel * 100
  const sign = pct > 0 ? '+' : pct < 0 ? '−' : ''
  return `${sign}${Math.abs(pct).toFixed(digits)}%`
}

export function formatDelta(abs: number | null | undefined, unit?: string | null): string {
  if (abs === null || abs === undefined || !Number.isFinite(abs)) return '—'
  if (abs === 0) return 'No change'
  const sign = abs > 0 ? '+' : '−'
  return `${sign}${formatValue(Math.abs(abs), unit)}`
}

const dateFmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
const dateFmtYear = new Intl.DateTimeFormat('en-US', {
  month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
})

/** Server timestamps are UTC; naive ones (older SQLite rows) are treated as UTC too. */
export function parseServerTime(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const hasZone = /[zZ]$|[+-]\d\d:?\d\d$/.test(iso)
  const d = new Date(hasZone ? iso : `${iso}Z`)
  return Number.isNaN(d.getTime()) ? null : d
}

export function formatDateTime(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parseServerTime(iso)
  if (!d) return '—'
  return (d.getFullYear() === now.getFullYear() ? dateFmt : dateFmtYear).format(d)
}

const STATUS: Record<string, string> = {
  succeeded: 'Succeeded',
  failed: 'Failed',
  running: 'Running',
  requested: 'Queued',
  submitted: 'Submitted',
  pending: 'Pending',
  skipped: 'Skipped',
  validating: 'Validating',
  ingested: 'Committed',
  unchanged: 'No change',
  rejected: 'Rejected',
  recorded: 'Recorded',
  executed: 'Executed',
  draft: 'Draft',
  active: 'Active',
  inactive: 'Inactive',
  revoked: 'Revoked',
  expired: 'Expired',
  archived: 'Archived',
  open: 'Open',
  closed: 'Closed',
  denied: 'Denied',
  granted: 'Granted',
  success: 'Succeeded',
  failure: 'Failed',
}

export function statusLabel(status: string | null | undefined): string {
  if (!status) return 'Unknown'
  return STATUS[status] ?? sentenceCase(status.replace(/_/g, ' '))
}

export type Tone = 'success' | 'danger' | 'warning' | 'info' | 'neutral'

export function statusTone(status: string | null | undefined): Tone {
  switch (status) {
    case 'succeeded':
    case 'ingested':
    case 'executed':
    case 'active':
    case 'granted':
    case 'success':
      return 'success'
    case 'failed':
    case 'rejected':
    case 'denied':
    case 'failure':
      return 'danger'
    case 'running':
    case 'requested':
    case 'submitted':
    case 'pending':
      return 'info'
    case 'revoked':
    case 'expired':
    case 'skipped':
      return 'warning'
    default:
      return 'neutral'
  }
}

const SOURCES: Record<string, string> = {
  excel: 'Excel workbook upload',
  seed: 'Initial demo values',
  external: 'Direct update',
  api: 'Direct update',
  model_output: 'Model output',
  network_share: 'Governed network share',
  csv: 'CSV upload',
  rest: 'REST poll',
  webhook: 'Webhook',
  telemetry: 'Telemetry feed',
  simulated: 'Simulated feed',
  connector: 'Connector',
}

export function sourceLabel(sourceType: string | null | undefined): string {
  if (!sourceType) return 'Unknown source'
  return SOURCES[sourceType] ?? sentenceCase(sourceType.replace(/_/g, ' '))
}

/** "Decision Overview" → "Decision overview". Leaves acronyms such as TEU alone. */
export function sentenceCase(text: string): string {
  const words = text.trim().split(/\s+/)
  return words
    .map((w, i) => {
      if (/^[A-Z0-9₂]{2,}$/.test(w)) return w
      const lower = w.toLowerCase()
      return i === 0 ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower
    })
    .join(' ')
}

export function shortId(id: string | null | undefined): string {
  return id ? `${id.slice(0, 8)}…` : '—'
}

export function durationMs(start: string | null | undefined, end: string | null | undefined): number | null {
  const a = parseServerTime(start)
  const b = parseServerTime(end)
  if (!a || !b) return null
  return b.getTime() - a.getTime()
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return '—'
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`
  return `${(ms / 1000).toFixed(1)} s`
}
