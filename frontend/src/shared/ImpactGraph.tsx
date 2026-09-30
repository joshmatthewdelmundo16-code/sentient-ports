/**
 * Dependency / impact graph, laid out in layers from the real federation map:
 *   source datasets → models (by dependency depth) → terminal outputs.
 * When an explanation is supplied, the causal path is highlighted and everything off the
 * path is dimmed — the same field-level trace the "why" panel describes in words.
 */
import { useMemo } from 'react'
import type { Explanation, FederationMap } from '../api/types'

interface NodeBox {
  id: string
  kind: 'source' | 'model' | 'output'
  label: string
  sub?: string
  col: number
  row: number
  affected: boolean
  dim: boolean
}

interface EdgeLine {
  id: string
  from: string
  to: string
  label?: string
  affected: boolean
}

const NODE_W = 188
const NODE_H = 46
const COL_GAP = 70
const ROW_GAP = 22

export function upstreamClosure(map: FederationMap, target: string): Set<string> {
  const producers = new Map<string, string[]>()
  for (const w of map.writes) producers.set(w.dataset_id, [...(producers.get(w.dataset_id) ?? []), w.version_id])
  const seen = new Set<string>([target])
  const stack = [target]
  while (stack.length) {
    const v = stack.pop() as string
    for (const r of map.reads.filter((x) => x.version_id === v)) {
      for (const p of producers.get(r.dataset_id) ?? []) {
        if (!seen.has(p)) {
          seen.add(p)
          stack.push(p)
        }
      }
    }
  }
  return seen
}

/** Source datasets (read, never produced) feeding the target's upstream closure. */
export function sourcesFor(map: FederationMap, target: string | null | undefined): Set<string> {
  if (!target) return new Set()
  const versions = upstreamClosure(map, target)
  const produced = new Set(map.writes.map((w) => w.dataset_id))
  return new Set(map.reads.filter((r) => versions.has(r.version_id) && !produced.has(r.dataset_id)).map((r) => r.dataset_id))
}

export function layoutGraph(map: FederationMap, focusVersionId?: string | null, explanation?: Explanation | null) {
  const versions = focusVersionId ? upstreamClosure(map, focusVersionId) : new Set(map.models.map((m) => m.version_id))
  const models = map.models.filter((m) => versions.has(m.version_id))
  const dsLabel = new Map(map.datasets.map((d) => [d.id, d.label]))
  const producers = new Map<string, string>()
  for (const w of map.writes) if (versions.has(w.version_id)) producers.set(w.dataset_id, w.version_id)
  const reads = map.reads.filter((r) => versions.has(r.version_id))

  const affectedModels = new Set(explanation?.path.map((p) => p.version_id) ?? [])
  const changedSources = new Set(explanation?.changes.map((c) => c.dataset_id) ?? [])
  const changedOutputs = new Set(explanation?.path.flatMap((p) => p.outputs_changed.map((o) => o.dataset_id)) ?? [])
  const highlight = Boolean(explanation)

  // Depth of each model = 1 + deepest producer among its inputs.
  const depth = new Map<string, number>()
  const visit = (v: string, trail: Set<string>): number => {
    const known = depth.get(v)
    if (known !== undefined) return known
    if (trail.has(v)) return 1
    trail.add(v)
    let d = 1
    for (const r of reads.filter((x) => x.version_id === v)) {
      const p = producers.get(r.dataset_id)
      if (p && p !== v) d = Math.max(d, visit(p, trail) + 1)
    }
    depth.set(v, d)
    return d
  }
  models.forEach((m) => visit(m.version_id, new Set()))

  const nodes: NodeBox[] = []
  const edges: EdgeLine[] = []
  const sourceIds = [...new Set(reads.map((r) => r.dataset_id).filter((d) => !producers.has(d)))]
  sourceIds.forEach((d, i) =>
    nodes.push({
      id: `ds:${d}`, kind: 'source', label: dsLabel.get(d) ?? 'Dataset', sub: 'Source data', col: 0, row: i,
      affected: changedSources.has(d), dim: highlight && !changedSources.has(d),
    }),
  )
  const byDepth = new Map<number, typeof models>()
  for (const m of models) byDepth.set(depth.get(m.version_id) ?? 1, [...(byDepth.get(depth.get(m.version_id) ?? 1) ?? []), m])
  const maxDepth = Math.max(1, ...byDepth.keys())
  for (const [d, ms] of byDepth) {
    ms.sort((a, b) => a.name.localeCompare(b.name)).forEach((m, i) =>
      nodes.push({
        id: `m:${m.version_id}`, kind: 'model', label: m.name, sub: m.domain ? `${m.domain[0]?.toUpperCase()}${m.domain.slice(1)} model` : 'Model',
        col: d, row: i, affected: affectedModels.has(m.version_id), dim: highlight && !affectedModels.has(m.version_id),
      }),
    )
  }
  // Terminal outputs: written but not read inside the focused graph.
  const readSet = new Set(reads.map((r) => r.dataset_id))
  const terminals = [...producers.keys()].filter((d) => !readSet.has(d))
  terminals.forEach((d, i) =>
    nodes.push({
      id: `ds:${d}`, kind: 'output', label: dsLabel.get(d) ?? 'Dataset', sub: 'Result', col: maxDepth + 1, row: i,
      affected: changedOutputs.has(d), dim: highlight && !changedOutputs.has(d),
    }),
  )

  for (const r of reads) {
    const p = producers.get(r.dataset_id)
    const from = p ? `m:${p}` : `ds:${r.dataset_id}`
    const to = `m:${r.version_id}`
    const pathStep = explanation?.path.find((s) => s.version_id === r.version_id)
    const affected = Boolean(pathStep?.reads_changed.some((f) => f.dataset_id === r.dataset_id))
    edges.push({ id: `${from}->${to}:${r.dataset_id}`, from, to, label: p ? dsLabel.get(r.dataset_id) : undefined, affected })
  }
  for (const d of terminals) {
    const p = producers.get(d)
    if (p) edges.push({ id: `m:${p}->ds:${d}`, from: `m:${p}`, to: `ds:${d}`, affected: changedOutputs.has(d) && affectedModels.has(p) })
  }
  return { nodes, edges, cols: maxDepth + 2 }
}

