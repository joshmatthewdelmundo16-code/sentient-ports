import { describe, expect, it } from 'vitest'
import { addToast, markLeaving, MAX_TOASTS, removeToast, toastDuration, type ToastItem } from './toastModel'

const t = (id: number, kind: ToastItem['kind'] = 'info'): ToastItem => ({ id, kind, title: `t${id}`, leaving: false })

describe('toast model', () => {
  it('keeps failures and warnings on screen longer than successes', () => {
    expect(toastDuration('good')).toBe(4200)
    expect(toastDuration('info')).toBe(4200)
    expect(toastDuration('err')).toBeGreaterThan(toastDuration('good'))
    expect(toastDuration('warn')).toBeGreaterThan(toastDuration('good'))
  })
  it('appends in order', () => {
    expect(addToast([t(1)], t(2)).map((x) => x.id)).toEqual([1, 2])
  })
  it('never holds more than the cap, dropping the oldest', () => {
    let list: ToastItem[] = []
    for (let i = 1; i <= MAX_TOASTS + 3; i++) list = addToast(list, t(i))
    expect(list).toHaveLength(MAX_TOASTS)
    expect(list[0]?.id).toBe(4)
    expect(list[list.length - 1]?.id).toBe(MAX_TOASTS + 3)
  })
  it('marks only the requested toast as leaving, without mutating the input', () => {
    const before = [t(1), t(2)]
    const after = markLeaving(before, 2)
    expect(after.map((x) => x.leaving)).toEqual([false, true])
    expect(before[1]?.leaving).toBe(false)
  })
  it('removes by id and ignores unknown ids', () => {
    expect(removeToast([t(1), t(2)], 1).map((x) => x.id)).toEqual([2])
    expect(removeToast([t(1)], 99)).toHaveLength(1)
  })
})
