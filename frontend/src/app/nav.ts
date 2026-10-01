import type { IconName } from '../components/ui/Icon'

/** Navigation — the product's information architecture, in workflow order. All labels are
 *  Title Case (every word capitalised), preserving acronyms. `step` numbers the primary
 *  decision workflow. */
export interface NavItem {
  to: string
  label: string
  step?: number
  icon: IconName
  description: string
}

export interface NavGroup {
  title: string
  items: NavItem[]
}

export const NAV: NavGroup[] = [
  {
    title: 'Decide',
    items: [
      { to: '/', label: 'Start Here', step: 1, icon: 'dashboard', description: 'What this platform does and where you are in the workflow.' },
      { to: '/decision', label: 'Decision Overview', step: 2, icon: 'target', description: 'Baseline, scenario, what changed and the resulting impact.' },
      { to: '/scenarios', label: 'Scenario Comparison', step: 3, icon: 'layers', description: 'Change assumptions, run the scenario and compare every metric.' },
      { to: '/excel', label: 'Assumptions & Excel', step: 4, icon: 'sheet', description: 'Upload a workbook, validate it and commit the change.' },
    ],
  },
  {
    title: 'Understand',
    items: [
      { to: '/impact', label: 'Impact & Why', step: 5, icon: 'bars', description: 'The models a change flowed through, field by field.' },
      { to: '/sources', label: 'Sources & Provenance', step: 6, icon: 'database', description: 'Where every value came from.' },
      { to: '/activity', label: 'Activity', step: 7, icon: 'history', description: 'What happened, in plain language.' },
      { to: '/execution', label: 'Execution & Governance', step: 8, icon: 'activity', description: 'Runs, steps, and what is shared.' },
    ],
  },
  {
    title: 'Share',
    items: [
      { to: '/governance', label: 'Governance', step: 9, icon: 'shield', description: 'Approvals, exposure boundary and audit trail.' },
      { to: '/network', label: 'Port Network', step: 10, icon: 'network', description: 'Hub views built only from approved outputs.' },
      { to: '/collaboration', label: 'Collaboration', icon: 'link', description: 'Shared decision cases between organizations.' },
    ],
  },
  {
    title: 'Plan',
    items: [
      { to: '/planning', label: 'Master Planning', icon: 'gauge', description: 'Multi-year plans with investments and capacity.' },
      { to: '/optimization', label: 'Optimization', icon: 'sliders', description: 'Decision variables, constraints and trade-offs.' },
      { to: '/models', label: 'Model Library', icon: 'box', description: 'Models, providers, contracts and status.' },
      { to: '/live', label: 'Connectors & Live Data', icon: 'cloud', description: 'Sources, feeds and live refresh.' },
      { to: '/capabilities', label: 'Capabilities', icon: 'cpu', description: 'What is implemented, partial or not implemented.' },
    ],
  },
]
