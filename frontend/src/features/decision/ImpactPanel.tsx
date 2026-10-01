import { useMemo, useState } from 'react'
import type { Metric, RunBrief } from '../../api/types'
import { deltaClass, directionPill } from '../../components/data/delta'
import { Icon } from '../../components/ui/Icon'
import { Pill } from '../../components/ui/Pill'
import { Switch } from '../../components/ui/Switch'
import { formatDateTime, formatDelta, formatPct, formatValue } from '../../shared/format'
import { Card } from '../../shared/ui'

/** Results first (the headline outputs), then the rest in contract order. */
export function orderMetrics(metrics: Metric[], showUnchanged: boolean): Metric[] {
  const list = showUnchanged ? metrics : metrics.filter((m) => m.changed)
  return [...list].sort((a, b) => Number(b.terminal) - Number(a.terminal) || a.field_order - b.field_order)
}

/** Right panel: every recorded metric, baseline against scenario (reference "Live Decision
 *  Impact"). Values come from the recorded results of the two runs. Status says what happened
 *  (increased / decreased / no change); it never says whether that is good or bad. */
export function ImpactPanel({ metrics, run }: { metrics: Metric[]; run: RunBrief | null }) {
  const [showUnchanged, setShowUnchanged] = useState(false)
  const rows = useMemo(() => orderMetrics(metrics, showUnchanged), [metrics, showUnchanged])
  const changed = metrics.filter((m) => m.changed).length

  return (
    <Card
      flush
      title={<><Icon name="gauge" size={15} /> Live Decision Impact</>}
      subtitle={`${changed} Of ${metrics.length} Recorded Results Changed · Recomputed On Run`}
      actions={
        <>
          {run ? <Pill tone="neutral">Last Run {formatDateTime(run.finished_at)}</Pill> : <Pill tone="neutral">Not Run Yet</Pill>}
          <Switch checked={showUnchanged} onChange={setShowUnchanged} label="Show Unchanged" />
        </>
      }
    >
      <div className="impact-scroll">
        <table className="grid" data-testid="impact-table">
          <thead>
            <tr><th>Metric</th><th className="right">Baseline</th><th className="right">Scenario</th><th className="right">Delta</th><th>Status</th></tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={5} className="muted">No metric changed. Turn on Show Unchanged to see every value.</td></tr>
            ) : rows.map((m) => {
              const numeric = m.kind === 'numeric'
              const pill = directionPill(m.direction, m.changed, numeric)
              return (
                <tr key={`${m.dataset_id}:${m.field}`}>
                  <td>
                    <div className="cellmain">{m.field_label} {m.terminal ? <span className="tag">Result</span> : null}</div>
                    <div className="sub">{m.dataset_label}</div>
                  </td>
                  <td className="right num">{formatValue(m.baseline, m.unit)}</td>
                  <td className="right num strong">{formatValue(m.scenario, m.unit)}</td>
                  <td className="right">
                    {numeric && m.changed ? (
                      <>
                        <span className={`delta ${deltaClass(m.direction)}`}>{m.relative_delta !== null ? formatPct(m.relative_delta) : formatDelta(m.absolute_delta, m.unit)}</span>
                        {m.relative_delta !== null ? <div className="sub num">{formatDelta(m.absolute_delta, m.unit)}</div> : null}
                      </>
                    ) : <span className="delta flat">{m.changed ? 'Changed' : '—'}</span>}
                  </td>
                  <td><Pill tone={pill.tone} dot>{pill.label}</Pill></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
