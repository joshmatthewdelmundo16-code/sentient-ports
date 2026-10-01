import { describe, expect, it } from 'vitest'
import {
  applyTheme,
  oppositeTheme,
  parseTheme,
  readStoredTheme,
  resolveTheme,
  THEME_STORAGE_KEY,
  writeStoredTheme,
} from './theme'

function fakeStorage(initial: Record<string, string> = {}) {
  const data = { ...initial }
  return {
    data,
    getItem: (k: string) => (k in data ? (data[k] as string) : null),
    setItem: (k: string, v: string) => { data[k] = v },
    removeItem: (k: string) => { delete data[k] },
  }
}

describe('theme resolution', () => {
  it('defaults to dark when nothing is chosen and the OS has no light preference', () => {
    expect(resolveTheme(null, false)).toBe('dark')
  })
  it('follows an OS light preference when nothing is chosen', () => {
    expect(resolveTheme(null, true)).toBe('light')
  })
  it('lets an explicit choice win over the OS in both directions', () => {
    expect(resolveTheme('dark', true)).toBe('dark')
    expect(resolveTheme('light', false)).toBe('light')
  })
  it('toggles to the opposite theme', () => {
    expect(oppositeTheme('dark')).toBe('light')
    expect(oppositeTheme('light')).toBe('dark')
  })
})

describe('theme persistence', () => {
  it('only accepts known values', () => {
    expect(parseTheme('dark')).toBe('dark')
    expect(parseTheme('light')).toBe('light')
    expect(parseTheme('purple')).toBeNull()
    expect(parseTheme(null)).toBeNull()
    expect(parseTheme(undefined)).toBeNull()
  })
  it('round-trips a stored choice', () => {
    const s = fakeStorage()
    writeStoredTheme('light', s)
    expect(s.data[THEME_STORAGE_KEY]).toBe('light')
    expect(readStoredTheme(s)).toBe('light')
  })
  it('clears the stored choice when set back to follow-the-OS', () => {
    const s = fakeStorage({ [THEME_STORAGE_KEY]: 'dark' })
    writeStoredTheme(null, s)
    expect(readStoredTheme(s)).toBeNull()
  })
  it('ignores a corrupt stored value', () => {
    expect(readStoredTheme(fakeStorage({ [THEME_STORAGE_KEY]: '{bad' }))).toBeNull()
  })
  it('survives missing or throwing storage', () => {
    expect(readStoredTheme(null)).toBeNull()
    expect(() => writeStoredTheme('dark', null)).not.toThrow()
    const throwing = {
      getItem: () => { throw new Error('blocked') },
      setItem: () => { throw new Error('blocked') },
      removeItem: () => { throw new Error('blocked') },
    }
    expect(readStoredTheme(throwing)).toBeNull()
    expect(() => writeStoredTheme('dark', throwing)).not.toThrow()
    expect(() => writeStoredTheme(null, throwing)).not.toThrow()
  })
})

describe('applyTheme', () => {
  function fakeRoot() {
    const attrs = new Map<string, string>()
    return {
      attrs,
      setAttribute: (k: string, v: string) => { attrs.set(k, v) },
      removeAttribute: (k: string) => { attrs.delete(k) },
    }
  }
  it('sets data-theme for an explicit choice', () => {
    const r = fakeRoot()
    applyTheme(r, 'light')
    expect(r.attrs.get('data-theme')).toBe('light')
    applyTheme(r, 'dark')
    expect(r.attrs.get('data-theme')).toBe('dark')
  })
  it('removes data-theme to follow the OS', () => {
    const r = fakeRoot()
    applyTheme(r, 'light')
    applyTheme(r, null)
    expect(r.attrs.has('data-theme')).toBe(false)
  })
})
