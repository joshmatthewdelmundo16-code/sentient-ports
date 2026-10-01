import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
import { Icon, type IconName } from './Icon'
import { nextMenuIndex, type MenuNavKey } from './menuNav'

export interface MenuItem {
  value: string
  label: ReactNode
  icon?: IconName
  tag?: string
  /** Status dot shown instead of an icon (scenario state). */
  dot?: 'run' | 'done' | 'draft'
  disabled?: boolean
}

/** Props the trigger button must spread. The trigger is rendered by the caller so each
 *  surface (topbar select, avatar) keeps its own look. */
export interface MenuTriggerProps {
  ref: RefObject<HTMLButtonElement | null>
  onClick: () => void
  onKeyDown: (e: KeyboardEvent<HTMLButtonElement>) => void
  'aria-haspopup': 'menu'
  'aria-expanded': boolean
  'aria-controls': string | undefined
}

const NAV_KEYS: MenuNavKey[] = ['ArrowDown', 'ArrowUp', 'Home', 'End']

/** Dropdown menu (the reference's popmenu): keyboard navigable, closes on Escape, Tab and
 *  outside click, and returns focus to its trigger. */
export function Menu({ items, value, onSelect, trigger, label, align = 'start', header }: {
  items: MenuItem[]
  /** Selected item: shown with a check and exposed as a radio item. Omit for action menus. */
  value?: string | null
  onSelect: (value: string) => void
  trigger: (props: MenuTriggerProps, open: boolean) => ReactNode
  /** Accessible name of the popup. */
  label: string
  align?: 'start' | 'end'
  /** Non-interactive block above the items. */
  header?: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([])
  const focusOnMount = useRef(false)
  const menuId = useId()

  const close = useCallback((restoreFocus: boolean) => {
    setOpen(false)
    if (restoreFocus) triggerRef.current?.focus()
  }, [])

  const openMenu = () => {
    focusOnMount.current = true
    setOpen(true)
  }

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const disabled = items.map((i) => Boolean(i.disabled))
  const selected = value === undefined ? -1 : items.findIndex((i) => i.value === value && !i.disabled)
  const initial = selected >= 0 ? selected : disabled.findIndex((d) => !d)

  const onMenuKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      close(true)
    } else if (e.key === 'Tab') {
      close(false)
    } else if (NAV_KEYS.includes(e.key as MenuNavKey)) {
      e.preventDefault()
      const current = itemRefs.current.findIndex((el) => el === document.activeElement)
      const next = nextMenuIndex(current, e.key as MenuNavKey, disabled)
      if (next >= 0) itemRefs.current[next]?.focus()
    }
  }

  const triggerProps: MenuTriggerProps = {
    ref: triggerRef,
    onClick: () => (open ? close(false) : openMenu()),
    onKeyDown: (e) => {
      if (!open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        e.preventDefault()
        openMenu()
      }
    },
    'aria-haspopup': 'menu',
    'aria-expanded': open,
    'aria-controls': open ? menuId : undefined,
  }

  return (
    <div className="menu-wrap" ref={wrapRef}>
      {trigger(triggerProps, open)}
      {open ? (
        <div id={menuId} className={`popmenu ${align === 'end' ? 'end' : ''}`} role="menu" aria-label={label} onKeyDown={onMenuKey}>
          {header}
          {items.map((item, i) => {
            const isSelected = value !== undefined && item.value === value
            return (
              <button
                key={item.value}
                type="button"
                tabIndex={-1}
                role={value === undefined ? 'menuitem' : 'menuitemradio'}
                aria-checked={value === undefined ? undefined : isSelected}
                disabled={item.disabled}
                className={`pm-item ${isSelected ? 'on' : ''}`}
                ref={(el) => {
                  itemRefs.current[i] = el
                  if (el && i === initial && focusOnMount.current) {
                    focusOnMount.current = false
                    el.focus()
                  }
                }}
                onClick={() => {
                  onSelect(item.value)
                  close(true)
                }}
              >
                {item.dot ? <span className={`pd2 ${item.dot}`} aria-hidden="true" /> : item.icon ? <Icon name={item.icon} size={14} /> : null}
                <span className="pm-label">{item.label}</span>
                {item.tag ? <span className="pm-tag">{item.tag}</span> : null}
                {isSelected ? <Icon name="check" size={14} className="pm-ck" /> : null}
              </button>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}
