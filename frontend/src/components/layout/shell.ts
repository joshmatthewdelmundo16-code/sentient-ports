/** Pure shell logic (navigation state, breadcrumbs, avatar initials), kept free of React and
 *  the DOM so it can be unit-tested. */
import type { NavGroup } from '../../app/nav'

export const NAV_STORAGE_KEY = 'nav-open'
/** At or below this width the sidebar is an off-canvas drawer; above it, a 240px / 60px rail. */
export const DESKTOP_QUERY = '(min-width: 961px)'

/** Initial open state. On desktop the saved choice wins (default expanded); on small screens
 *  the drawer always starts closed so a saved desktop preference cannot cover the page. */
export function initialNavOpen(saved: string | null, isDesktop: boolean): boolean {
  if (!isDesktop) return false
  return saved === null ? true : saved === '1'
}

export function isDesktopNow(): boolean {
  try {
    return typeof window === 'undefined' || typeof window.matchMedia !== 'function' || window.matchMedia(DESKTOP_QUERY).matches
  } catch {
    return true
  }
}

export function readNavSaved(): string | null {
  try {
    return localStorage.getItem(NAV_STORAGE_KEY)
  } catch {
    return null
  }
}

export function writeNavSaved(open: boolean): void {
  try {
    localStorage.setItem(NAV_STORAGE_KEY, open ? '1' : '0')
  } catch {
    /* storage unavailable — the preference just won't persist */
  }
}

export interface Crumb {
  group: string | null
  label: string
}

/** Where the current path sits in the navigation. Unknown paths have no group. */
export function resolveCrumb(pathname: string, nav: NavGroup[]): Crumb | null {
  const path = pathname.replace(/\/+$/, '') || '/'
  for (const g of nav) {
    for (const item of g.items) {
      const hit = item.to === '/' ? path === '/' : path === item.to || path.startsWith(`${item.to}/`)
      if (hit) return { group: g.title, label: item.label }
    }
  }
  return null
}

/** "Maria del Carmen Ortiz" → "MO"; "admin" → "AD"; empty → "?". */
export function initialsOf(name: string | null | undefined): string {
  const parts = (name ?? '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  if (parts.length === 1) return (parts[0] as string).slice(0, 2).toUpperCase()
  return `${(parts[0] as string)[0]}${(parts[parts.length - 1] as string)[0]}`.toUpperCase()
}
