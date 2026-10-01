/** Pure model for the network map. The map is drawn from the real organization hierarchy only:
 *  names, kinds and parent/child structure. There are no coordinates, throughput, berths or
 *  statuses in the platform, so none are invented — positions come from a tidy-tree layout. */
import type { HierarchyNode } from '../../api/types'
import type { PillTone } from '../../components/ui/Pill'

export interface FlatNode {
  id: string
  key: string
  name: string
  kind: string
  synthetic: boolean
  depth: number
  parentId: string | null
  parentName: string | null
  childIds: string[]
}

/** Depth-first, so a parent always precedes its members. */
export function flattenHierarchy(roots: HierarchyNode[]): FlatNode[] {
  const out: FlatNode[] = []
  const walk = (n: HierarchyNode, depth: number, parent: HierarchyNode | null) => {
    out.push({
      id: n.id, key: n.key, name: n.name, kind: n.kind, synthetic: n.synthetic, depth,
      parentId: parent?.id ?? null, parentName: parent?.name ?? null, childIds: n.children.map((c) => c.id),
    })
    n.children.forEach((c) => walk(c, depth + 1, n))
  }
  roots.forEach((r) => walk(r, 0, null))
  return out
}

export const KIND_LABEL: Record<string, string> = {
  port: 'Port',
  regional_hub: 'Regional Hub',
  national_hub: 'National Hub',
  network: 'Port Network',
  sandbox: 'Engine Sandbox',
}
export const kindName = (kind: string): string => KIND_LABEL[kind] ?? 'Workspace'

/** Colour class suffix per kind (see network.css). Ports are private zones (info), hubs are shared (accent). */
export function kindTone(kind: string): PillTone {
  switch (kind) {
    case 'port': return 'info'
    case 'regional_hub':
    case 'national_hub': return 'accent'
    case 'sandbox': return 'warn'
    default: return 'neutral'
  }
}

/** Larger marks for organizations that sit higher in the structure. */
export function nodeRadius(kind: string): number {
  switch (kind) {
    case 'network': return 24
    case 'national_hub': return 21
    case 'regional_hub': return 18
    default: return 15
  }
}

export function filterNodes(nodes: FlatNode[], query: string): FlatNode[] {
  const q = query.trim().toLowerCase()
  if (!q) return nodes
  return nodes.filter((n) => n.name.toLowerCase().includes(q) || n.key.toLowerCase().includes(q) || kindName(n.kind).toLowerCase().includes(q))
}

/** Ports beneath a node (the node itself is not counted). */
export function descendantPorts(nodes: FlatNode[], id: string): number {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  let count = 0
  const walk = (nid: string) => {
    for (const c of byId.get(nid)?.childIds ?? []) {
      if (byId.get(c)?.kind === 'port') count += 1
      walk(c)
    }
  }
  walk(id)
  return count
}

// ---------------------------------------------------------------- layout

export interface LaidNode extends FlatNode {
  x: number
  y: number
  r: number
}
export interface LaidEdge {
  id: string
  from: string
  to: string
  x1: number
  y1: number
  x2: number
  y2: number
}
export interface NetworkLayout {
  nodes: LaidNode[]
  edges: LaidEdge[]
  width: number
  height: number
}

export const LAYOUT = { xGap: 170, yGap: 125, treeGap: 40, pad: 64 } as const

/** Tidy top-down tree: leaves take consecutive slots, a parent sits centred over its members,
 *  separate trees are placed side by side. Deterministic and overlap-free by construction. */
export function layoutHierarchy(roots: HierarchyNode[]): NetworkLayout {
  const flat = flattenHierarchy(roots)
  if (!flat.length) return { nodes: [], edges: [], width: 2 * LAYOUT.pad, height: 2 * LAYOUT.pad }

  const byId = new Map(flat.map((n) => [n.id, n]))
  const xOf = new Map<string, number>()
  let slot = 0
  const place = (id: string): number => {
    const n = byId.get(id)!
    if (!n.childIds.length) {
      const x = slot * LAYOUT.xGap
      slot += 1
      xOf.set(id, x)
      return x
    }
    const xs = n.childIds.map(place)
    const x = (xs[0]! + xs[xs.length - 1]!) / 2
    xOf.set(id, x)
    return x
  }
  roots.forEach((r, i) => {
    if (i > 0) slot += LAYOUT.treeGap / LAYOUT.xGap
    place(r.id)
  })

  const maxDepth = Math.max(...flat.map((n) => n.depth))
  const nodes: LaidNode[] = flat.map((n) => ({
    ...n,
    x: LAYOUT.pad + xOf.get(n.id)!,
    y: LAYOUT.pad + n.depth * LAYOUT.yGap,
    r: nodeRadius(n.kind),
  }))
  const pos = new Map(nodes.map((n) => [n.id, n]))

  const edges: LaidEdge[] = []
  for (const n of nodes) {
    if (!n.parentId) continue
    const p = pos.get(n.parentId)!
    const dx = n.x - p.x
    const dy = n.y - p.y
    const len = Math.hypot(dx, dy) || 1
    edges.push({
      id: `${p.id}->${n.id}`, from: p.id, to: n.id,
      x1: p.x + (dx / len) * p.r, y1: p.y + (dy / len) * p.r,
      x2: n.x - (dx / len) * n.r, y2: n.y - (dy / len) * n.r,
    })
  }

  const maxX = Math.max(...nodes.map((n) => n.x))
  return { nodes, edges, width: maxX + LAYOUT.pad, height: 2 * LAYOUT.pad + maxDepth * LAYOUT.yGap + 18 }
}

// ---------------------------------------------------------------- selected-node facts

export interface Fact {
  key: string
  value: string
}

/** What the platform actually knows about one organization, for the detail card. */
export function nodeFacts(node: FlatNode, ctx: {
  nodes: FlatNode[]
  /** How many of the hub's indicators this organization has shared, when the viewed hub covers it. */
  reporting?: { shared: number; total: number } | null
  /** Outputs this organization has approved for the organization you are viewing as. */
  sharedWithYou: number
}): Fact[] {
  const facts: Fact[] = [{ key: 'Type', value: kindName(node.kind) }]
  facts.push({ key: 'Part Of', value: node.parentName ?? 'Top Level' })
  if (node.childIds.length) {
    facts.push({ key: 'Direct Members', value: String(node.childIds.length) })
    facts.push({ key: 'Ports Beneath', value: String(descendantPorts(ctx.nodes, node.id)) })
  }
  if (ctx.reporting) facts.push({ key: 'Indicators Shared', value: `${ctx.reporting.shared} Of ${ctx.reporting.total}` })
  facts.push({ key: 'Shared With You', value: `${ctx.sharedWithYou} Output${ctx.sharedWithYou === 1 ? '' : 's'}` })
  return facts
}
