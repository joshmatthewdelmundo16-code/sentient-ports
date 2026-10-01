import { useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './Icon'
import { useModal } from './useModal'

function DrawerPanel({ onClose, title, subtitle, children, footer }: {
  onClose: () => void
  title: string
  subtitle?: ReactNode
  children: ReactNode
  footer?: ReactNode
}) {
  const titleId = useId()
  const panelRef = useRef<HTMLElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  useModal(panelRef, onClose, closeRef)

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panelRef}>
        <div className="dr-head">
          <div>
            <h3 id={titleId}>{title}</h3>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          <button type="button" className="close-x" ref={closeRef} onClick={onClose} aria-label="Close">
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="dr-body">{children}</div>
        {footer ? <div className="dr-foot">{footer}</div> : null}
      </aside>
    </>
  )
}

/** Right-hand slide-over (480px). Mount-on-open, so focus handling follows its lifetime. */
export function Drawer({ open, ...rest }: {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: ReactNode
  children: ReactNode
  footer?: ReactNode
}) {
  if (!open) return null
  return createPortal(<DrawerPanel {...rest} />, document.body)
}
