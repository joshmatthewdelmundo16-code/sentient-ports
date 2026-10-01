import { useEffect, useRef, useState } from 'react'
import { Outlet, useLocation } from 'react-router'
import { useBuildInfo } from '../api/queries'
import { Breadcrumbs } from '../components/layout/Breadcrumbs'
import { Sidebar } from '../components/layout/Sidebar'
import { Topbar } from '../components/layout/Topbar'
import { DESKTOP_QUERY, initialNavOpen, isDesktopNow, readNavSaved, writeNavSaved } from '../components/layout/shell'

/** Application shell: fixed sidebar (240px, or a 60px icon rail), 56px topbar, breadcrumb strip
 *  and an inner scroll region. Below 961px the sidebar is an off-canvas drawer instead. */
export function AppShell() {
  const location = useLocation()
  const build = useBuildInfo()
  const info = build.data
  const stale = info && info.version !== __API_VERSION__
  const toggleRef = useRef<HTMLButtonElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // One boolean drives both modes: desktop → expanded (true) / icon rail (false);
  // small screens → drawer open / closed. The desktop choice persists.
  const [navOpen, setNavOpen] = useState<boolean>(() => initialNavOpen(readNavSaved(), isDesktopNow()))

  const toggleNav = () => {
    const next = !navOpen
    setNavOpen(next)
    if (isDesktopNow()) writeNavSaved(next)
  }

  // Crossing the breakpoint: small screens always start with the drawer closed; coming back to
  // desktop restores the saved preference.
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(DESKTOP_QUERY)
    const onChange = () => setNavOpen(initialNavOpen(readNavSaved(), mq.matches))
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  // Escape closes the drawer (small screens only) and returns focus to the toggle.
  useEffect(() => {
    if (!navOpen || isDesktopNow()) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setNavOpen(false)
        toggleRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navOpen])

  // A route change starts at the top of the scroll region, as in the reference.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 })
  }, [location.pathname])

  // On small screens the drawer closes after choosing a destination; on desktop it stays put.
  const closeOnMobile = () => {
    if (!isDesktopNow()) setNavOpen(false)
  }

  const path = location.pathname
  const showDecisionContext = !(path.startsWith('/network') || path.startsWith('/collaboration'))

  return (
    <div className={`app ${navOpen ? '' : 'collapsed'}`}>
      <div className={`rail-scrim ${navOpen ? 'open' : ''}`} onClick={() => setNavOpen(false)} aria-hidden="true" />
      <Sidebar open={navOpen} onNavigate={closeOnMobile} />
      <div className="main">
        {stale ? (
          <div className="banner" role="status">
            This page was built for API {__API_VERSION__} but the server reports {info?.version}. Reload after the server restarts, or rebuild the frontend.
          </div>
        ) : null}
        {info && info.missing_capabilities.length ? (
          <div className="banner danger" role="status">
            The server is missing capabilities this page needs: {info.missing_capabilities.join(', ')}.
          </div>
        ) : null}
        <Topbar navOpen={navOpen} onToggleNav={toggleNav} toggleRef={toggleRef} showDecisionContext={showDecisionContext} />
        <Breadcrumbs pathname={path} />
        <div className="scroll" ref={scrollRef}>
          <main className="page" id="main">
            <div key={path} className="view">
              <Outlet />
            </div>
          </main>
        </div>
      </div>
    </div>
  )
}
