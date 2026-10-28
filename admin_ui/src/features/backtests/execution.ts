import { formatDay, formatNumber } from 'ioiti-ui'

import type { BacktestExecution } from '@/lib/types'

/** What WATA's own orders took and cost, as the form says it and the values it would take from it. Pure. */

function seconds(value: number | null): string {
  return value === null ? '-' : `${formatNumber(value, 1)} s`
}

/**
 * The delay that applies to a run: from the signal's own time for a script, from its receipt for a replay of the
 * journal, and for the signals a rule raised (the engine's time is in their moment already).
 */
function delayOf(measured: BacktestExecution, source: 'pine' | 'journal' | 'ta_signals') {
  return source === 'pine' ? measured.signal_delay_seconds.from_signal : measured.signal_delay_seconds.from_receipt
}

export function executionSentence(measured: BacktestExecution, source: 'pine' | 'journal' | 'ta_signals'): string {
  const period = `${formatDay(measured.from, false)} to ${formatDay(measured.to)}`
  if (measured.orders === 0 && measured.signals === 0) return `No order of WATA from ${period} to measure.`
  const delay = delayOf(measured, source)
  const parts = [
    `From ${period}, on ${measured.orders} order${measured.orders === 1 ? '' : 's'} of WATA:`,
    `a signal reached the trader ${seconds(delay.median)} after ${source === 'pine' ? 'its time' : 'it was received'} (90 %: ${seconds(delay.p90)}),`,
    `an order took ${seconds(measured.order_seconds.median)} to be accepted,`,
    measured.slippage.all.median === null
      ? 'no fill was beside a recorded quote.'
      : `a fill was ${formatNumber(measured.slippage.all.median, 3)} € a turbo worse than the quote recorded (${measured.slippage.all.count} fills).`,
  ]
  if (measured.refusals.share_percent) parts.push(`Saxo refused ${formatNumber(measured.refusals.share_percent, 1)} % of the orders, which a backtest does not.`)
  return parts.join(' ')
}

/**
 * The settings a run would take from the measure, as typed in the form: only
 * those measured often enough, and only the ones the form has (``known``).
 */
export function executionChanges(measured: BacktestExecution, source: 'pine' | 'journal' | 'ta_signals', known?: readonly string[]): Record<string, string> {
  const changes: Record<string, string> = {}
  const delay = delayOf(measured, source)
  if (delay.count >= measured.least && delay.median !== null) changes.signal_delay_seconds = String(Math.max(0, Math.round(delay.median * 10) / 10))
  if (measured.suggested.order_seconds !== undefined) changes.order_seconds = String(measured.suggested.order_seconds)
  if (measured.suggested.slippage !== undefined) changes.slippage = String(measured.suggested.slippage)
  return known ? Object.fromEntries(Object.entries(changes).filter(([name]) => known.includes(name))) : changes
}
