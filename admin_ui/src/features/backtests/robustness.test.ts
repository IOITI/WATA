import { describe, expect, it } from 'vitest'

import type { BacktestFigures, BacktestRisk, BacktestSweep, BacktestSweepCell, BacktestValue, BacktestWalkForward } from '@/lib/types'

import { choiceTrust, daysRange, riskNote, riskRows, stressSentences, stressVerdict, walkForwardCurves, walkForwardSentence } from './robustness'

const RISK: BacktestRisk = {
  sharpe: 1.84,
  sortino: 2.6,
  calmar: 3.1,
  yearly_percent: 41.2,
  longest_under_water_days: 6,
  worst_5_percent_days: -2.4,
  skew: -0.4,
  kurtosis: 4.1,
  payoff: 1.35,
  exit_efficiency: 46,
  trades_per_day: 3.5,
  hours_held_per_day: 1.25,
}

function figures(total: number): BacktestFigures {
  return { total_percent: total, trades: 4, days: 10 } as BacktestFigures
}

function cell(values: BacktestValue[], total: number, curve?: [string, number, number][]): BacktestSweepCell {
  return { values, tuning: figures(total), held_out: figures(total), whole: figures(total), curve } as BacktestSweepCell
}

describe('the risk of a run', () => {
  it('reads as rows, a missing ratio as the formatters’ empty mark', () => {
    const rows = riskRows(RISK)
    expect(rows.find((row) => row.label.startsWith('Sharpe'))?.value).toBe('1.84')
    expect(rows.find((row) => row.label.startsWith('Longest'))?.value).toBe('6 days')
    expect(rows.find((row) => row.label.startsWith('A winner kept'))?.value).toBe('46%')
    expect(riskRows({ ...RISK, sharpe: null, longest_under_water_days: 1 }).slice(0, 5).map((row) => row.value)).toEqual([
      '—',
      '2.60',
      '3.10',
      '+41.20%',
      '1 day',
    ])
  })

  it('says why nothing is brought to a year', () => {
    expect(riskNote({ ...RISK, sharpe: null }, 12)).toBe('12 days traded: under 20, nothing is brought to a year.')
    expect(riskNote({ ...RISK, sharpe: null }, 30)).toMatch(/same result/)
    expect(riskNote(RISK, 30)).toBeNull()
  })

  it('says what the same days give in another order and without the best of them', () => {
    const sentences = stressSentences({
      monte_carlo: { draws: 1000, median: -4.1, worst_5_percent: -9.8, actual: -3.2 },
      without_best_days: [
        { removed: 1, total_percent: 2.1 },
        { removed: 2, total_percent: -0.4 },
      ],
    })
    expect(sentences[0]).toContain('1,000 times')
    expect(sentences[0]).toContain('−9.80%')
    expect(sentences[1]).toBe('Without its best day: +2.10%; best two: −0.40%.')
    expect(stressSentences({ monte_carlo: null, without_best_days: [] })).toEqual([])
  })
})

describe('a stress sweep', () => {
  const later = [1, 3, 6, 16]
  const dearer = [0, 0.01, 0.02, 0.05]
  const sweep: BacktestSweep = {
    axes: [
      { kind: 'setting', name: 'order_seconds', values: later },
      { kind: 'setting', name: 'slippage', values: dearer },
    ],
    held_out: 0.3,
    held_out_from: null,
    cells: later.flatMap((seconds, row) => dearer.map((price) => cell([seconds, price], Math.round((4 - row - 30 * price) * 100) / 100))),
  }

  it('says the run as it was, one step worse on both, and the worst', () => {
    const verdict = stressVerdict(sweep)
    expect(verdict?.sentence).toContain('As it was run: +4.00%.')
    expect(verdict?.sentence).toContain('Each order 2 s later and 0.01 € dearer a turbo: +2.70%.')
    expect(verdict?.sentence).toContain('the worst, 15 s later and 0.05 € dearer: −0.50%')
    expect(verdict?.holds).toBe(true)
  })

  it('does not hold when one step worse loses', () => {
    const losing = { ...sweep, cells: sweep.cells.map((entry, index) => (index === 5 ? cell(entry.values, -1) : entry)) }
    expect(stressVerdict(losing)?.holds).toBe(false)
    expect(stressVerdict({ ...sweep, axes: [sweep.axes[0]] })).toBeNull()
  })
})

