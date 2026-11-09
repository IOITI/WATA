import { formatDay } from 'ioiti-ui'
import { describe, expect, it } from 'vitest'

import type { BacktestRestsOn } from '@/lib/types'

import { restsOnSentences, tradeFlags } from './restsOn'
import { noiseSentences } from './robustness'

const NOTHING: BacktestRestsOn = { trades: 0, flags: {}, trades_priced_off_quotes: 0, share_percent: null, funds_refused: 0, partial_days: [] }

describe('what a run rests on beyond the recorded quotes', () => {
  it('says each trade priced at a recorded quote, or that there was none', () => {
    expect(restsOnSentences(NOTHING)).toEqual(['No trade to price.'])
    expect(restsOnSentences({ ...NOTHING, trades: 1, share_percent: 0 })).toEqual(['Each of its 1 trade was bought and sold at a recorded quote.'])
  })

  it('counts one trade priced off the quotes in the singular, and names the days recorded in part, three at most', () => {
    const one: BacktestRestsOn = {
      trades: 12,
      flags: { knocked_out: 1 },
      trades_priced_off_quotes: 1,
      share_percent: 8.3,
      funds_refused: 1,
      partial_days: ['2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23', '2026-10-24'],
    }
    expect(restsOnSentences(one)).toEqual([
      '1 of its 12 trades (8.3%) was priced off the recorded quotes: 1 knocked out when the index reached its level.',
      '1 entry refused as Saxo refuses one: the turbos cost more than the account held at the price paid.',
      `5 days recorded for less than 95% of the trading hours (${formatDay('2026-10-20')}, ${formatDay('2026-10-21')}, ` +
        `${formatDay('2026-10-22')} and 2 more): what came while the recorder was not listening was not traded.`,
    ])
  })

  it('shows a trade’s flags in the order they are said, a hole last', () => {
    expect(tradeFlags({ flags: ['hole', 'index_priced', 'frozen_quote'] })).toEqual(['Frozen quote', 'Priced from the index', 'Held through a hole'])
    expect(tradeFlags({})).toEqual([])
  })
})

describe('a fill-noise stress', () => {
  it('says how far each level moved the total, beside the run’s own', () => {
    const levels = [
      { percent: 0.1, draws: 4, lowest: 1.2, highest: 2.6, median: 1.9 },
      { percent: 0.5, draws: 4, lowest: -3.1, highest: 4.4, median: 0.7 },
    ]
    expect(noiseSentences(levels, 2)).toEqual([
      'Each fill moved by up to 0.1% either way, 4 draws: from +1.20% to +2.60%, +1.90% in the middle (the run: +2.00%).',
      'Each fill moved by up to 0.5% either way, 4 draws: from −3.10% to +4.40%, +0.70% in the middle (the run: +2.00%). Some draws end in a loss.',
    ])
    // A run that lost whatever its fills: nothing more to say.
    expect(noiseSentences([{ percent: 0.1, draws: 4, lowest: -45.5, highest: -44.9, median: -45.2 }])).toEqual([
      'Each fill moved by up to 0.1% either way, 4 draws: from −45.50% to −44.90%, −45.20% in the middle.',
    ])
  })
})
