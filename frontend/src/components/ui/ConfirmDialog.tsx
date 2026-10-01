import { useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { LoadingButton } from './Button'
import { Icon } from './Icon'
import { useModal } from './useModal'

function ConfirmPanel({ title, children, confirmLabel, busyLabel, tone, onConfirm, onClose }: {
  title: string
  children: ReactNode
  confirmLabel: string
  busyLabel: string
  tone: 'accent' | 'danger'
  onConfirm: () => unknown
  onClose: () => void
}) {
  const titleId = useId()
  const bodyId = useId()
  const panelRef = useRef<HTMLDivElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const [pending, setPending] = useState(false)
  // While the action runs, the dialog cannot be dismissed (Escape, scrim and Cancel are inert).
  useModal(panelRef, pending ? null : onClose, confirmRef)

  const confirm = async () => {
    if (pending) return
    setPending(true)
    try {
      await onConfirm()
      onClose()
    } catch {
      // The caller reports the failure (toast); the dialog stays open so the user can retry or cancel.
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget && !pending) onClose() }}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={bodyId} ref={panelRef}>
        <div className="dhead">
          <div className={`dic ${tone === 'danger' ? 'err' : 'accent'}`}>
            <Icon name={tone === 'danger' ? 'alert' : 'shield'} size={19} />
          </div>
          <div><h3 id={titleId}>{title}</h3></div>
        </div>
        <div className="dbody" id={bodyId}>{children}</div>
        <div className="dfoot">
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={pending}>Cancel</button>
          <LoadingButton
            ref={confirmRef}
            variant={tone === 'danger' ? 'danger' : 'primary'}
            loading={pending}
            loadingLabel={busyLabel}
            onClick={() => void confirm()}
          >
            {confirmLabel}
          </LoadingButton>
        </div>
      </div>
    </div>
  )
}

/** Confirmation for consequential actions. `onConfirm` may return a promise: the confirm button
 *  shows `busyLabel`, the dialog closes on success, and stays open on failure. */
export function ConfirmDialog({ open, title, children, confirmLabel = 'Confirm', busyLabel = 'Confirming...', tone = 'accent', onConfirm, onClose }: {
  open: boolean
  title: string
  children: ReactNode
  confirmLabel?: string
  busyLabel?: string
  tone?: 'accent' | 'danger'
  onConfirm: () => unknown
  onClose: () => void
}) {
  if (!open) return null
  return createPortal(
    <ConfirmPanel title={title} confirmLabel={confirmLabel} busyLabel={busyLabel} tone={tone} onConfirm={onConfirm} onClose={onClose}>
      {children}
    </ConfirmPanel>,
    document.body,
  )
}
