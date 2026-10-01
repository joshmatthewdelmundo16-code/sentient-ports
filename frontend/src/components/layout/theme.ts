/** Theme logic, free of React and the DOM so it can be unit-tested.
 *
 *  Model (matches the reference, plus a working explicit-light override):
 *    explicit === null → follow the OS preference; with no preference signal, dark
 *    explicit === 'dark' | 'light' → user's choice, persisted
 *  tokens.css keys off the `data-theme` attribute on <html>; null removes the attribute. */

export type Theme = 'dark' | 'light'

export const THEME_STORAGE_KEY = 'theme'
const LIGHT_QUERY = '(prefers-color-scheme: light)'

export function parseTheme(value: unknown): Theme | null {
  return value === 'dark' || value === 'light' ? value : null
}

/** Storage can be missing or throw (private windows, blocked site data). Never let it break the app. */
function browserStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

export function readStoredTheme(storage: Pick<Storage, 'getItem'> | null = browserStorage()): Theme | null {
  if (!storage) return null
  try {
    return parseTheme(storage.getItem(THEME_STORAGE_KEY))
  } catch {
    return null
  }
}

export function writeStoredTheme(theme: Theme | null, storage: Pick<Storage, 'setItem' | 'removeItem'> | null = browserStorage()): void {
  if (!storage) return
  try {
    if (theme === null) storage.removeItem(THEME_STORAGE_KEY)
    else storage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    /* storage unavailable — the choice just won't persist */
  }
}

/** The theme actually in effect. */
export function resolveTheme(explicit: Theme | null, prefersLight: boolean): Theme {
  return explicit ?? (prefersLight ? 'light' : 'dark')
}

export function oppositeTheme(theme: Theme): Theme {
  return theme === 'dark' ? 'light' : 'dark'
}

export function applyTheme(
  root: Pick<HTMLElement, 'setAttribute' | 'removeAttribute'>,
  explicit: Theme | null,
): void {
  if (explicit === null) root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', explicit)
}

export function prefersLightNow(): boolean {
  try {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(LIGHT_QUERY).matches
  } catch {
    return false
  }
}

export function subscribePrefersLight(onChange: () => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => undefined
  const mq = window.matchMedia(LIGHT_QUERY)
  mq.addEventListener('change', onChange)
  return () => mq.removeEventListener('change', onChange)
}

/** Apply the stored choice before React renders, so there is no flash of the wrong theme.
 *  Runs from module code, not an inline script: the app's CSP forbids inline scripts. */
export function initTheme(): Theme | null {
  const stored = readStoredTheme()
  if (typeof document !== 'undefined') applyTheme(document.documentElement, stored)
  return stored
}
