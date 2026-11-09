import { formatNumber, formatPercent, formatSpan } from 'ioiti-ui'

import type { BacktestReconciliationSummary } from '@/lib/types'

/** What a replay of the journal says set beside WATA's real positions. Pure. */

/** How far to trust a replay: under this share of the same exits, the backtest's exits are not WATA's. */
export const SAME_EXIT_TRUSTED = 70

export interface ReconciliationVerdict {
  tone: 'close' | 'apart' | 'none'
  sentences: string[]
}

function counted(counts: Record<string, number>, label: (name: string) => string): string {
  return Object.entries(counts)
    .map(([name, count]) => `${count} ${label(name)}`)
    .join(', ')
}

// What WATA did of a signal only the backtest traded, and the backtest of one only WATA traded.
const LIVE_OUTCOME: Record<string, string> = {
  refused: 'refused by WATA',
  failed: 'failed at WATA',
  executed: 'bought by WATA with no position kept',
  unknown: 'not in the journal',
}
const BACKTEST_FATE: Record<string, string> = {
  refused: 'refused by the backtest’s rules',
  unpriced: 'with no recorded turbo for the backtest',
  failed: 'failed in the backtest',
  executed: 'bought by the backtest, not paired',
  not_replayed: 'outside the days replayed',
}

function liveOutcome(name: string): string {
  return LIVE_OUTCOME[name] ?? `${name} at WATA`
}

function backtestFate(name: string): string {
  return BACKTEST_FATE[name] ?? `${name} in the backtest`
}

/**
 * What the fill model gets wrong, said from the medians of the pairs: the price paid against WATA's, the price got
 * against WATA's, and when the backtest sold. None for a run made before they were measured.
 */
export function pricesSentence(summary: BacktestReconciliationSummary): string | null {
  const parts: string[] = []
  const bought = summary.median_entry_price_percent
  if (bought !== null && bought !== undefined) {
    parts.push(bought === 0 ? 'paid what WATA paid' : `paid ${formatNumber(Math.abs(bought), 2)} % ${bought > 0 ? 'more' : 'less'} than WATA`)
  }
  const sold = summary.median_exit_price_percent
  if (sold !== null && sold !== undefined) {
    parts.push(sold === 0 ? 'got what WATA got on a sale' : `got ${formatNumber(Math.abs(sold), 2)} % ${sold > 0 ? 'more' : 'less'} on a sale`)
  }
  const later = summary.median_exit_seconds
  if (later !== null && later !== undefined) {
    // Seconds under a minute, a span beyond (a sale a quarter of an hour apart is another exit).
    const apart = Math.abs(later) < 60 ? `${formatNumber(Math.abs(later), 1)} s` : formatSpan(Math.round(Math.abs(later)))
    parts.push(later === 0 ? 'sold when WATA sold' : `sold ${apart} ${later < 0 ? 'earlier' : 'later'}`)
  }
  if (parts.length === 0) return null
  const said = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
  return `In the middle, the backtest ${said}.`
}

export function reconciliationVerdict(summary: BacktestReconciliationSummary): ReconciliationVerdict {
  if (summary.paired === 0) {
    return { tone: 'none', sentences: ['No backtest trade could be set beside a real position of the same signal.'] }
  }
  const apart = summary.median_result_difference ?? 0
  const sentences = [
    `${summary.paired} trade${summary.paired === 1 ? '' : 's'} set beside WATA’s positions of the same signals: ` +
      `the backtest made ${formatPercent(summary.backtest_total_percent, true)} where WATA made ${formatPercent(summary.real_total_percent, true)}; ` +
      `a backtest trade made ${formatNumber(Math.abs(apart), 2)} points ${apart < 0 ? 'less' : 'more'} than WATA’s in the middle.`,
    `The same exit ${formatNumber(summary.same_exit_percent ?? 0, 0)} % of the time` +
      (summary.same_turbo_percent === null ? '' : `, the same turbo ${formatNumber(summary.same_turbo_percent, 0)} %`) +
      (summary.median_entry_seconds === null
        ? '.'
        : `; bought ${formatNumber(Math.abs(summary.median_entry_seconds), 1)} s ${summary.median_entry_seconds < 0 ? 'before' : 'after'} WATA in the middle.`),
  ]
  const priced = pricesSentence(summary)
  if (priced) sentences.push(priced)
  if (summary.backtest_only > 0) sentences.push(`Only the backtest traded ${summary.backtest_only}: ${counted(summary.backtest_only_live_outcomes, liveOutcome)}.`)
  if (summary.live_only > 0) sentences.push(`Only WATA traded ${summary.live_only}: ${counted(summary.live_only_backtest_fates, backtestFate)}.`)
  const close = (summary.same_exit_percent ?? 0) >= SAME_EXIT_TRUSTED && summary.backtest_only + summary.live_only <= summary.paired / 5
  return { tone: close ? 'close' : 'apart', sentences }
}
