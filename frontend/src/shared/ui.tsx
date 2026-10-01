/** Shared presentational components. No data fetching here — features pass data in.
 *  The look comes from the reference design system (components/ui, styles/primitives.css);
 *  this module keeps the API the feature pages already use. */
import { useId, useState, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import { Button } from '../components/ui/Button'
import { Crane } from '../components/ui/Crane'
import { Icon } from '../components/ui/Icon'
import { Pill, type PillTone } from '../components/ui/Pill'
import { statusLabel, statusTone, type Tone } from './format'

export function PageHeader({ title, description, actions }: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="phead">
      <div>
        <h1 className="ptitle">{title}</h1>
        {description ? <p className="psub">{description}</p> : null}
      </div>
      {actions ? <div className="phead-actions">{actions}</div> : null}
    </header>
  )
}

export function Card({ title, subtitle, actions, children, className = '', tight = false, flush = false, id }: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children?: ReactNode
  className?: string
  tight?: boolean
  /** No body padding — for tables that run edge to edge. */
  flush?: boolean
  id?: string
}) {
  return (
    <section className={`panel ${className}`.trim()} id={id}>
      {title || actions ? (
        <div className="panel-head">
          <div>
            {title ? <h2 className="ptt">{title}</h2> : null}
            {subtitle ? <div className="psubtt">{subtitle}</div> : null}
          </div>
          {actions ? <div className="panel-actions">{actions}</div> : null}
        </div>
      ) : null}
      <div className={`panel-body ${tight ? 'tight' : ''} ${flush ? 'flush' : ''}`.trim()}>{children}</div>
    </section>
  )
}

type BadgeTone = Tone | 'private' | 'shared' | 'network' | 'synthetic'

/** Zone and synthetic badges map onto the pill palette: private = info, shared = accent,
 *  network = neutral, synthetic = warning. */
const BADGE_TONE: Record<BadgeTone, PillTone> = {
  neutral: 'neutral', success: 'good', warning: 'warn', danger: 'err', info: 'info',
  private: 'info', shared: 'accent', network: 'neutral', synthetic: 'warn',
}

export function Badge({ tone = 'neutral', children, title }: {
  tone?: BadgeTone
  children: ReactNode
  title?: string
}) {
  return <Pill tone={BADGE_TONE[tone]} title={title}>{children}</Pill>
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  const tone = statusTone(status)
  // In-progress work reads as accent (teal) and pulses; the other tones map directly.
  const pill: PillTone = tone === 'info' ? 'accent' : BADGE_TONE[tone]
  return <Pill tone={pill} dot running={status === 'running'}>{statusLabel(status)}</Pill>
}

export function SyntheticBadge({ title = 'Synthetic demonstration data — not real port data' }: { title?: string }) {
  return <Badge tone="synthetic" title={title}>Synthetic</Badge>
}

export function Callout({ tone = 'info', title, children }: {
  tone?: 'info' | 'warning' | 'danger' | 'success' | 'synthetic' | 'neutral'
  title?: ReactNode
  children?: ReactNode
}) {
  return (
    <div className={`callout ${tone === 'neutral' ? '' : tone}`.trim()} role={tone === 'danger' ? 'alert' : undefined}>
      {title ? <strong>{title} </strong> : null}
      {children}
    </div>
  )
}

export function Loading({ lines = 3, label = 'Loading' }: { lines?: number; label?: string }) {
  return (
    <div className="stack-sm" aria-busy="true" aria-label={label}>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skel" style={{ width: `${90 - i * 12}%`, height: 16 }} />
      ))}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const status = error instanceof ApiError ? error.status : 0
  const title =
    status === 403 ? 'You Do Not Have Access To This' :
    status === 404 ? 'Not Found In This Scope' :
    status === 401 ? 'Sign In Required' :
    'Something Went Wrong'
  const message = error instanceof Error ? error.message : 'The request failed.'
  return (
    <div className="state" role="alert">
      <Crane />
      <h3>{title}</h3>
      <p>{message}</p>
      {onRetry ? <Button size="sm" onClick={onRetry}>Try Again</Button> : null}
    </div>
  )
}

export function EmptyState({ title, children, action }: {
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="state">
      <Crane />
      <h3>{title}</h3>
      {children ? <p>{children}</p> : null}
      {action}
    </div>
  )
}

/** Identifiers and hashes live here — secondary, collapsed by default. */
export function TechnicalDetails({ items, label = 'Technical Details' }: {
  items: Record<string, string | number | null | undefined>
  label?: string
}) {
  const entries = Object.entries(items).filter(([, v]) => v !== null && v !== undefined && v !== '')
  if (!entries.length) return null
  return (
    <details className="technical">
      <summary>{label}</summary>
      <dl className="kv">
        {entries.map(([k, v]) => (
          <div key={k} style={{ display: 'contents' }}>
            <dt>{k}</dt>
            <dd>{String(v)}</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

/** Segmented control (reference `.seg`) with tab semantics. */
export function Tabs<T extends string>({ tabs, value, onChange, label }: {
  tabs: { id: T; label: string }[]
  value: T
  onChange: (id: T) => void
  label: string
}) {
  return (
    <div className="seg" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          className={t.id === value ? 'on' : ''}
          aria-selected={t.id === value}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: (id: string) => ReactNode }) {
  const id = useId()
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  )
}

export function Disclosure({ summary, children, defaultOpen = false }: {
  summary: ReactNode
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div>
      <Button variant="ghost" size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="chevr" size={12} className={open ? 'caret open' : 'caret'} /> {summary}
      </Button>
      {open ? <div style={{ marginTop: 8 }}>{children}</div> : null}
    </div>
  )
}

/** Query wrapper: renders loading, error and empty states consistently. */
export function QueryState<T>({ query, children, empty, isEmpty }: {
  query: { data: T | undefined; isLoading: boolean; error: unknown; refetch: () => unknown }
  children: (data: T) => ReactNode
  empty?: ReactNode
  isEmpty?: (data: T) => boolean
}) {
  if (query.isLoading) return <Loading />
  if (query.error) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  if (query.data === undefined) return <Loading />
  if (isEmpty && isEmpty(query.data)) return <>{empty ?? null}</>
  return <>{children(query.data)}</>
}
