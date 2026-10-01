import type { CSSProperties } from 'react'
import type { IndexBar, BarScale } from '../../features/scenario/scenarioModel'
import { formatPct, formatValue } from '../../shared/format'

const LAB = 34
const GAP = 8

/** Grouped bars of scenario against baseline, indexed so the baseline is 100 for every result
 *  (reference "Baseline Vs Scenario"). Bar labels show the real values; the table beneath carries
 *  the same numbers for anyone who cannot read the chart. */
export function IndexedBars({ bars, scale }: { bars: IndexBar[]; scale: BarScale }) {
  const plot = (ratio: number) => `calc(${LAB + GAP}px + (var(--h) - ${LAB + 12 + GAP}px) * ${ratio})`
  return (
    <div className="bars-scroll">
      <div
        className="bars"
        role="img"
        aria-label={`Scenario against baseline for ${bars.length} results, indexed so the baseline equals 100`}
        style={{ '--n': bars.length, '--lab': `${LAB}px` } as CSSProperties}
        data-testid="indexed-bars"
      >
        <div className="gridline" style={{ bottom: plot(0) }}><span>0</span></div>
        <div className="gridline" style={{ bottom: plot(scale.baseline) }}><span>Baseline = 100</span></div>
        {bars.map((b, i) => {
          const rel = b.baseline === 0 ? null : (b.scenario - b.baseline) / b.baseline
          return (
            <div key={b.key} className="bargroup" data-testid="bar-group" title={`${b.label}: ${formatValue(b.baseline, b.unit)} → ${formatValue(b.scenario, b.unit)}${rel !== null ? ` (${formatPct(rel)})` : ''}`}>
              <div className="pair">
                <div className="bar base" style={{ height: `${scale.baseline * 100}%` }}><span className="bval">{formatValue(b.baseline, b.unit)}</span></div>
                <div className="bar scen" style={{ height: `${(scale.heights[i]?.scenario ?? 0) * 100}%` }}><span className="bval">{formatValue(b.scenario, b.unit)}</span></div>
              </div>
              <div className="blab">{b.label}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
