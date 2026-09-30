/** Small SVG charts. Hand-rolled (no charting dependency): each is < 150 lines and draws
 *  only what the product needs — relative change bars, time series, and trade-off scatter. */
import type { ReactNode } from 'react'
import { formatNumber, formatPct, formatValue } from './format'

export interface ChangeBar {
  key: string
  label: string
  sub?: string
  relative: number | null
  detail?: string
}

/** Diverging bars of relative change, centred on zero. Direction colours are neutral. */
export function ChangeBars({ bars, title = 'Relative change' }: { bars: ChangeBar[]; title?: string }) {
  const rowH = 34
  const labelW = 300
  const valueW = 110
  const plotW = 700
  const width = labelW + plotW + valueW
  const height = Math.max(1, bars.length) * rowH + 24
  const maxAbs = Math.max(0.01, ...bars.map((b) => Math.abs(b.relative ?? 0)))
  const scale = (v: number) => (v / maxAbs) * (plotW / 2 - 8)
  const cx = labelW + plotW / 2
  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
      <line x1={cx} x2={cx} y1={4} y2={height - 18} stroke="var(--line-strong)" />
      {bars.map((b, i) => {
        const y = 8 + i * rowH
        const rel = b.relative ?? 0
        const w = Math.abs(scale(rel))
        const x = rel >= 0 ? cx : cx - w
        return (
          <g key={b.key}>
            <text x={0} y={y + 13} style={{ fill: 'var(--ink-900)', fontSize: 12 }}>{b.label}</text>
            {b.sub ? <text x={0} y={y + 26} style={{ fontSize: 10.5 }}>{b.sub}</text> : null}
            <rect x={x} y={y + 5} width={Math.max(w, rel === 0 ? 0 : 2)} height={16} rx={3}
              fill={rel > 0 ? 'var(--up)' : 'var(--down)'} opacity={0.85}>
              {b.detail ? <title>{b.detail}</title> : null}
            </rect>
            <text x={width} y={y + 17} textAnchor="end" style={{ fill: 'var(--ink-700)', fontWeight: 600 }}>
              {b.relative === null ? 'n/a' : formatPct(b.relative)}
            </text>
          </g>
        )
      })}
      <text x={cx} y={height - 4} textAnchor="middle">0%</text>
    </svg>
  )
}

export interface Series {
  key: string
  label: string
  color: string
  values: (number | null)[]
  dashed?: boolean
}

function niceTicks(min: number, max: number, count = 5): number[] {
  if (min === max) return [min]
  const span = max - min
  const step0 = span / count
  const mag = Math.pow(10, Math.floor(Math.log10(step0)))
  const norm = step0 / mag
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag
  const start = Math.floor(min / step) * step
  const ticks: number[] = []
  for (let v = start; v <= max + step * 0.5; v += step) ticks.push(Number(v.toFixed(10)))
  return ticks
}