export function ImpactGraph({ map, focusVersionId, explanation }: {
  map: FederationMap
  focusVersionId?: string | null
  explanation?: Explanation | null
}) {
  const { nodes, edges, cols } = useMemo(() => layoutGraph(map, focusVersionId, explanation), [map, focusVersionId, explanation])
  const rowsPerCol = new Map<number, number>()
  nodes.forEach((n) => rowsPerCol.set(n.col, Math.max(rowsPerCol.get(n.col) ?? 0, n.row + 1)))
  const maxRows = Math.max(1, ...rowsPerCol.values())
  const width = cols * NODE_W + (cols - 1) * COL_GAP + 24
  const height = maxRows * NODE_H + (maxRows - 1) * ROW_GAP + 24
  const pos = new Map<string, { x: number; y: number }>()
  for (const n of nodes) {
    const rows = rowsPerCol.get(n.col) ?? 1
    const offset = ((maxRows - rows) * (NODE_H + ROW_GAP)) / 2
    pos.set(n.id, { x: 12 + n.col * (NODE_W + COL_GAP), y: 12 + offset + n.row * (NODE_H + ROW_GAP) })
  }
  if (!nodes.length) return null
  return (
    <div className="graph-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', minWidth: Math.min(width, 760), height: 'auto', display: 'block' }} role="img" aria-label="Model dependency graph">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--line-strong)" />
          </marker>
          <marker id="arrow-hot" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--up)" />
          </marker>
        </defs>
        {edges.map((e) => {
          const a = pos.get(e.from)
          const b = pos.get(e.to)
          if (!a || !b) return null
          const x1 = a.x + NODE_W
          const y1 = a.y + NODE_H / 2
          const x2 = b.x
          const y2 = b.y + NODE_H / 2
          const mx = (x1 + x2) / 2
          return (
            <path key={e.id} className={`graph-edge ${e.affected ? 'affected' : ''}`}
              d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2 - 2} ${y2}`}
              markerEnd={`url(#${e.affected ? 'arrow-hot' : 'arrow'})`}>
              {e.label ? <title>{e.label}</title> : null}
            </path>
          )
        })}
        {nodes.map((n) => {
          const p = pos.get(n.id)
          if (!p) return null
          return (
            <g key={n.id} className={`graph-node ${n.kind} ${n.affected ? 'affected' : ''} ${n.dim ? 'dim' : ''}`}
              transform={`translate(${p.x},${p.y})`}>
              <rect width={NODE_W} height={NODE_H} rx={8} />
              <text x={12} y={19}>{n.label.length > 26 ? `${n.label.slice(0, 25)}…` : n.label}</text>
              <text x={12} y={35} className="sub">{n.sub}</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
