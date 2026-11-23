import { describe, expect, it } from 'vitest'

import type { BacktestExecution } from '@/lib/types'

import { drawnPricing, executionChanges, executionSentence } from './execution'

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

  it('say what a fill paid beyond the quote a run prices it at, once the admin measures it', () => {
    const atFill: BacktestExecution = {
      ...MEASURED,
      slippage: { ...MEASURED.slippage, all: { ...spread(0.02, 0.123, 57), median_ticks: 2 } },
      slippage_at_fill: {
        buy: { ...spread(-0.004, 0.02, 30), median_ticks: -0.4 },
        sell: { ...spread(0.002, 0.03, 15), median_ticks: 0.2 },
        exit: { ...spread(0.015, 0.05, 12), median_ticks: 1.5 },
        orders: { ...spread(-0.003, 0.02, 45), median_ticks: -0.3 },
        exit_percent: spread(0.2, 0.5, 12),
      },
    }
    expect(executionSentence(atFill, 'pine')).toContain(
      'an order sent between two of Saxo’s quotes was 0.003 € a turbo better than the quote a run prices it at (45 fills), ' +
        'a fill 0.020 € a turbo worse than the quote standing when its order left, the turbo’s move since that quote included.',
    )
    // Too few orders had a quote to be priced at: the measure of before alone.
    const none = { ...atFill, slippage_at_fill: { ...atFill.slippage_at_fill!, orders: { ...spread(null, null, 0), median_ticks: null } } }
    expect(executionSentence(none, 'pine')).toContain('a fill was 0.020 € a turbo worse than the quote recorded (57 fills).')
  })

  it('say what the prices drawn for a run are added to', () => {
    expect(drawnPricing('fill')).toBe('each price what they paid beyond the quote the run fills an order at')
    // A run made before says nothing: its prices were measured against the quote standing when each order left.
    expect(drawnPricing(undefined)).toBe(drawnPricing('departure'))
    expect(drawnPricing('departure')).toContain('the quote standing when they left')
  })

  it("give a run's settings, the delay of its kind of signals", () => {
    expect(executionChanges(MEASURED, 'pine')).toEqual({ signal_delay_seconds: '3', order_seconds: '0.5', slippage: '0.005' })
    expect(executionChanges(MEASURED, 'journal').signal_delay_seconds).toBe('0.6')
    expect(executionChanges(MEASURED, 'pine', ['slippage'])).toEqual({ slippage: '0.005' })
    // What the monitor's exits got under the bid that decided them, once enough were measured.
    expect(executionChanges({ ...MEASURED, suggested: { exit_slippage_percent: 0.2 } }, 'journal')).toEqual({ signal_delay_seconds: '0.6', exit_slippage_percent: '0.2' })
    expect(executionChanges({ ...MEASURED, suggested: {}, signal_delay_seconds: { ...MEASURED.signal_delay_seconds, from_signal: spread(3, 4, 4) } }, 'pine')).toEqual({})
  })
})