describe('a walk-forward', () => {
  const walk: BacktestWalkForward = {
    train_days: 2,
    test_days: 2,
    days: 4,
    total_percent: 1.5,
    worst_day: -1,
    worst_drawdown: -1.99,
    changes: 1,
    best_in_hindsight_percent: 8.37,
    windows: [
      { train: ['2026-10-12', '2026-10-13'], test: ['2026-10-14', '2026-10-15'], cell: 0, values: [8], train_percent: 4, test_percent: -1.99, test_rank: 2, cells: 2 },
      { train: ['2026-10-14', '2026-10-15'], test: ['2026-10-16', '2026-10-17'], cell: 1, values: [12], train_percent: 4, test_percent: 4.04, test_rank: 1, cells: 2 },
    ],
    curve: [
      ['2026-10-14', -1],
      ['2026-10-15', -1.99],
      ['2026-10-16', 0],
      ['2026-10-17', 1.5],
    ],
  }
  const days = ['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16', '2026-10-17']
  const sweep: BacktestSweep = {
    axes: [{ kind: 'input', name: 'in_0', values: [8, 12] }],
    held_out: 0.3,
    held_out_from: null,
    cells: [
      cell([8], 0, days.map((day, index) => [day, [2, 2, -1, -1, -1, -1][index], 1])),
      cell([12], 0, days.map((day, index) => [day, [-1, -1, 1, 3, 2, 2][index], 1])),
    ],
  }

  it('is told in a sentence', () => {
    const sentence = walkForwardSentence(walk, sweep.axes, { in_0: 'Fast length' })
    expect(sentence).toContain('Choosing the fast length that did best on the 2 days before, again every 2 days, made +1.50%')
    expect(sentence).toContain('known only afterwards, made +8.37%')
    expect(sentence).toContain('2 windows, 2 different choices.')
  })

  it('is drawn beside the best combination over the same days', () => {
    const [chosen, hindsight] = walkForwardCurves(sweep, walk)
    expect(chosen.points.map((point) => point.value)).toEqual([-1, -1.99, 0, 1.5])
    expect(hindsight.name).toBe('The best, known afterwards')
    // 1, 3, 2, 2 % one after the other.
    expect(hindsight.points[3].value).toBeCloseTo(((1.01 * 1.03 * 1.02 * 1.02) - 1) * 100, 1)
    expect(walkForwardCurves({ ...sweep, cells: sweep.cells.map((entry) => ({ ...entry, curve: undefined })) }, walk)).toHaveLength(1)
  })

  it('names its windows by their days', () => {
    expect(daysRange(['2026-10-14', '2026-10-15'])).toBe('14 Oct to 15 Oct')
    expect(daysRange(['2026-10-14', '2026-10-14'])).toBe('14 Oct')
  })
})

describe('the choice of a sweep', () => {
  it('is doubted under 95 % for the deflated ratio and over half for overfitting', () => {
    const found = choiceTrust({
      deflated_sharpe: { probability: 0.62, sharpe_daily: 0.2, expected_max_sharpe_daily: 0.15, trials: 40, days: 30, sentence: 'luck' },
      pbo: { probability: 0.3, splits: 8, combinations: 70, sentence: 'fine' },
    })
    expect(found).toEqual([
      { sentence: 'luck', doubtful: true },
      { sentence: 'fine', doubtful: false },
    ])
    expect(choiceTrust({})).toEqual([])
  })
})
