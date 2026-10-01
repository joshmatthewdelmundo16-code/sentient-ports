import type { ReactNode } from 'react'

export type PillTone = 'good' | 'warn' | 'err' | 'info' | 'accent' | 'neutral'

/** Status pill (reference `.pill`): optional leading dot, optional pulse for live states. */
export function Pill({ tone = 'neutral', dot = false, running = false, title, children }: {
  tone?: PillTone
  dot?: boolean
  /** Pulses the dot — for work in progress. */
  running?: boolean
  title?: string
  children: ReactNode
}) {
  return (
    <span className={`pill ${tone} ${running ? 'running' : ''}`.trim()} title={title}>
      {dot ? <span className="pd" aria-hidden="true" /> : null}
      {children}
    </span>
  )
}
