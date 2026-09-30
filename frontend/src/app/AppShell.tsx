import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useBuildInfo } from '../api/queries'
import { Badge } from '../shared/ui'
import { NAV } from './nav'
import { ROUTE_PATHS } from './routes'
import { kindLabel, useScope } from './scope'
import { useSelection } from './selection'

function zoneTone(kind: string | undefined): 'private' | 'shared' | 'network' {
  if (kind === 'port' || kind === 'sandbox') return 'private'
  if (kind === 'network') return 'network'
  return 'shared'
}

function ScopeSwitcher() {
  const { session, scope, setScope, role } = useScope()
  const memberships = session?.memberships ?? []
  if (!memberships.length) {
    return (
      <div className="context-item">
        <span className="context-label">Scope</span>
        <span className="context-value">{session?.auth_mode === 'local' ? 'Local workspace' : 'No organization'}</span>
      </div>
    )
  }
  return (
    <div className="context-item">
      <label className="context-label" htmlFor="scope-select">Scope</label>
      <div className="row" style={{ gap: 6 }}>
        <Badge tone={zoneTone(scope?.kind)}>{kindLabel(scope?.kind)}</Badge>
        <select
          id="scope-select"
          className="input"
          style={{ width: 'auto', padding: '3px 8px', fontWeight: 600 }}
          value={scope?.id ?? ''}
          onChange={(e) => setScope(e.target.value)}
          data-testid="scope-select"
        >
          {memberships.map((m) => (
            <option key={m.organization.id} value={m.organization.id}>
              {m.organization.name}
            </option>
          ))}
        </select>
        {role ? <span className="tiny muted">as {role}</span> : null}
      </div>
    </div>
  )
}

function DecisionContext() {
  const { baseline, scenario, scenariosForBaseline, workspace, selectBaseline, selectScenario } = useSelection()
  if (!workspace || !workspace.baselines.length) return null
  return (
    <>
      <span className="context-sep" aria-hidden="true" />
      <div className="context-item">
        <label className="context-label" htmlFor="baseline-select">Baseline</label>
        <select id="baseline-select" className="input" style={{ width: 'auto', padding: '3px 8px', fontWeight: 600 }}
          value={baseline?.id ?? ''} onChange={(e) => selectBaseline(e.target.value)}>
          {workspace.baselines.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
      </div>
      <div className="context-item">
        <label className="context-label" htmlFor="scenario-select">Scenario</label>
        {scenariosForBaseline.length ? (
          <select id="scenario-select" className="input" style={{ width: 'auto', padding: '3px 8px', fontWeight: 600 }}
            value={scenario?.id ?? ''} onChange={(e) => selectScenario(e.target.value)}>
            {scenariosForBaseline.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        ) : (
          <span className="context-value muted">None yet</span>
        )}
      </div>
    </>
  )
}

function UserMenu() {
  const { session } = useScope()
  const qc = useQueryClient()
  if (!session?.user) {
    return session?.auth_mode === 'local' ? <Badge tone="warning" title="AUTH_MODE=local — single-developer mode, SQLite only">Local mode</Badge> : null
  }
  const signOut = async () => {
    await api('/api/auth/logout', { method: 'POST' }).catch(() => undefined)
    qc.clear()
    window.location.assign('/app/login')
  }
  return (
    <div className="row" style={{ gap: 8 }}>
      <div className="context-item" style={{ textAlign: 'right' }}>
        <span className="context-label">Signed in</span>
        <span className="context-value">{session.user.display_name}</span>
      </div>
      <button type="button" className="btn btn-sm" onClick={() => void signOut()}>Sign out</button>
    </div>
  )
}

export function AppShell() {
  const [open, setOpen] = useState(false)
  const location = useLocation()
  const build = useBuildInfo()
  const info = build.data
  const shared = info && info.database !== 'sqlite'
  const stale = info && info.version !== __API_VERSION__
  return (
    <div className="shell">
      <div className={`scrim ${open ? 'open' : ''}`} onClick={() => setOpen(false)} aria-hidden="true" />
      <aside className={`sidebar ${open ? 'open' : ''}`} aria-label="Main navigation">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">PN</div>
          <div>
            <div className="brand-name">Port decision platform</div>
            <div className="brand-sub">Federated models · governed sharing</div>
          </div>
        </div>
        <nav className="nav">
          {NAV.map((g) => ({ ...g, items: g.items.filter((i) => ROUTE_PATHS.has(i.to)) })).filter((g) => g.items.length).map((g) => (
            <div key={g.title} className="nav-group">
              <div className="nav-group-title">{g.title}</div>
              {g.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.to === '/'} onClick={() => setOpen(false)}
                  className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`} title={item.description}>
                  <span className="nav-step" aria-hidden="true">{item.step ?? '·'}</span>
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="env">
            <span className={`env-dot ${shared ? 'shared' : ''}`} aria-hidden="true" />
            {info ? (shared ? 'Shared PostgreSQL database' : 'Local SQLite database') : 'Connecting…'}
          </div>
          {info ? <div className="tiny" style={{ marginTop: 4 }}>{info.environment} · v{info.version}</div> : null}
          <div className="tiny" style={{ marginTop: 4 }}><a href="/ui" style={{ color: 'inherit', textDecoration: 'underline' }}>Previous interface</a></div>
        </div>
      </aside>
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
        <header className="contextbar">
          <button type="button" className="btn btn-sm menu-toggle" onClick={() => setOpen(true)} aria-label="Open navigation">☰</button>
          <ScopeSwitcher />
          {location.pathname.startsWith('/network') || location.pathname.startsWith('/collaboration') ? null : <DecisionContext />}
          <span className="spacer" />
          <UserMenu />
        </header>
        <main className="page" id="main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
