/** Shared presentational components. No data fetching here — features pass data in. */
import { useId, useState, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import { statusLabel, statusTone, type Tone } from './format'

export function PageHeader({ title, description, actions }: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="row">{actions}</div> : null}
    </header>
  )
}

export function Card({ title, subtitle, actions, children, className = '', tight = false, id }: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children?: ReactNode
  className?: string
  tight?: boolean
  id?: string
}) {
  return (
    <section className={`card ${tight ? 'card-tight' : ''} ${className}`} id={id}>
      {title || actions ? (
        <div className="card-header">
          <div>
            {title ? <h2>{title}</h2> : null}
            {subtitle ? <div className="card-sub">{subtitle}</div> : null}
          </div>
          {actions ? <div className="row">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  )
}

export function Badge({ tone = 'neutral', children, title }: {
  tone?: Tone | 'private' | 'shared' | 'network' | 'synthetic'
  children: ReactNode
  title?: string
}) {
  return (
    <span className={`badge ${tone === 'neutral' ? '' : tone}`} title={title}>
      {children}
    </span>
  )
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  return (
    <Badge tone={statusTone(status)}>
      <span className="dot" aria-hidden="true" />
      {statusLabel(status)}
    </Badge>
  )
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
    <div className={`callout ${tone === 'neutral' ? '' : tone}`} role={tone === 'danger' ? 'alert' : undefined}>
      {title ? <strong>{title} </strong> : null}
      {children}
    </div>
  )
}

export function Loading({ lines = 3, label = 'Loading' }: { lines?: number; label?: string }) {
  return (
    <div className="stack-sm" aria-busy="true" aria-label={label}>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 12}%`, height: 16 }} />
      ))}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const status = error instanceof ApiError ? error.status : 0
  const title =
    status === 403 ? 'You do not have access to this' :
    status === 404 ? 'Not found in this scope' :
    status === 401 ? 'Sign in required' :
    'Something went wrong'
  const message = error instanceof Error ? error.message : 'The request failed.'
  return (
    <div className="state" role="alert">
      <h3>{title}</h3>
      <p>{message}</p>
      {onRetry ? <button className="btn btn-sm" onClick={onRetry}>Try again</button> : null}
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
      <h3>{title}</h3>
      {children ? <p>{children}</p> : null}
      {action}
    </div>
  )
}

/** Identifiers and hashes live here — secondary, collapsed by default. */
export function TechnicalDetails({ items, label = 'Technical details' }: {
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

export function Tabs<T extends string>({ tabs, value, onChange, label }: {
  tabs: { id: T; label: string }[]
  value: T
  onChange: (id: T) => void
  label: string
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          className="tab"
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
      <button type="button" className="btn btn-ghost btn-sm" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? '▾' : '▸'} {summary}
      </button>
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
