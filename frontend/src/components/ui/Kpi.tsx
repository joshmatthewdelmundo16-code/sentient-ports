import type { ReactNode } from 'react'

/** KPI strip: modules separated by 1px rules (reference `.kpi-row`). Four across, 2 on tablets, 1 on phones. */
export function KpiRow({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`kpi-row ${className}`.trim()}>{children}</div>
}

export function Kpi({ label, value, tone, accent = false, badge, meta, children, testId }: {
  label: string
  value: ReactNode
  /** Colours the value. Direction colours are neutral by design, so there is no good/bad here. */
  tone?: 'accent' | 'info' | 'warn' | 'err' | 'good'
  accent?: boolean
  badge?: ReactNode
  meta?: ReactNode
  /** Optional footer (e.g. a progress or sparkline element). */
  children?: ReactNode
  testId?: string
}) {
  return (
    <div className={`kpi ${accent ? 'accent-val' : ''}`.trim()} data-testid={testId}>
      <div className="k-top">
        <span className="k-lab">{label}</span>
        {badge}
      </div>
      <div className={`k-val ${tone ?? ''}`.trim()}>{value}</div>
      {meta ? <div className="k-meta">{meta}</div> : null}
      {children ? <div className="k-spark">{children}</div> : null}
    </div>
  )
}
