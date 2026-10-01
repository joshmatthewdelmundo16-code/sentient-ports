import type { ReactNode, RefObject } from 'react'
import { kindLabel, useScope } from '../../app/scope'
import { useSelection } from '../../app/selection'
import { statusTone } from '../../shared/format'
import { Icon } from '../ui/Icon'
import { Menu, type MenuItem } from '../ui/Menu'
import { NotificationsButton } from './NotificationsButton'
import { ThemeToggle } from './ThemeToggle'
import { UserMenu } from './UserMenu'

/** Label + value + chevron button, as in the reference's topbar. The popup is the shared Menu. */
function TopbarSelect({ label, valueText, menuLabel, items, value, onSelect, variant, extra, testId }: {
  label: string
  valueText: string
  menuLabel: string
  items: MenuItem[]
  value: string
  onSelect: (value: string) => void
  variant?: 'scenario'
  extra?: ReactNode
  testId?: string
}) {
  return (
    <Menu
      label={menuLabel}
      items={items}
      value={value}
      onSelect={onSelect}
      trigger={(p, open) => (
        <button type="button" className={`tb-sel ${variant ?? ''} ${open ? 'open' : ''}`} data-testid={testId} {...p}>
          <span className="tb-txt">
            <span className="lab">{label}</span>
            <span className="val">{valueText}</span>
          </span>
          {extra}
          <Icon name="chevd" size={14} className="chev" />
        </button>
      )}
    />
  )
}

function StaticSelect({ label, valueText, variant }: { label: string; valueText: string; variant?: 'scenario' }) {
  return (
    <div className={`tb-sel static ${variant ?? ''}`}>
      <span className="tb-txt">
        <span className="lab">{label}</span>
        <span className="val">{valueText}</span>
      </span>
    </div>
  )
}

function ScopeSelect() {
  const { session, scope, setScope, role } = useScope()
  const memberships = session?.memberships ?? []
  if (!memberships.length) {
    return <StaticSelect label="Scope" valueText={session?.auth_mode === 'local' ? 'Local Workspace' : 'No Organization'} />
  }
  return (
    <TopbarSelect
      label="Scope"
      valueText={scope?.name ?? 'Select Scope'}
      menuLabel="Scope"
      value={scope?.id ?? ''}
      onSelect={setScope}
      testId="scope-select"
      items={memberships.map((m) => ({ value: m.organization.id, label: m.organization.name, icon: 'anchor' as const, tag: kindLabel(m.organization.kind) }))}
      extra={<>
        <span className="tb-zone">{kindLabel(scope?.kind)}</span>
        {role ? <span className="tb-role">{role}</span> : null}
      </>}
    />
  )
}

function scenarioDot(status: string): 'run' | 'done' | 'draft' {
  const tone = statusTone(status)
  return tone === 'info' ? 'run' : tone === 'success' ? 'done' : 'draft'
}

function DecisionSelects() {
  const { baseline, scenario, scenariosForBaseline, workspace, selectBaseline, selectScenario } = useSelection()
  if (!workspace || !workspace.baselines.length) return null
  return (
    <>
      <TopbarSelect
        label="Baseline"
        valueText={baseline?.name ?? 'Select Baseline'}
        menuLabel="Baseline"
        value={baseline?.id ?? ''}
        onSelect={selectBaseline}
        items={workspace.baselines.map((b) => ({ value: b.id, label: b.name, icon: 'layers' as const }))}
      />
      {scenariosForBaseline.length ? (
        <TopbarSelect
          label="Scenario"
          variant="scenario"
          valueText={scenario?.name ?? 'Select Scenario'}
          menuLabel="Scenario"
          value={scenario?.id ?? ''}
          onSelect={selectScenario}
          items={scenariosForBaseline.map((s) => ({ value: s.id, label: s.name, dot: scenarioDot(s.run?.status ?? s.status) }))}
        />
      ) : (
        <StaticSelect label="Scenario" valueText="None Yet" variant="scenario" />
      )}
    </>
  )
}

export function Topbar({ navOpen, onToggleNav, toggleRef, showDecisionContext }: {
  navOpen: boolean
  onToggleNav: () => void
  toggleRef: RefObject<HTMLButtonElement | null>
  /** Baseline / scenario selectors are hidden on the network and collaboration pages. */
  showDecisionContext: boolean
}) {
  return (
    <header className="topbar">
      <button
        ref={toggleRef}
        type="button"
        className="hamb"
        onClick={onToggleNav}
        aria-label={navOpen ? 'Hide Navigation' : 'Show Navigation'}
        aria-expanded={navOpen}
        aria-controls="app-sidebar"
      >
        <Icon name="menu" size={18} />
      </button>
      <ScopeSelect />
      {showDecisionContext ? <DecisionSelects /> : null}
      <div className="tb-spacer" />
      <ThemeToggle />
      <NotificationsButton />
      <UserMenu />
    </header>
  )
}
