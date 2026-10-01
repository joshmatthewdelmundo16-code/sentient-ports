import { describe, expect, it } from 'vitest'
import { ICON_NAMES } from './Icon'

describe('icon set', () => {
  it('includes the glyphs the shell and primitives need', () => {
    for (const n of ['menu', 'chevd', 'chevr', 'moon', 'sun', 'bell', 'check', 'checkc', 'x', 'alert', 'zap', 'play', 'search', 'plus', 'minus', 'cross']) {
      expect(ICON_NAMES).toContain(n)
    }
  })
  it('has no duplicate names', () => {
    expect(new Set(ICON_NAMES).size).toBe(ICON_NAMES.length)
  })
})
