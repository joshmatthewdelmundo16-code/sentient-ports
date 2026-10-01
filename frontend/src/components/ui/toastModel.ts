/** Toast model, free of React so it can be unit-tested. */
export type ToastKind = 'good' | 'err' | 'warn' | 'info'

export interface ToastItem {
  id: number
  kind: ToastKind
  title: string
  message?: string
  leaving: boolean
}

export const MAX_TOASTS = 5
/** Exit animation length; must match the CSS transition on `.toast`. */
export const LEAVE_MS = 180

/** Successes and info fade quickly (reference: 4.2s). Failures and warnings stay longer — a
 *  message the user may need to read and act on should not vanish while they are reading it. */
export function toastDuration(kind: ToastKind): number {
  return kind === 'err' || kind === 'warn' ? 7000 : 4200
}

/** Adds a toast, keeping at most MAX_TOASTS (oldest dropped). */
export function addToast(list: ToastItem[], item: ToastItem, max = MAX_TOASTS): ToastItem[] {
  return [...list, item].slice(-max)
}

export function markLeaving(list: ToastItem[], id: number): ToastItem[] {
  return list.map((t) => (t.id === id ? { ...t, leaving: true } : t))
}

export function removeToast(list: ToastItem[], id: number): ToastItem[] {
  return list.filter((t) => t.id !== id)
}
