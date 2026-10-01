import { useEffect, type RefObject } from 'react'

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Behaviour shared by the drawer and the confirm dialog while they are mounted:
 *  focus moves in (to `initialFocus`, else the first focusable) and returns to the opener on
 *  close; Tab stays inside the panel; Escape calls `onEscape` (pass null to ignore it, e.g.
 *  while an action is running). */
export function useModal(
  panelRef: RefObject<HTMLElement | null>,
  onEscape: (() => void) | null,
  initialFocus?: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const first = panelRef.current?.querySelector<HTMLElement>(FOCUSABLE)
    ;(initialFocus?.current ?? first)?.focus()
    return () => opener?.focus?.()
    // Runs once per mount: the panel is mounted only while open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onEscape?.()
        return
      }
      if (e.key !== 'Tab') return
      const nodes = panelRef.current ? [...panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)] : []
      const first = nodes[0]
      const last = nodes[nodes.length - 1]
      if (!first || !last) {
        e.preventDefault()
        return
      }
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [panelRef, onEscape])
}
