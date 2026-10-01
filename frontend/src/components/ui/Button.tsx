import { useEffect, useRef, useState, type ButtonHTMLAttributes, type MouseEvent, type ReactNode, type Ref } from 'react'
import { Icon, type IconName } from './Icon'

export type ButtonVariant = 'default' | 'primary' | 'ghost' | 'outline' | 'danger' | 'good'
export type ButtonSize = 'md' | 'sm' | 'xs'

export function buttonClass(variant: ButtonVariant = 'default', size: ButtonSize = 'md', block = false, extra = ''): string {
  return ['btn', variant !== 'default' ? `btn-${variant}` : '', size !== 'md' ? `btn-${size}` : '', block ? 'btn-block' : '', extra]
    .filter(Boolean)
    .join(' ')
}

interface CommonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onClick'> {
  variant?: ButtonVariant
  size?: ButtonSize
  block?: boolean
  icon?: IconName
  ref?: Ref<HTMLButtonElement>
}

/** Plain button. `type` defaults to "button" so it never submits a form by accident. */
export function Button({ variant, size, block, icon, className, type = 'button', children, ...rest }: CommonProps & {
  onClick?: (e: MouseEvent<HTMLButtonElement>) => void
}) {
  return (
    <button type={type} className={buttonClass(variant, size, block, className)} {...rest}>
      {icon ? <Icon name={icon} size={size === 'md' || !size ? 15 : 13} /> : null}
      {children}
    </button>
  )
}

/** If the action never reports "busy" (it returned early, or finished before a render saw it),
 *  the click lock is released after this long so the button cannot stay stuck. */
const LOCK_FALLBACK_MS = 1000

/** Button for actions that take time. While busy it shows a spinner and `loadingLabel`
 *  ("Saving..."), is disabled, and ignores further clicks, so an action cannot be started twice.
 *
 *  Busy state comes from either source:
 *   · `loading` — pass a mutation's `isPending`; or
 *   · `onClick` returning a promise — the button tracks it itself.
 *
 *  A mutation's `isPending` flips a tick after the click, so a fast second click (a double-click)
 *  could slip through; the button therefore locks synchronously on the first click and holds the
 *  lock until the action has been seen busy and is done. */
export function LoadingButton({ loading, loadingLabel, onClick, disabled, variant, size, block, icon, className, type = 'button', children, ...rest }: CommonProps & {
  loading?: boolean
  /** Shown while busy. Falls back to the normal label. */
  loadingLabel?: ReactNode
  onClick?: (e: MouseEvent<HTMLButtonElement>) => unknown
}) {
  const [selfBusy, setSelfBusy] = useState(false)
  const busy = Boolean(loading) || selfBusy
  const lock = useRef<{ sawBusy: boolean; timer: ReturnType<typeof setTimeout> } | null>(null)

  useEffect(() => {
    const l = lock.current
    if (!l) return
    if (busy) l.sawBusy = true
    else if (l.sawBusy) {
      clearTimeout(l.timer)
      lock.current = null
    }
  }, [busy])

  useEffect(() => () => { if (lock.current) clearTimeout(lock.current.timer) }, [])

  const handle = (e: MouseEvent<HTMLButtonElement>) => {
    if (busy || disabled || lock.current) return
    const held = { sawBusy: false, timer: setTimeout(() => { if (lock.current === held && !held.sawBusy) lock.current = null }, LOCK_FALLBACK_MS) }
    lock.current = held
    const result = onClick?.(e)
    if (result && typeof (result as Promise<unknown>).then === 'function') {
      setSelfBusy(true)
      const done = () => setSelfBusy(false)
      void (result as Promise<unknown>).then(done, done)
    }
  }

  return (
    <button
      type={type}
      className={buttonClass(variant, size, block, `${busy ? 'loading' : ''} ${className ?? ''}`.trim())}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      onClick={handle}
      {...rest}
    >
      {busy ? (
        <>
          <span className="spin" aria-hidden="true" />
          <span>{loadingLabel ?? children}</span>
        </>
      ) : (
        <>
          {icon ? <Icon name={icon} size={size === 'md' || !size ? 15 : 13} /> : null}
          {children}
        </>
      )}
    </button>
  )
}
