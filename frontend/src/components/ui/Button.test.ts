import { describe, expect, it } from 'vitest'
import { buttonClass } from './Button'

describe('buttonClass', () => {
  it('is just .btn by default', () => {
    expect(buttonClass()).toBe('btn')
  })
  it('adds variant and size modifiers', () => {
    expect(buttonClass('primary', 'sm')).toBe('btn btn-primary btn-sm')
    expect(buttonClass('outline', 'xs')).toBe('btn btn-outline btn-xs')
  })
  it('supports block and extra classes', () => {
    expect(buttonClass('danger', 'md', true, 'loading')).toBe('btn btn-danger btn-block loading')
  })
})
