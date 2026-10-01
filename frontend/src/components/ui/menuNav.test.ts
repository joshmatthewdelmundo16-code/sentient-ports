import { describe, expect, it } from 'vitest'
import { nextMenuIndex } from './menuNav'

describe('nextMenuIndex', () => {
  const none = [false, false, false, false]
  it('moves down and up, wrapping at the ends', () => {
    expect(nextMenuIndex(0, 'ArrowDown', none)).toBe(1)
    expect(nextMenuIndex(3, 'ArrowDown', none)).toBe(0)
    expect(nextMenuIndex(0, 'ArrowUp', none)).toBe(3)
    expect(nextMenuIndex(2, 'ArrowUp', none)).toBe(1)
  })
  it('starts from the first / last item when nothing is focused', () => {
    expect(nextMenuIndex(-1, 'ArrowDown', none)).toBe(0)
    expect(nextMenuIndex(-1, 'ArrowUp', none)).toBe(3)
  })
  it('jumps to the first and last enabled item', () => {
    expect(nextMenuIndex(2, 'Home', [true, false, false, true])).toBe(1)
    expect(nextMenuIndex(1, 'End', [false, false, false, true])).toBe(2)
  })
  it('skips disabled items', () => {
    expect(nextMenuIndex(0, 'ArrowDown', [false, true, true, false])).toBe(3)
    expect(nextMenuIndex(3, 'ArrowUp', [false, true, true, false])).toBe(0)
  })
  it('returns -1 when every item is disabled', () => {
    for (const k of ['ArrowDown', 'ArrowUp', 'Home', 'End'] as const) {
      expect(nextMenuIndex(0, k, [true, true])).toBe(-1)
    }
  })
  it('stays put when it is the only enabled item', () => {
    expect(nextMenuIndex(1, 'ArrowDown', [true, false, true])).toBe(1)
  })
})
