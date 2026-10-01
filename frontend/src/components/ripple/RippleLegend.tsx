import type { ReactNode } from 'react'

export interface LegendItem {
  label: string
  /** CSS colour (a token). */
  color: string
}

/** Colour key for the ripple and the dependency graph (reference `.ripple-legend`). */
export function RippleLegend({ items, hint }: { items: LegendItem[]; hint?: ReactNode }) {
  return (
    <div className="ripple-legend">
      {items.map((i) => (
        <span key={i.label} className="ll"><i style={{ background: i.color }} /> {i.label}</span>
      ))}
      {hint ? <span className="muted hint-r">{hint}</span> : null}
    </div>
  )
}

export const RIPPLE_LEGEND: LegendItem[] = [
  { label: 'Input / Assumption', color: 'var(--warning)' },
  { label: 'Directly Affected', color: 'var(--accent)' },
  { label: 'Downstream', color: 'var(--info)' },
  { label: 'Decision Impact', color: 'var(--accent-strong)' },
]
