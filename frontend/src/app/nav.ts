/** Navigation — the product's information architecture, in workflow order. All labels are
 *  sentence case. `step` numbers the primary decision workflow. */
export interface NavItem {
  to: string
  label: string
  step?: number
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
      { to: '/', label: 'Start here', step: 1, description: 'What this platform does and where you are in the workflow.' },
      { to: '/decision', label: 'Decision overview', step: 2, description: 'Baseline, scenario, what changed and the resulting impact.' },
      { to: '/scenarios', label: 'Scenario comparison', step: 3, description: 'Change assumptions, run the scenario and compare every metric.' },
      { to: '/excel', label: 'Assumptions & Excel', step: 4, description: 'Upload a workbook, validate it and commit the change.' },
    ],
  },
  {
    title: 'Understand',
    items: [
      { to: '/impact', label: 'Impact & why', step: 5, description: 'The models a change flowed through, field by field.' },
      { to: '/sources', label: 'Sources & provenance', step: 6, description: 'Where every value came from.' },
      { to: '/activity', label: 'Activity', step: 7, description: 'What happened, in plain language.' },
      { to: '/execution', label: 'Execution & governance', step: 8, description: 'Runs, steps, and what is shared.' },
    ],
  },
  {
    title: 'Share',
    items: [
      { to: '/governance', label: 'Governance', step: 9, description: 'Approvals, exposure boundary and audit trail.' },
      { to: '/network', label: 'Port network', step: 10, description: 'Hub views built only from approved outputs.' },
      { to: '/collaboration', label: 'Collaboration', description: 'Shared decision cases between organizations.' },
    ],
  },
  {
    title: 'Plan',
    items: [
      { to: '/planning', label: 'Master planning', description: 'Multi-year plans with investments and capacity.' },
      { to: '/optimization', label: 'Optimization', description: 'Decision variables, constraints and trade-offs.' },
      { to: '/models', label: 'Model library', description: 'Models, providers, contracts and status.' },
      { to: '/live', label: 'Connectors & live data', description: 'Sources, feeds and live refresh.' },
      { to: '/capabilities', label: 'Capabilities', description: 'What is implemented, partial or not implemented.' },
    ],
  },
]
