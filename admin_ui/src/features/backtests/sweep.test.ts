import { describe, expect, it } from 'vitest'

import type { BacktestFigures, BacktestSweep, BacktestSweepCell } from '@/lib/types'
import { SUMMARY } from '@/test/backtests'

import { axisName, cellName, heat, METRICS, sweepGrid, sweepVerdict } from './sweep'

function figures(total: number, days: number, trades = 4): BacktestFigures {
  return { ...SUMMARY.figures!, total_percent: total, days, trades }
}

function cell(values: BacktestSweepCell['values'], tuning: number | null, heldOut: number | null): BacktestSweepCell {
  return {
    values,
    tuning: tuning === null ? null : figures(tuning, 7),
    held_out: heldOut === null ? null : figures(heldOut, 3),
    whole: tuning === null ? null : figures(tuning + (heldOut ?? 0), 10),
    money: SUMMARY.money!,
    signals: SUMMARY.signals!,
  }
}

// The stop-loss (rows) against the day's target (columns).
const SWEEP: BacktestSweep = {
  axes: [
    { kind: 'setting', name: 'stoploss_percent', values: [-10, -15] },
    { kind: 'setting', name: 'day_profit_target', values: [1, 1.7, 3] },
  ],
  held_out: 0.3,
  held_out_from: '2026-10-22',
  cells: [cell([-10, 1], 2, 1), cell([-10, 1.7], 6, -2), cell([-10, 3], -1, 0.5), cell([-15, 1], 3, 2), cell([-15, 1.7], 4, 1.5), cell([-15, 3], 1, -1)],
}

const LABELS = { stoploss_percent: 'Stop-loss', day_profit_target: 'Sell once the day would reach' }

describe('a sweep as a grid', () => {
  it('puts the first axis in rows and the second in columns, each cell knowing its place', () => {
    const grid = sweepGrid(SWEEP, METRICS[0], 'tuning')
    expect(grid.rows).toEqual([-10, -15])
    expect(grid.columns).toEqual([1, 1.7, 3])
    expect(grid.cells.map((row) => row.map((entry) => [entry.index, entry.value]))).toEqual([
      [[0, 2], [1, 6], [2, -1]],
      [[3, 3], [4, 4], [5, 1]],
    ])
    expect(sweepGrid(SWEEP, METRICS[0], 'held_out').cells[0].map((entry) => entry.value)).toEqual([1, -2, 0.5])
  })

  it('has one column when there is one axis', () => {
    const one: BacktestSweep = { ...SWEEP, axes: [SWEEP.axes[0]], cells: [cell([-10], 1, null), cell([-15], 2, null)] }
    const grid = sweepGrid(one, METRICS.find((metric) => metric.id === 'trades')!, 'tuning')
    expect(grid.columns).toEqual([''])
    expect(grid.cells).toEqual([[{ index: 0, value: 4 }], [{ index: 1, value: 4 }]])
  })

  it('tints gains and losses by how far they go, and other figures from the least to the most', () => {
    expect(heat(3, [3, -6, null], true)).toEqual({ tone: 'positive', strength: 0.5 })
    expect(heat(-6, [3, -6], true)).toEqual({ tone: 'negative', strength: 1 })
    expect(heat(0, [3, -6], true)).toEqual({ tone: 'neutral', strength: 0 })
    expect(heat(null, [3], true)).toEqual({ tone: 'neutral', strength: 0 })
    expect(heat(5, [0, 5, 10], false)).toEqual({ tone: 'neutral', strength: 0.5 })
    expect(heat(5, [5, 5], false)).toEqual({ tone: 'neutral', strength: 0 })
  })

  it('names axes and cells by the labels of their settings', () => {
    expect(axisName(SWEEP.axes[0], LABELS)).toBe('Stop-loss')
    expect(axisName({ kind: 'input', name: 'in_0', values: [] })).toBe('input in_0')
    expect(axisName({ kind: 'setting', name: 'max_trades_per_day', values: [] })).toBe('max trades per day')
    expect(cellName(SWEEP.axes, SWEEP.cells[1], LABELS)).toBe('stop-loss -10, sell once the day would reach 1.7')
  })
})

describe('what a sweep says of choosing a value', () => {
  it('says when the best of the first days did not hold up on the days held out', () => {
    expect(sweepVerdict(SWEEP, LABELS)).toEqual({
      best: 1,
      held: false,
      sentence:
        'On the 7 days a value may be chosen on, the best was stop-loss -10, sell once the day would reach 1.7: +6.00%. ' +
        'On the 3 days held out it made −2.00%: number 6 of 6. ' +
        'It did not hold up on days that had no say in the choice: it was fitted to the first ones.',
    })
  })

  it('says when it held up', () => {
    const held: BacktestSweep = { ...SWEEP, cells: SWEEP.cells.map((entry, index) => (index === 1 ? cell([-10, 1.7], 6, 3) : entry)) }
    const verdict = sweepVerdict(held, LABELS)
    expect(verdict?.held).toBe(true)
    expect(verdict?.sentence).toContain('it made +3.00%: the best again. It held up on days that had no say in the choice.')
  })

  it('says nothing can be said without days held out', () => {
    const none: BacktestSweep = { ...SWEEP, held_out_from: null, cells: SWEEP.cells.map((entry) => ({ ...entry, held_out: null })) }
    expect(sweepVerdict(none, LABELS)).toMatchObject({ best: 1, held: null })
    expect(sweepVerdict(none, LABELS)?.sentence).toMatch(/Too few days to hold any out/)
    const empty: BacktestSweep = { ...SWEEP, cells: SWEEP.cells.map((entry) => ({ ...entry, tuning: null })) }
    expect(sweepVerdict(empty, LABELS)).toBeNull()
  })
})
