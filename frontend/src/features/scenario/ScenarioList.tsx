import { Fragment, useState } from 'react'
import { Link } from 'react-router'
import type { ScenarioView } from '../../api/types'
import { deltaClass } from '../../components/data/delta'
import { Button, buttonClass } from '../../components/ui/Button'
import { Icon } from '../../components/ui/Icon'
import { Pill } from '../../components/ui/Pill'
import { formatDateTime, formatPct, formatTransition } from '../../shared/format'
import { Card, EmptyState, StatusBadge } from '../../shared/ui'
import { overrideChange } from './scenarioModel'

/** Scenarios derived from the selected baseline (reference "Scenarios" table). A row opens to the
 *  assumptions that scenario changes; the active scenario is marked. */
export function ScenarioList({ scenarios, selectedId, onSelect, onNew }: {
  scenarios: ScenarioView[]
  selectedId: string | undefined
  onSelect: (id: string) => void
  onNew: () => void
}) {
  const [open, setOpen] = useState<Set<string>>(new Set())
  const toggle = (id: string) => setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })

  if (!scenarios.length) {
    return (
      <Card title="Scenarios">
        <EmptyState title="No Scenario Yet" action={<Button variant="primary" icon="plus" onClick={onNew}>New Scenario</Button>}>
          Create a scenario to test a change against this baseline.
        </EmptyState>
      </Card>
    )
  }

  return (
    <Card flush title={<><Icon name="layers" size={15} /> Scenarios</>} subtitle={`${scenarios.length} Derived From This Baseline · Select One To Edit And Compare`}>
      <table className="grid" data-testid="scenario-list">
        <thead>
          <tr><th style={{ width: 26 }} /><th>Name</th><th>Status</th><th>Last Run</th><th className="right">Assumptions Changed</th></tr>
        </thead>
        <tbody>
          {scenarios.map((s) => {
            const isOpen = open.has(s.id)
            const active = s.id === selectedId
            return (
              <Fragment key={s.id}>
                <tr className={`rowx ${isOpen ? 'open' : ''} ${active ? 'selrow' : ''}`.trim()} onClick={() => toggle(s.id)} data-testid="scenario-row" data-id={s.id}>
                  <td>
                    <button type="button" className="caret-btn" aria-expanded={isOpen} aria-label={`${isOpen ? 'Hide' : 'Show'} assumptions of ${s.name}`}>
                      <span className="caret"><Icon name="chevr" size={14} /></span>
                    </button>
                  </td>
                  <td>
                    <div className="cellmain row" style={{ gap: 8 }}>{active ? <span className="sel-dot" title="Selected scenario" /> : null}{s.name}</div>
                    {s.description ? <div className="sub">{s.description}</div> : null}
                  </td>
                  <td><StatusBadge status={s.run?.status ?? s.status} /></td>
                  <td className="num">{s.run ? formatDateTime(s.run.finished_at ?? s.run.started_at) : 'Not Run Yet'}</td>
                  <td className="right num">{s.overrides.length}</td>
                </tr>
                {isOpen ? (
                  <tr className="subrow" data-testid="scenario-sub">
                    <td colSpan={5}>
                      <div className="subinner">
                        <div className="sect-head" style={{ marginBottom: 10 }}>Assumption Changes</div>
                        {s.overrides.length ? (
                          <table className="grid" style={{ background: 'transparent' }}>
                            <thead><tr><th>Assumption</th><th className="right">Baseline Value → Scenario Value</th><th className="right">Delta</th></tr></thead>
                            <tbody>
                              {s.overrides.map((o) => {
                                const { rel } = overrideChange(o)
                                return (
                                  <tr key={o.id}>
                                    <td><div className="cellmain">{o.field_label}</div><div className="sub">{o.dataset_label}</div></td>
                                    <td className="right num strong">{formatTransition(o.baseline_value, o.value, o.unit)}</td>
                                    <td className="right">{rel !== null ? <span className={`delta ${deltaClass(rel > 0 ? 'increase' : rel < 0 ? 'decrease' : 'none')}`}>{formatPct(rel)}</span> : <span className="delta flat">—</span>}</td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                        ) : <p className="muted small">This scenario does not change any assumption yet.</p>}
                        <div className="row" style={{ marginTop: 12, gap: 8 }}>
                          {active ? <Pill tone="accent" dot>Selected</Pill> : <Button size="xs" variant="outline" onClick={() => onSelect(s.id)}>Select This Scenario</Button>}
                          {active ? <Link className={buttonClass('primary', 'xs')} to="/decision"><Icon name="target" size={13} /> Open In Decision Overview</Link> : null}
                          {active ? <Link className={buttonClass('outline', 'xs')} to="/impact"><Icon name="bars" size={13} /> View Impact</Link> : null}
                        </div>
                      </div>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </Card>
  )
}
