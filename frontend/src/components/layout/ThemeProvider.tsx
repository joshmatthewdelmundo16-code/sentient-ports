import { createContext, useCallback, useContext, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react'
import {
  applyTheme,
  oppositeTheme,
  prefersLightNow,
  readStoredTheme,
  resolveTheme,
  subscribePrefersLight,
  writeStoredTheme,
  type Theme,
} from './theme'

interface ThemeContextValue {
  /** The user's explicit choice, or null while following the OS. */
  explicit: Theme | null
  /** The theme in effect right now. */
  theme: Theme
  setTheme: (theme: Theme) => void
  toggle: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [explicit, setExplicit] = useState<Theme | null>(() => readStoredTheme())
  const prefersLight = useSyncExternalStore(subscribePrefersLight, prefersLightNow, () => false)
  const theme = resolveTheme(explicit, prefersLight)

  const setTheme = useCallback((next: Theme) => {
    setExplicit(next)
    applyTheme(document.documentElement, next)
    writeStoredTheme(next)
  }, [])

  const toggle = useCallback(() => setTheme(oppositeTheme(theme)), [setTheme, theme])

  const value = useMemo(() => ({ explicit, theme, setTheme, toggle }), [explicit, theme, setTheme, toggle])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used inside <ThemeProvider>')
  return ctx
}
