/**
 * Whether the signals are drawn on the chart, kept in this browser like the
 * last range used: a taste of whoever reads the chart, not a part of the view
 * to send to someone. A convenience only: without it, or where the browser
 * keeps nothing, the signals are shown.
 */
const KEY = 'wata.prices.signals'

export function rememberedSignals(): boolean {
  try {
    return window.localStorage.getItem(KEY) !== 'hidden'
  } catch {
    return true
  }
}

export function rememberSignals(shown: boolean): void {
  try {
    if (shown) window.localStorage.removeItem(KEY)
    else window.localStorage.setItem(KEY, 'hidden')
  } catch {
    // Private window, storage blocked: nothing is remembered, nothing breaks.
  }
}
