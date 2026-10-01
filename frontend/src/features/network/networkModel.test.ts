import { describe, expect, it } from 'vitest'
import type { HierarchyNode } from '../../api/types'
import { descendantPorts, filterNodes, flattenHierarchy, kindName, kindTone, LAYOUT, layoutHierarchy, nodeFacts, nodeRadius } from './networkModel'

const n = (id: string, kind: string, children: HierarchyNode[] = [], name = id): HierarchyNode => ({ id, key: `k-${id}`, name, kind, synthetic: true, children })

// network → national → { north → {eastmouth, northbay}, south → {southreach} } ; separate sandbox tree
const tree = [
  n('net', 'network', [n('nat', 'national_hub', [n('north', 'regional_hub', [n('east', 'port', [], 'Eastmouth port'), n('nb', 'port', [], 'Northbay port')]), n('south', 'regional_hub', [n('sr', 'port', [], 'Southreach port')])])]),
  n('sand', 'sandbox'),
]

describe('flattenHierarchy', () => {
  const flat = flattenHierarchy(tree)
  it('is depth-first with parents before members', () => {
    expect(flat.map((x) => x.id)).toEqual(['net', 'nat', 'north', 'east', 'nb', 'south', 'sr', 'sand'])
  })
  it('records depth and parent', () => {
    const nb = flat.find((x) => x.id === 'nb')!
    expect(nb).toMatchObject({ depth: 3, parentId: 'north', parentName: 'north' })
    expect(flat.find((x) => x.id === 'net')).toMatchObject({ depth: 0, parentId: null, parentName: null })
  })
})

describe('layoutHierarchy', () => {
  const layout = layoutHierarchy(tree)
  const at = (id: string) => layout.nodes.find((x) => x.id === id)!
  it('places every organization once', () => {
    expect(layout.nodes).toHaveLength(8)
    expect(new Set(layout.nodes.map((x) => x.id)).size).toBe(8)
  })
  it('puts depth on rows', () => {
    expect(at('east').y).toBe(at('nb').y)
    expect(at('nat').y - at('net').y).toBe(LAYOUT.yGap)
    expect(at('sand').y).toBe(at('net').y)
  })
  it('centres a parent over its members', () => {
    expect(at('north').x).toBeCloseTo((at('east').x + at('nb').x) / 2)
    expect(at('nat').x).toBeCloseTo((at('north').x + at('south').x) / 2)
    expect(at('south').x).toBeCloseTo(at('sr').x)
  })
  it('never overlaps two organizations on the same row', () => {
    const rows = new Map<number, number[]>()
    for (const x of layout.nodes) rows.set(x.y, [...(rows.get(x.y) ?? []), x.x])
    for (const xs of rows.values()) {
      const sorted = [...xs].sort((a, b) => a - b)
      for (let i = 1; i < sorted.length; i++) expect(sorted[i]! - sorted[i - 1]!).toBeGreaterThanOrEqual(LAYOUT.xGap * 0.5)
    }
  })
  it('keeps separate trees apart', () => {
    expect(at('sand').x).toBeGreaterThan(at('sr').x + LAYOUT.xGap - 1)
  })
  it('has one edge per member, trimmed to the circle edges', () => {
    expect(layout.edges).toHaveLength(6)
    const e = layout.edges.find((x) => x.id === 'north->east')!
    const north = at('north'), east = at('east')
    expect(Math.hypot(e.x1 - north.x, e.y1 - north.y)).toBeCloseTo(north.r)
    expect(Math.hypot(e.x2 - east.x, e.y2 - east.y)).toBeCloseTo(east.r)
  })
  it('fits everything inside its own bounds', () => {
    for (const x of layout.nodes) {
      expect(x.x - x.r).toBeGreaterThan(0)
      expect(x.x + x.r).toBeLessThan(layout.width)
      expect(x.y + x.r).toBeLessThan(layout.height)
    }
  })
  it('handles an empty hierarchy and a single node', () => {
    expect(layoutHierarchy([])).toMatchObject({ nodes: [], edges: [] })
    const one = layoutHierarchy([n('only', 'port')])
    expect(one.nodes).toHaveLength(1)
    expect(one.edges).toHaveLength(0)
  })
  it('is deterministic', () => {
    expect(layoutHierarchy(tree)).toEqual(layoutHierarchy(tree))
  })
})

describe('kinds', () => {
  it('names and sizes kinds', () => {
    expect(kindName('regional_hub')).toBe('Regional Hub')
    expect(kindName('weird')).toBe('Workspace')
    expect(nodeRadius('network')).toBeGreaterThan(nodeRadius('regional_hub'))
    expect(nodeRadius('regional_hub')).toBeGreaterThan(nodeRadius('port'))
  })
  it('colours ports as private zones and hubs as shared', () => {
    expect(kindTone('port')).toBe('info')
    expect(kindTone('regional_hub')).toBe('accent')
    expect(kindTone('network')).toBe('neutral')
    expect(['good', 'err']).not.toContain(kindTone('sandbox'))
  })
})

describe('filterNodes', () => {
  const flat = flattenHierarchy(tree)
  it('matches name, key and kind, case-insensitively', () => {
    expect(filterNodes(flat, 'east').map((x) => x.id)).toEqual(['east'])
    expect(filterNodes(flat, 'K-NB').map((x) => x.id)).toEqual(['nb'])
    expect(filterNodes(flat, 'regional').map((x) => x.id)).toEqual(['north', 'south'])
  })
  it('returns everything for a blank query and nothing for a miss', () => {
    expect(filterNodes(flat, '  ')).toHaveLength(8)
    expect(filterNodes(flat, 'zzz')).toHaveLength(0)
  })
})

describe('descendantPorts / nodeFacts', () => {
  const flat = flattenHierarchy(tree)
  it('counts ports beneath a node, not the node itself', () => {
    expect(descendantPorts(flat, 'net')).toBe(3)
    expect(descendantPorts(flat, 'north')).toBe(2)
    expect(descendantPorts(flat, 'east')).toBe(0)
  })
  it('states only what is known for a hub', () => {
    const f = nodeFacts(flat.find((x) => x.id === 'north')!, { nodes: flat, sharedWithYou: 2 })
    expect(f.map((x) => x.key)).toEqual(['Type', 'Part Of', 'Direct Members', 'Ports Beneath', 'Shared With You'])
    expect(f.find((x) => x.key === 'Shared With You')?.value).toBe('2 Outputs')
  })
  it('omits member counts for a port and shows reporting only when known', () => {
    const port = flat.find((x) => x.id === 'nb')!
    expect(nodeFacts(port, { nodes: flat, sharedWithYou: 1 }).map((x) => x.key)).toEqual(['Type', 'Part Of', 'Shared With You'])
    const withReporting = nodeFacts(port, { nodes: flat, sharedWithYou: 0, reporting: { shared: 3, total: 5 } })
    expect(withReporting.find((x) => x.key === 'Indicators Shared')?.value).toBe('3 Of 5')
    expect(withReporting.find((x) => x.key === 'Shared With You')?.value).toBe('0 Outputs')
  })
  it('never fabricates operational fields', () => {
    const keys = nodeFacts(flat[0]!, { nodes: flat, sharedWithYou: 0 }).map((x) => x.key.toLowerCase()).join(' ')
    expect(keys).not.toMatch(/throughput|berth|eta|status|vessel/)
  })
})
