import { describe, expect, it } from 'vitest'

import type { ExecutionLedger, LedgerFigures, LedgerGroup } from '@/lib/types'

import { costTone, deliveryText, ledgerLines, ledgerTiles, pointsText, saxoText, secondsText } from './executionLedger'

const NONE: LedgerFigures = { count: 0, median: null, mean: null, p90: null, sum: null }

function figures(median: number, count = 4): LedgerFigures {
  return { count, median, mean: median, p90: median * 2, sum: median * count }
}

function group(name: string, extra: Partial<LedgerGroup> = {}): LedgerGroup {
  return {
    group: name,
    trades: 4,
    result_percent: figures(-1.5),
    cost_percent: figures(0.8),
    signal_to_received: NONE,
    entry_slippage_percent: figures(0.3),
    exit_slippage_percent: figures(0.4),
    ...extra,
  }
}

function ledger(summary: Partial<ExecutionLedger['summary']> = {}): ExecutionLedger {
  return {
    from: '2026-10-01',
    to: '2026-10-31',
    trades: [],
    summary: {
      trades: 4,
      signal_to_received: figures(14),
      received_to_order: figures(0.02),
      order_to_accepted: figures(0.25),
      accepted_to_fill: figures(0.4),
      sale_attempts: figures(0),
      sale_to_fill: figures(1.2),
      delay_percent: figures(0.35),
      entry_slippage_percent: figures(0.5),
      exit_slippage_percent: figures(1.1),
      spread_percent: figures(0.3),
      cost_percent: figures(1.9),
      result_percent: figures(-2),
      ...summary,
    },
    by_exit: [],
    by_decided_on: [],
    by_source: [],
  }
}

describe('the execution ledger on the Orders page', () => {
  it('says a cost in points with its sign, and nothing when none was measured', () => {
    expect(pointsText(0.4)).toBe('+0.40 pt')
    expect(pointsText(-0.106)).toBe('−0.11 pt')
    expect(pointsText(0)).toBe('0.00 pt')
    expect(pointsText(null)).toBe('—')
    expect(pointsText(Number.NaN)).toBe('—')
  })

  it('says a time in seconds, then minutes', () => {
    expect(secondsText(0.253)).toBe('0.25 s')
    expect(secondsText(4.2)).toBe('4.2 s')
    expect(secondsText(14.94)).toBe('15 s')
    expect(secondsText(125)).toBe('2 min 5 s')
    expect(secondsText(-0.4)).toBe('−0.40 s')
    expect(secondsText(null)).toBe('—')
  })

  it('marks a cost over a tenth of a point, and a gain under minus a tenth', () => {
    expect(costTone(0.5)).toBe('negative')
    expect(costTone(0.05)).toBe('neutral')
    expect(costTone(-0.3)).toBe('positive')
    expect(costTone(null)).toBe('neutral')
  })

  it('gives the cost in all, then each of its three causes', () => {
    const tiles = ledgerTiles(ledger())

    expect(tiles.map((tile) => tile.label)).toEqual(['Lost to execution', 'Signal to order', 'Entry slippage', 'Exit slippage'])
    expect(tiles[0]).toMatchObject({ value: '+1.90 pt', tone: 'negative' })
    expect(tiles[0].hint).toBe('Per trade, typical. +7.60 pt over 4 trades, mean +1.90 pt')
    expect(tiles[3].hint).toContain('Sold under the price the sale was decided on.')
  })

  it('says when nothing is measured yet', () => {
    const tiles = ledgerTiles(
      ledger({ cost_percent: NONE, delay_percent: NONE, entry_slippage_percent: NONE, exit_slippage_percent: NONE }),
    )

    expect(tiles.map((tile) => tile.value)).toEqual(['—', '—', '—', '—'])
    expect(tiles[0].hint).toBe('Not measured yet')
    expect(tiles.every((tile) => tile.tone === 'neutral')).toBe(true)
  })

  it('lines the exits up with their typical result, cost and exit slippage', () => {
    expect(ledgerLines([group('Stop-loss', { exit_slippage_percent: figures(1.4) })])).toEqual([
      {
        group: 'Stop-loss',
        trades: 4,
        result: '−1.50 %',
        cost: '+0.80 pt',
        costTone: 'negative',
        exit: '+1.40 pt',
        exitTone: 'negative',
      },
    ])
  })

  it('says how long the signals took from where they were raised, and only what was measured', () => {
    expect(
      deliveryText([
        group('TradingView', { signal_to_received: figures(14.2) }),
        group('WATA’s engine', { signal_to_received: figures(0.9) }),
        group('Not known'),
      ]),
    ).toBe('TradingView 14 s, WATA’s engine 0.90 s')
    expect(deliveryText([group('Not known')])).toBeNull()
  })

  it('says Saxo’s side in one sentence', () => {
    expect(saxoText(ledger())).toBe(
      'Saxo accepted a purchase in 0.25 s, filled it 0.40 s later by its own clock, a sale was sent 0.00 s after it was first tried, and filled 1.2 s after it left',
    )
  })
})
