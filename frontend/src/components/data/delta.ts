import type { PillTone } from '../ui/Pill'

export type Direction = 'increase' | 'decrease' | 'none' | null | undefined

/** Class for a delta value. Direction colours are neutral on purpose: the platform describes
 *  change, it does not judge it (an increase is not "good" and a decrease is not "bad"). */
export function deltaClass(direction: Direction): 'up' | 'down' | 'flat' {
  return direction === 'increase' ? 'up' : direction === 'decrease' ? 'down' : 'flat'
}

/** Status pill for a metric row: what happened, not whether it is desirable. */
export function directionPill(direction: Direction, changed: boolean, numeric: boolean): { tone: PillTone; label: string } {
  if (!changed) return { tone: 'neutral', label: 'No Change' }
  if (!numeric) return { tone: 'info', label: 'Changed' }
  if (direction === 'increase') return { tone: 'info', label: 'Increased' }
  if (direction === 'decrease') return { tone: 'accent', label: 'Decreased' }
  return { tone: 'info', label: 'Changed' }
}
