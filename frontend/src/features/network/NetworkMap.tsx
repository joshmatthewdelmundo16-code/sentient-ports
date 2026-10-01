import { useMemo, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { Link } from 'react-router'
import type { HierarchyNode } from '../../api/types'
import { buttonClass } from '../../components/ui/Button'
import { Icon } from '../../components/ui/Icon'
import { Pill } from '../../components/ui/Pill'
import { descendantPorts, filterNodes, flattenHierarchy, kindName, kindTone, layoutHierarchy, nodeFacts, type FlatNode } from './networkModel'

const ZOOM_MIN = 0.6
const ZOOM_MAX = 2.2
const ZOOM_STEP = 1.25

const trim = (s: string, n = 24) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)

/** Interactive map of the real organization hierarchy (reference "Port Network"): searchable list on
 *  the left, a canvas of organizations and the links between them, zoom controls, a legend, a hover
 *  card and a detail card for the selection. Only recorded facts are shown. The detail card and
 *  legend live in their own column beside the drawing, so they never cover an organization. */
export function NetworkMap({ roots, scopeId, reporting, sharedWithYou }: {
  roots: HierarchyNode[]
  /** The organization you are viewing as — marked on the map. */
  scopeId: string | undefined
  reporting: Map<string, { shared: number; total: number }>
  sharedWithYou: Map<string, number>
}) {
  const flat = useMemo(() => flattenHierarchy(roots), [roots])
  const layout = useMemo(() => layoutHierarchy(roots), [roots])
  const [picked, setPicked] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [zoom, setZoom] = useState(1)
  const [tip, setTip] = useState<{ id: string; x: number; y: number } | null>(null)

  const selected: FlatNode | undefined = flat.find((n) => n.id === picked) ?? flat.find((n) => n.id === scopeId) ?? flat[0]
  const visible = filterNodes(flat, query)
  const kindsPresent = [...new Set(flat.map((n) => n.kind))]
  const tipNode = tip ? layout.nodes.find((n) => n.id === tip.id) : undefined

  const select = (id: string) => setPicked(id)
  const onKey = (e: KeyboardEvent, id: string) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      select(id)
    }
  }
  const onMove = (e: MouseEvent<SVGGElement>, id: string) => {
    const box = e.currentTarget.ownerSVGElement?.parentElement?.getBoundingClientRect()
    if (!box) return
    setTip({ id, x: Math.max(8, Math.min(e.clientX - box.left + 16, box.width - 190)), y: Math.max(8, e.clientY - box.top - 10) })
  }

  const cx = layout.width / 2
  const cy = layout.height / 2

  return (
    <div className="net-panel" data-testid="network-map">
      <div className="net-wrap">
        <div className="net-side">
          <div className="ns-head">
            <div className="ns-title">Organizations <Pill tone="neutral">{flat.length} Nodes</Pill></div>
            <div className="search">
              <span className="ic"><Icon name="search" size={14} /></span>
              <input className="input" placeholder="Search Organizations..." aria-label="Search Organizations" value={query} onChange={(e) => setQuery(e.target.value)} data-testid="network-search" />
            </div>
          </div>
          <div className="ns-list">
            {visible.length === 0 ? (
              <div className="state compact"><p>No Organizations Match “{query.trim()}”.</p></div>
            ) : visible.map((n) => (
              <button
                key={n.id}
                type="button"
                className={`port-item k-${kindTone(n.kind)} ${n.id === selected?.id ? 'sel' : ''}`.trim()}
                style={{ paddingLeft: 16 + Math.min(n.depth, 3) * 8 }}
                aria-current={n.id === selected?.id ? 'true' : undefined}
                onClick={() => select(n.id)}
                data-testid="network-item"
              >
                <span className="pdot" aria-hidden="true" />
                <span className="pi-main">
                  <span className="pi-name">{n.name} <span className="pi-code">{n.key}</span></span>
                  <span className="pi-sub" style={{ display: 'block' }}>{kindName(n.kind)}{n.id === scopeId ? ' · Viewing As' : ''}</span>
                </span>
                {n.childIds.length ? <span className="pi-tp">{descendantPorts(flat, n.id)}<small>Ports</small></span> : null}
              </button>
            ))}
          </div>
        </div>

        <div className="net-canvas-wrap">
          <div className="net-overlay">
            {selected ? (
              <div className={`net-detail k-${kindTone(selected.kind)}`} data-testid="network-detail">
                <div className="nd-head">
                  <div className="nd-name">{selected.name} <span className="pi-code" style={{ fontFamily: 'var(--mono)', fontSize: 9.5, fontWeight: 400 }}>{selected.key}</span></div>
                  <div className="nd-pills">
                    <Pill tone={kindTone(selected.kind)}>{kindName(selected.kind)}</Pill>
                    {selected.synthetic ? <Pill tone="warn" title="Synthetic demonstration data — not a real organization">Synthetic</Pill> : null}
                    {selected.id === scopeId ? <Pill tone="accent" dot>Viewing As</Pill> : null}
                  </div>
                </div>
                <div className="nd-body">
                  <div className="meta-list">
                    {nodeFacts(selected, { nodes: flat, reporting: reporting.get(selected.id) ?? null, sharedWithYou: sharedWithYou.get(selected.id) ?? 0 }).map((f) => (
                      <div key={f.key} className="mrow"><span className="mk">{f.key}</span><span className="mv num">{f.value}</span></div>
                    ))}
                  </div>
                  {selected.id === scopeId ? <Link className={buttonClass('primary', 'sm', true)} to="/governance"><Icon name="shield" size={14} /> Manage Sharing</Link> : null}
                </div>
              </div>
            ) : null}

            <div className="net-legend">
              {kindsPresent.map((k) => (
                <div key={k} className={`nl k-${kindTone(k)}`}><i /> {kindName(k)}</div>
              ))}
              {scopeId && flat.some((n) => n.id === scopeId) ? <div className="nl"><i className="ring" /> You Are Viewing As This Organization</div> : null}
              <div className="nl note">Node Size · Position In The Hierarchy</div>
            </div>
          </div>

          <div className="net-stage">
            <svg className="net-svg" viewBox={`0 0 ${layout.width} ${layout.height}`} preserveAspectRatio="xMidYMid meet" role="group" aria-label="Organization hierarchy map" data-testid="network-svg">
              <g className="net-g" transform={`translate(${cx},${cy}) scale(${zoom}) translate(${-cx},${-cy})`} data-zoom={zoom}>
                <g>
                  {layout.edges.map((e) => {
                    const hot = selected && (e.from === selected.id || e.to === selected.id)
                    return <line key={e.id} className={`net-edge ${hot ? 'hot' : selected ? 'dim' : ''}`.trim()} x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2} data-testid="network-edge" />
                  })}
                </g>
                <g>
                  {layout.nodes.map((n) => (
                    <g
                      key={n.id}
                      className={`net-node k-${kindTone(n.kind)} ${n.id === selected?.id ? 'sel' : ''}`.trim()}
                      tabIndex={0}
                      role="button"
                      aria-label={`${n.name}, ${kindName(n.kind)}`}
                      aria-pressed={n.id === selected?.id}
                      onClick={() => select(n.id)}
                      onKeyDown={(e) => onKey(e, n.id)}
                      onMouseMove={(e) => onMove(e, n.id)}
                      onMouseLeave={() => setTip(null)}
                      data-testid="network-node"
                      data-id={n.id}
                    >
                      <circle className="halo" cx={n.x} cy={n.y} r={n.r + 9} />
                      <circle className="body" cx={n.x} cy={n.y} r={n.r} />
                      <circle className="core" cx={n.x} cy={n.y} r={Math.max(3, n.r * 0.34)} />
                      {n.id === scopeId ? <circle className="you-ring" cx={n.x} cy={n.y} r={n.r} data-testid="network-you" /> : null}
                      <text className="net-label" x={n.x} y={n.y + n.r + 15} textAnchor="middle">{trim(n.name)}</text>
                    </g>
                  ))}
                </g>
              </g>
            </svg>

            {tipNode ? (
              <div className={`net-tip k-${kindTone(tipNode.kind)}`} style={{ left: tip!.x, top: tip!.y }} role="presentation" data-testid="network-tip">
                <div className="nt-name"><i />{tipNode.name}</div>
                <div className="nt-grid">
                  <span className="k">Type</span><span className="v">{kindName(tipNode.kind)}</span>
                  <span className="k">Members</span><span className="v">{tipNode.childIds.length}</span>
                </div>
              </div>
            ) : null}
          </div>

          <div className="net-ctrls">
            <button type="button" aria-label="Zoom In" disabled={zoom >= ZOOM_MAX} onClick={() => setZoom((z) => Math.min(ZOOM_MAX, z * ZOOM_STEP))}><Icon name="plus" size={15} /></button>
            <button type="button" aria-label="Zoom Out" disabled={zoom <= ZOOM_MIN} onClick={() => setZoom((z) => Math.max(ZOOM_MIN, z / ZOOM_STEP))}><Icon name="minus" size={15} /></button>
            <button type="button" aria-label="Reset View" onClick={() => setZoom(1)}><Icon name="cross" size={15} /></button>
          </div>
        </div>
      </div>
    </div>
  )
}
