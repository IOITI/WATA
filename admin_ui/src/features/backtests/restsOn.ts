import { formatDay, formatNumber, formatPercent } from 'ioiti-ui'
import type { CheckState } from 'ioiti-ui'

import type { BacktestRestsOn, BacktestTrade, BacktestTradeFlag } from '@/lib/types'

/**
 * What a run's result rests on beyond the recorded quotes (``rests_on`` of src/backtest/figures.py): the trades a
 * hole, a frozen quote, a knock-out or a turbo never quoted again made the simulator price otherwise, the entries
 * Saxo would have refused for funds, the days recorded in part. Said by the run's verdict (verdict.ts), its trust
 * panel and its trades. Pure.
 */

/** More of a run's trades than this share (%) priced off the recorded quotes, and it rests on guesses (``RESTS_ON_SHARE``). */
export const MAX_PRICED_OFF_QUOTES = 10

/** A day recorded for less than this share of its trading hours is partly recorded (the simulator's ``PARTIAL_SHARE``). */
export const PARTIAL_SHARE = 0.95

/** Each flag in a few words, as a trade's row says it; in the order they are said. */
export const FLAG_LABEL: Record<BacktestTradeFlag, string> = {
  sold_after_hole: 'Sold once quoted again',
  frozen_quote: 'Frozen quote',
  index_priced: 'Priced from the index',
  knocked_out: 'Knocked out',
  knock_out_guess: 'Taken for knocked out',
  carried: 'Sold a day later',
  unresolved: 'Never quoted again',
  hole: 'Held through a hole',
}

/** Each flag counted in a sentence: ``${count} ${what}``. */
const FLAG_SAID: Record<BacktestTradeFlag, string> = {
  sold_after_hole: 'sold once Saxo quoted the turbo again',
  frozen_quote: 'ordered on a quote frozen for over two minutes',
  index_priced: 'priced from the index',
  knocked_out: 'knocked out when the index reached its level',
  knock_out_guess: 'taken for knocked out without the index',
  carried: 'sold on a later day',
  unresolved: 'never quoted again while the run lasted',
  hole: 'held through a hole in the turbo’s quotes',
}

/** The one flag that changed nothing of a trade's prices: a hole met while held, the sale made at a recorded quote. */
const NOT_A_GUESS: BacktestTradeFlag = 'hole'

const ORDER = Object.keys(FLAG_LABEL) as BacktestTradeFlag[]

const plural = (count: number, one: string, many = `${one}s`) => `${formatNumber(count)} ${count === 1 ? one : many}`

/** A trade's flags in the order they are said, as its row shows them. */
export function tradeFlags(trade: Pick<BacktestTrade, 'flags'>): string[] {
  const flags = new Set(trade.flags ?? [])
  return ORDER.filter((flag) => flags.has(flag)).map((flag) => FLAG_LABEL[flag])
}

/** The flags counted, in the order they are said, those of no trade left out. */
function counted(restsOn: BacktestRestsOn, keep: (flag: BacktestTradeFlag) => boolean): [BacktestTradeFlag, number][] {
  return ORDER.filter(keep)
    .map((flag): [BacktestTradeFlag, number] => [flag, restsOn.flags[flag] ?? 0])
    .filter(([, count]) => count > 0)
}

/** What a run rests on beyond the recorded quotes, a sentence a thing. */
export function restsOnSentences(restsOn: BacktestRestsOn): string[] {
  const sentences: string[] = []
  const priced = restsOn.trades_priced_off_quotes
  if (restsOn.trades === 0) {
    sentences.push('No trade to price.')
  } else if (priced === 0) {
    sentences.push(`Each of its ${plural(restsOn.trades, 'trade')} was bought and sold at a recorded quote.`)
  } else {
    const guesses = counted(restsOn, (flag) => flag !== NOT_A_GUESS)
    const twice = guesses.reduce((sum, [, count]) => sum + count, 0) > priced
    sentences.push(
      `${formatNumber(priced)} of its ${plural(restsOn.trades, 'trade')} (${formatPercent(restsOn.share_percent ?? 0, false, 1)}) ` +
        `${priced === 1 ? 'was' : 'were'} priced off the recorded quotes: ` +
        guesses.map(([flag, count]) => `${formatNumber(count)} ${FLAG_SAID[flag]}`).join(', ') +
        (twice ? ' (a trade may be counted twice).' : '.'),
    )
  }
  const holes = restsOn.flags[NOT_A_GUESS] ?? 0
  if (holes > 0) {
    sentences.push(`${plural(holes, 'trade')} ${holes === 1 ? 'was' : 'were'} ${FLAG_SAID[NOT_A_GUESS]}.`)
  }
  if (restsOn.funds_refused > 0) {
    sentences.push(
      `${plural(restsOn.funds_refused, 'entry', 'entries')} refused as Saxo refuses ${restsOn.funds_refused === 1 ? 'one' : 'them'}: ` +
        'the turbos cost more than the account held at the price paid.',
    )
  }
  const retried = restsOn.funds_retried ?? 0
  if (retried > 0) {
    sentences.push(
      `${plural(retried, 'entry', 'entries')} bought with fewer turbos, as WATA sends an order again once Saxo refused it for funds.`,
    )
  }
  const partial = restsOn.partial_days
  if (partial.length > 0) {
    const named = partial.slice(0, 3).map((day) => formatDay(day)).join(', ')
    const more = partial.length > 3 ? ` and ${formatNumber(partial.length - 3)} more` : ''
    sentences.push(
      `${plural(partial.length, 'day')} recorded for less than ${formatPercent(PARTIAL_SHARE * 100, false, 0)} of the trading hours ` +
        `(${named}${more}): what came while the recorder was not listening was not traded.`,
    )
  }
  return sentences
}

/** The verdict's check of it: too many trades priced off the recorded quotes fail; a run of before is not counted. */
export function restsOnCheck(restsOn: BacktestRestsOn | null): { state: CheckState; detail: string } {
  if (!restsOn) {
    return { state: 'open', detail: 'Not counted: the run was made before what its trades rest on was. Make it again to know.' }
  }
  const over = (restsOn.share_percent ?? 0) > MAX_PRICED_OFF_QUOTES
  const sentences = restsOnSentences(restsOn)
  return {
    state: over ? 'fail' : 'pass',
    detail: sentences.join(' ') + (over ? ` Over ${MAX_PRICED_OFF_QUOTES} % of its trades rest on what was not recorded.` : ''),
  }
}
