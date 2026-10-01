export type MenuNavKey = 'ArrowDown' | 'ArrowUp' | 'Home' | 'End'

/** Index of the item to focus next in a menu, skipping disabled items and wrapping at the ends.
 *  `current` is the focused index (-1 if none). Returns -1 when every item is disabled. */
export function nextMenuIndex(current: number, key: MenuNavKey, disabled: boolean[]): number {
  const n = disabled.length
  const enabled = (i: number) => !disabled[i]
  if (!disabled.some((d) => !d)) return -1
  if (key === 'Home') return disabled.findIndex((_, i) => enabled(i))
  if (key === 'End') {
    for (let i = n - 1; i >= 0; i--) if (enabled(i)) return i
    return -1
  }
  const step = key === 'ArrowDown' ? 1 : -1
  // With nothing focused, Down enters at the first item and Up at the last.
  let i = current < 0 ? (step === 1 ? -1 : n) : current
  for (let k = 0; k < n; k++) {
    i = (i + step + n) % n
    if (enabled(i)) return i
  }
  return -1
}
