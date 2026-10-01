import { describe, expect, it } from 'vitest'
import { NAV } from '../../app/nav'
import { ROUTES } from '../../app/routes'
import { initialNavOpen, initialsOf, resolveCrumb } from './shell'

describe('initialNavOpen', () => {
  it('defaults to expanded on desktop and honours the saved choice', () => {
    expect(initialNavOpen(null, true)).toBe(true)
    expect(initialNavOpen('1', true)).toBe(true)
    expect(initialNavOpen('0', true)).toBe(false)
  })
  it('always starts closed on small screens, whatever was saved', () => {
    expect(initialNavOpen(null, false)).toBe(false)
    expect(initialNavOpen('1', false)).toBe(false)
  })
  it('treats an unrecognised saved value as collapsed on desktop', () => {
    expect(initialNavOpen('banana', true)).toBe(false)
  })
})

describe('resolveCrumb', () => {
  it('finds the group and label for every nav item', () => {
    for (const g of NAV) {
      for (const item of g.items) {
        expect(resolveCrumb(item.to, NAV)).toEqual({ group: g.title, label: item.label })
      }
    }
  })
  it('matches nested paths and trailing slashes', () => {
    expect(resolveCrumb('/network/hub', NAV)?.label).toBe('Port Network')
    expect(resolveCrumb('/scenarios/', NAV)?.label).toBe('Scenario Comparison')
  })
  it('does not treat the root as a prefix of everything', () => {
    expect(resolveCrumb('/nope', NAV)).toBeNull()
  })
  it('does not match a path that merely shares a prefix', () => {
    expect(resolveCrumb('/decisions', NAV)).toBeNull()
  })
})

describe('navigation completeness', () => {
  it('has an icon for every item', () => {
    for (const g of NAV) for (const i of g.items) expect(i.icon).toBeTruthy()
  })
  it('every routed page is reachable from the navigation', () => {
    const navPaths = new Set(NAV.flatMap((g) => g.items.map((i) => i.to)))
    for (const r of ROUTES) expect(navPaths.has(r.path)).toBe(true)
  })
})

describe('initialsOf', () => {
  it('uses first and last initials', () => {
    expect(initialsOf('Maria del Carmen Ortiz')).toBe('MO')
    expect(initialsOf('Ada Lovelace')).toBe('AL')
  })
  it('handles one word, blanks and nullish', () => {
    expect(initialsOf('admin')).toBe('AD')
    expect(initialsOf('  ')).toBe('?')
    expect(initialsOf(null)).toBe('?')
    expect(initialsOf(undefined)).toBe('?')
  })
})
