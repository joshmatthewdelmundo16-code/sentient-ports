import { NavLink } from 'react-router'
import { useBuildInfo } from '../../api/queries'
import { NAV } from '../../app/nav'
import { ROUTE_PATHS } from '../../app/routes'
import { useSelection } from '../../app/selection'
import { Icon } from '../ui/Icon'
import { BrandMark } from './BrandMark'

/** Fixed navigation rail: 240px, or a 60px icon rail when collapsed (desktop); an off-canvas
 *  drawer on small screens. Groups, items and routes are V1's; only the presentation is new. */
export function Sidebar({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const build = useBuildInfo()
  const { workspace } = useSelection()
  const info = build.data
  const shared = Boolean(info && info.database !== 'sqlite')

  // Counts come from the workspace; nothing is shown until it has loaded.
  const counts: Record<string, { n: number; label: string } | undefined> = {
    '/scenarios': workspace ? { n: workspace.scenarios.length, label: 'scenarios' } : undefined,
    '/execution': workspace ? { n: workspace.counts.runs, label: 'runs recorded' } : undefined,
  }

  return (
    <aside id="app-sidebar" className={`rail ${open ? 'open' : ''}`} aria-label="Main Navigation">
      <div className="brand">
        <BrandMark />
        <div className="brand-txt">
          <span className="brand-name">SENTIENT PORTS</span>
          <span className="brand-sub">Decision Support</span>
        </div>
      </div>
      <nav className="nav">
        {NAV.map((g) => ({ ...g, items: g.items.filter((i) => ROUTE_PATHS.has(i.to)) }))
          .filter((g) => g.items.length)
          .map((g) => (
            <div key={g.title} className="nav-section">
              <div className="nav-group">{g.title}</div>
              {g.items.map((item) => {
                const count = counts[item.to]
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === '/'}
                    onClick={onNavigate}
                    aria-label={item.label}
                    title={item.description}
                    className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                  >
                    <Icon name={item.icon} size={17} />
                    <span className="nav-label">{item.label}</span>
                    {count && count.n > 0 ? <span className="nav-badge" title={`${count.n} ${count.label}`}>{count.n}</span> : null}
                  </NavLink>
                )
              })}
            </div>
          ))}
      </nav>
      <div className="rail-foot">
        <div className="sys-stat" title={info ? `${shared ? 'Shared PostgreSQL' : 'Local SQLite'} database · ${info.environment} · v${info.version}` : 'Connecting'}>
          <span className={`dot ${shared ? 'shared' : ''}`} aria-hidden="true" />
          <div className="txt">
            <small>Database</small>
            <b>{info ? (shared ? 'Shared PostgreSQL' : 'Local SQLite') : 'Connecting…'}</b>
            {info ? <span className="sys-meta">{info.environment} · v{info.version}</span> : null}
            <a className="sys-link" href="/ui">Previous Interface</a>
          </div>
        </div>
      </div>
    </aside>
  )
}
