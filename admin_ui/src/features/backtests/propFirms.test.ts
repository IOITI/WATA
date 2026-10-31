import { describe, expect, it } from 'vitest'

import type { PropFirmAnswer, PropFirmFigures, PropFirmKept } from '@/lib/types'

import { answerSaid, askedOf, askedSaid, barSaid, challengeRows, figuresSaid, sentSaid } from './propFirms'

const FIGURES: Omit<PropFirmFigures, 'r_series'> = {
  stoploss_percent: -10, trades: 42, days: 12, trades_per_day: 3.5, win_rate: 0.5476, average_win_r: 0.62, average_loss_r: -0.48,
  notes: ['42 trades: LuxAlgo advises a hundred or more for its bootstrap'],
}

const ANSWER: PropFirmAnswer = {
  bar: { minPassPerAttempt: 0.5, requirePositiveEv: false },
  passing: [{
    firmId: 'example-funding', firmName: 'Example Funding', challengeId: 'example-50k', name: 'Example Funding - 50K',
    accountSize: 50_000, currency: 'usd', passPerAttempt: 0.62, passCiLow: 0.6, passCiHigh: 0.64, fundedProbability: 0.41,
    expectedAttempts: 1.6, expectedCost: 552, evTotal: 830, meetsBar: true, flagIds: ['attempts-iid'],
  }],
  belowBar: [{ name: 'Sample - 100K', accountSize: 100_000, currency: 'usd', passPerAttempt: 0.31, evTotal: -210, meetsBar: false }],
  excludedNotSimulatable: 2,
  note: 'A screen of distributions.',
}

const KEPT: PropFirmKept = {
  asked_at: '2026-10-31T09:00:00Z', bar: 0.5,
  asked: { tradesPerDay: 3.5, riskMode: 'percent-of-balance', riskValue: 0.5, productType: 'futures', accountSizeMin: 25_000, accountSizeMax: 100_000 },
  figures: FIGURES, answer: ANSWER,
}

describe('a run checked against funded challenges', () => {
  it('becomes a request of a risk and a scope, or says why not', () => {
    expect(askedOf('0.5', 'futures', 'medium')).toEqual({ risk_percent: 0.5, product_type: 'futures', account_min: 25_000, account_max: 100_000 })
    expect(askedOf('1', 'cfd', 'any')).toEqual({ risk_percent: 1, product_type: 'cfd', account_min: null, account_max: null })
    expect(askedOf('', 'futures', 'small')).toBe('Say the risk per trade, in % of the balance')
    expect(askedOf('9', 'futures', 'small')).toBe('The risk per trade is from 0.1 % to 5 % of the balance')
  })

  it('says its trades in R and what leaves for LuxAlgo, which names nothing', () => {
    expect(figuresSaid(FIGURES)).toBe(
      '42 trades on 12 days, 3.5 a day; 55% won, winners 0.62 R and losers -0.48 R on average (1 R: the stop-loss of 10.0%).',
    )
    expect(sentSaid(FIGURES)).toMatch(/^What leaves for LuxAlgo: this run's 42 results as R-multiples/)
    expect(sentSaid(FIGURES)).toMatch(/Nothing that names the run, the account or its value\.$/)
  })

  it("lists LuxAlgo's answer as a screen: those that meet the bar first, in its order", () => {
    const [first, second] = challengeRows(ANSWER)
    expect(first).toMatchObject({
      name: 'Example Funding - 50K', size: 'US$50,000', pass: '62%', range: '60% to 64%', funded: '41%', attempts: '1.6',
      cost: 'US$552.00', meetsBar: true, flags: ['attempts-iid'], evSign: 1,
    })
    expect(first.ev).toMatch(/^\+US\$830\.00$/)
    expect(second).toMatchObject({ name: 'Sample - 100K', pass: '31%', range: null, funded: '-', meetsBar: false, evSign: -1 })
    expect(answerSaid(ANSWER)).toBe('1 of 2 challenges screened meets the bar. 2 challenges LuxAlgo cannot simulate left out.')
    expect(answerSaid({})).toBe('No challenge was screened.')
    // Meeting the bar is not a profit: said when the expected value is negative.
    const instant = { ...ANSWER.passing![0], name: 'Instant - 25K', evTotal: -309 }
    expect(answerSaid({ passing: [instant, { ...instant, name: 'Instant - 50K' }], belowBar: [] })).toBe(
      '2 of 2 challenges screened meet the bar, all of them at a negative expected value.',
    )
    expect(answerSaid({ passing: [instant, ANSWER.passing![0]] })).toBe(
      '2 of 2 challenges screened meet the bar, 1 of them at a negative expected value.',
    )
  })

  it('says what it asked, and the bar its answer is read against', () => {
    expect(askedSaid(KEPT, 'Europe/Paris')).toMatch(/risking 0\.50 % of the balance a trade, on futures accounts 25,000 to 100,000\.$/)
    expect(barSaid(KEPT)).toBe('The bar: a pass per attempt of 50% or more.')
    expect(barSaid({ ...KEPT, answer: { ...ANSWER, bar: { minPassPerAttempt: 0.6, requirePositiveEv: true } } })).toBe(
      'The bar: a pass per attempt of 60% or more, and a positive expected value.',
    )
  })
})
