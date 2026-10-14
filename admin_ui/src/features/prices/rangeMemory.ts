import type { PriceInstrument } from '@/lib/types'

import type { ChartView } from './view'

/**
 * The range last chosen for each kind of instrument, kept in this browser.
 * A range that suits an index (fifteen points) means nothing on a turbo (ten
 * cents), so each kind has its own. A convenience only: without it, or where
 * the browser keeps nothing, the range is the automatic one.
 */
type Kind = PriceInstrument['role']

const key = (kind: Kind) => `wata.prices.range.${kind}`

export function rememberedRange(kind: Kind): number | 'auto' {
  try {
    const size = Number(window.localStorage.getItem(key(kind)))
    return Number.isFinite(size) && size > 0 ? size : 'auto'
  } catch {
    return 'auto'
  }
}

export function rememberRange(kind: Kind, range: ChartView['range']): void {
  try {
    if (typeof range === 'number') window.localStorage.setItem(key(kind), String(range))
    else window.localStorage.removeItem(key(kind))
  } catch {
    // Private window, storage blocked: nothing is remembered, nothing breaks.
  }
}
