import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Icon, type IconName } from './Icon'
import { addToast, LEAVE_MS, markLeaving, removeToast, toastDuration, type ToastItem, type ToastKind } from './toastModel'

interface ToastApi {
  success: (title: string, message?: string) => void
  error: (title: string, message?: string) => void
  warn: (title: string, message?: string) => void
  info: (title: string, message?: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

const ICON: Record<ToastKind, IconName> = { good: 'checkc', err: 'alert', warn: 'alert', info: 'zap' }

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(1)
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>())

  const later = useCallback((fn: () => void, ms: number) => {
    const t = setTimeout(() => {
      timers.current.delete(t)
      fn()
    }, ms)
    timers.current.add(t)
  }, [])

  const dismiss = useCallback((id: number) => {
    setToasts((l) => markLeaving(l, id))
    later(() => setToasts((l) => removeToast(l, id)), LEAVE_MS)
  }, [later])

  const push = useCallback((kind: ToastKind, title: string, message?: string) => {
    const id = nextId.current++
    setToasts((l) => addToast(l, { id, kind, title, message, leaving: false }))
    later(() => dismiss(id), toastDuration(kind))
  }, [dismiss, later])

  useEffect(() => {
    const pending = timers.current
    return () => pending.forEach(clearTimeout)
  }, [])

  const api = useMemo<ToastApi>(() => ({
    success: (t, m) => push('good', t, m),
    error: (t, m) => push('err', t, m),
    warn: (t, m) => push('warn', t, m),
    info: (t, m) => push('info', t, m),
  }), [push])

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toasts" role="region" aria-label="Messages">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind} ${t.leaving ? 'leaving' : ''}`.trim()} role={t.kind === 'err' ? 'alert' : 'status'}>
            <span className="tic"><Icon name={ICON[t.kind]} size={16} /></span>
            <div className="tbody">
              <div className="ttl">{t.title}</div>
              {t.message ? <div className="tmsg">{t.message}</div> : null}
            </div>
            <button type="button" className="tx" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              <Icon name="x" size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}
