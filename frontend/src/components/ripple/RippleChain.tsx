import { Fragment } from 'react'
import { Icon } from '../ui/Icon'
import { RIPPLE_LEGEND, RippleLegend } from './RippleLegend'
import type { RippleNode, RippleStage } from './rippleModel'

function Node({ node, selected, onSelect }: { node: RippleNode; selected: boolean; onSelect: (n: RippleNode) => void }) {
  return (
    <button
      type="button"
      className={`rnode ${node.kind} ${selected ? 'sel' : ''}`.trim()}
      aria-pressed={selected}
      aria-label={`${node.name}: trace provenance`}
      onClick={() => onSelect(node)}
      data-testid="ripple-node"
      data-kind={node.kind}
    >
      <span className="rn-ic"><Icon name={node.icon} size={16} /></span>
      <span className="rn-main">
        <span className="rn-name">{node.name}</span>
        <span className="rn-sub">{node.sub}</span>
      </span>
      {node.delta ? (
        <span className="rn-delta">
          <span className={`delta ${node.delta.cls}`}>{node.delta.text}</span>
          <span className="sub">{node.delta.label}</span>
        </span>
      ) : null}
    </button>
  )
}

/** Change-propagation chain (reference ripple): stage labels on the left, nodes on the right,
 *  chevron connectors between stages. A node opens its provenance. `pulse` > 0 plays the
 *  recalculation animation; bumping it replays it (the chain remounts, restarting the CSS animation). */
export function RippleChain({ stages, selectedId, onSelect, pulse }: {
  stages: RippleStage[]
  selectedId: string | null
  onSelect: (node: RippleNode) => void
  pulse: number
}) {
  return (
    <div className="ripple">
      <RippleLegend items={RIPPLE_LEGEND} hint="Click Any Node To Trace Provenance" />
      <div key={pulse} className={`chain ${pulse > 0 ? 'recalc' : ''}`.trim()} data-testid="ripple-chain">
        {stages.map((stage, i) => (
          <Fragment key={stage.kind}>
            {i > 0 ? <div className="stage-connector" aria-hidden="true"><Icon name="chevd" size={16} /></div> : null}
            <div className="chain-stage" data-stage={stage.kind}>
              <div className="stage-lab">{stage.label}</div>
              <div className="chain-nodes" role="group" aria-label={stage.label}>
                {stage.nodes.map((n) => <Node key={n.id} node={n} selected={n.id === selectedId} onSelect={onSelect} />)}
              </div>
            </div>
          </Fragment>
        ))}
      </div>
    </div>
  )
}
