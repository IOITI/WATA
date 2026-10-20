import { describe, expect, it } from 'vitest'

import type { BacktestExecution } from '@/lib/types'

import { executionChanges, executionSentence } from './execution'

const spread = (median: number | null, p90: number | null, count: number) => ({ median, p90, count })

const MEASURED: BacktestExecution = {
  from: '2026-07-21',
  to: '2026-10-20',
  signals: 40,
  orders: 76,
  requests: 80,
  signal_delay_seconds: { from_signal: spread(3.04, 4.2, 40), from_receipt: spread(0.6, 1.1, 40) },
  order_seconds: spread(0.512, 0.9, 76),
  slippage: {
    buy: { ...spread(0.01, 0.02, 38), median_ticks: 1 },
    sell: { ...spread(0.0, 0.01, 38), median_ticks: 0 },
    all: { ...spread(0.005, 0.02, 76), median_ticks: 0.5 },
  },
  refusals: { share_percent: 5, by_hour: [], codes: {} },
  suggested: { signal_delay_seconds: 3, order_seconds: 0.5, slippage: 0.005 },
  least: 10,
}

describe("WATA's orders", () => {
  it('say what they took and cost', () => {
    expect(executionSentence(MEASURED, 'pine')).toBe(
      'From 21 Jul to 20 Oct 2026, on 76 orders of WATA: a signal reached the trader 3.0 s after its time (90 %: 4.2 s), an order took 0.5 s to be accepted, a fill was 0.005 € a turbo worse than the quote recorded (76 fills). Saxo refused 5.0 % of the orders, which a backtest does not.',
    )
    expect(executionSentence(MEASURED, 'journal')).toContain('0.6 s after it was received')
    expect(executionSentence({ ...MEASURED, orders: 0, signals: 0 }, 'pine')).toBe('No order of WATA from 21 Jul to 20 Oct 2026 to measure.')
  })

  it("give a run's settings, the delay of its kind of signals", () => {
    expect(executionChanges(MEASURED, 'pine')).toEqual({ signal_delay_seconds: '3', order_seconds: '0.5', slippage: '0.005' })
    expect(executionChanges(MEASURED, 'journal').signal_delay_seconds).toBe('0.6')
    expect(executionChanges(MEASURED, 'pine', ['slippage'])).toEqual({ slippage: '0.005' })
    expect(executionChanges({ ...MEASURED, suggested: {}, signal_delay_seconds: { ...MEASURED.signal_delay_seconds, from_signal: spread(3, 4, 4) } }, 'pine')).toEqual({})
  })
})