/** Multi-series line chart over discrete x labels (e.g. planning years). */
export function LineChart({ x, series, unit, height = 260, markers, title }: {
  x: (string | number)[]
  series: Series[]
  unit?: string | null
  height?: number
  markers?: { index: number; label: string }[]
  title: string
}) {
  const width = 720
  const pad = { l: 64, r: 16, t: 14, b: 30 }
  const vals = series.flatMap((s) => s.values.filter((v): v is number => v !== null && Number.isFinite(v)))
  const lo = Math.min(0, ...vals)
  const hi = Math.max(1, ...vals)
  const ticks = niceTicks(lo, hi)
  const yMax = ticks[ticks.length - 1] ?? hi
  const yMin = ticks[0] ?? lo
  const px = (i: number) => pad.l + (x.length <= 1 ? 0 : (i / (x.length - 1)) * (width - pad.l - pad.r))
  const py = (v: number) => pad.t + (1 - (v - yMin) / (yMax - yMin || 1)) * (height - pad.t - pad.b)
  return (
    <figure style={{ margin: 0 }}>
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        <g className="grid">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={width - pad.r} y1={py(t)} y2={py(t)} />
              <text x={pad.l - 8} y={py(t) + 4} textAnchor="end">
                {unit ? formatValue(t, unit).replace(/ \/ .*/, '') : formatNumber(t)}
              </text>
            </g>
          ))}
        </g>
        {x.map((label, i) => (
          <text key={String(label)} x={px(i)} y={height - 10} textAnchor="middle">{label}</text>
        ))}
        {markers?.map((m) => (
          <g key={`${m.index}-${m.label}`}>
            <line x1={px(m.index)} x2={px(m.index)} y1={pad.t} y2={height - pad.b} stroke="var(--zone-private)" strokeDasharray="3 3" />
            <text x={px(m.index) + 4} y={pad.t + 10} style={{ fill: 'var(--zone-private)', fontSize: 10.5 }}>{m.label}</text>
          </g>
        ))}
        {series.map((s) => {
          const pts = s.values
            .map((v, i) => (v === null || !Number.isFinite(v) ? null : `${px(i)},${py(v)}`))
            .filter(Boolean)
          return (
            <g key={s.key}>
              <polyline points={pts.join(' ')} fill="none" stroke={s.color} strokeWidth={2.2}
                strokeDasharray={s.dashed ? '6 4' : undefined} />
              {s.values.map((v, i) =>
                v === null || !Number.isFinite(v) ? null : (
                  <circle key={i} cx={px(i)} cy={py(v)} r={3} fill={s.color}>
                    <title>{`${s.label} · ${x[i]}: ${formatValue(v, unit)}`}</title>
                  </circle>
                ),
              )}
            </g>
          )
        })}
      </svg>
      <figcaption className="legend">
        {series.map((s) => (
          <span key={s.key}>
            <span className="legend-swatch" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </figcaption>
    </figure>
  )
}

export interface Point {
  key: string
  x: number
  y: number
  feasible: boolean
  highlight?: boolean
  onFront?: boolean
  label: string
}

/** Trade-off scatter (e.g. cost vs waiting time). Infeasible points are hollow. */
export function ScatterChart({ points, xLabel, yLabel, xUnit, yUnit, title, footer }: {
  points: Point[]
  xLabel: string
  yLabel: string
  xUnit?: string | null
  yUnit?: string | null
  title: string
  footer?: ReactNode
}) {
  const width = 720
  const height = 320
  const pad = { l: 72, r: 16, t: 14, b: 40 }
  const xs = points.map((p) => p.x)
  const ys = points.map((p) => p.y)
  const xt = niceTicks(Math.min(...xs, 0), Math.max(...xs, 1))
  const yt = niceTicks(Math.min(...ys, 0), Math.max(...ys, 1))
  const x0 = xt[0] ?? 0
  const x1 = xt[xt.length - 1] ?? 1
  const y0 = yt[0] ?? 0
  const y1 = yt[yt.length - 1] ?? 1
  const px = (v: number) => pad.l + ((v - x0) / (x1 - x0 || 1)) * (width - pad.l - pad.r)
  const py = (v: number) => pad.t + (1 - (v - y0) / (y1 - y0 || 1)) * (height - pad.t - pad.b)
  const front = points.filter((p) => p.onFront).sort((a, b) => a.x - b.x)
  return (
    <figure style={{ margin: 0 }}>
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        <g className="grid">
          {yt.map((t) => (
            <g key={`y${t}`}>
              <line x1={pad.l} x2={width - pad.r} y1={py(t)} y2={py(t)} />
              <text x={pad.l - 8} y={py(t) + 4} textAnchor="end">{formatValue(t, yUnit).replace(/ \/ .*/, '')}</text>
            </g>
          ))}
          {xt.map((t) => (
            <text key={`x${t}`} x={px(t)} y={height - 22} textAnchor="middle">{formatValue(t, xUnit).replace(/ \/ .*/, '')}</text>
          ))}
        </g>
        <text x={(pad.l + width - pad.r) / 2} y={height - 4} textAnchor="middle" style={{ fill: 'var(--ink-700)' }}>{xLabel}</text>
        <text x={14} y={pad.t + 4} style={{ fill: 'var(--ink-700)' }}>{yLabel}</text>
        {front.length > 1 ? (
          <polyline points={front.map((p) => `${px(p.x)},${py(p.y)}`).join(' ')} fill="none"
            stroke="var(--zone-shared)" strokeWidth={1.6} strokeDasharray="4 3" />
        ) : null}
        {points.map((p) => (
          <circle key={p.key} cx={px(p.x)} cy={py(p.y)} r={p.highlight ? 7 : 4.2}
            fill={p.feasible ? (p.onFront ? 'var(--zone-shared)' : 'var(--accent)') : 'none'}
            stroke={p.highlight ? 'var(--ink-900)' : p.feasible ? 'none' : 'var(--ink-300)'}
            strokeWidth={p.highlight ? 2 : 1.2} opacity={p.feasible ? 0.9 : 0.8}>
            <title>{p.label}</title>
          </circle>
        ))}
      </svg>
      {footer ? <figcaption className="legend">{footer}</figcaption> : null}
    </figure>
  )
}
