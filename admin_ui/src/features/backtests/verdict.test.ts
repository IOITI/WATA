import { describe, expect, it } from 'vitest'

import type { BacktestModelCheck, BacktestSummary, BacktestSweep, BacktestSweepCell } from '@/lib/types'
import { listedRun, run, SUMMARY } from '@/test/backtests'

import { newestModelCheck, newestStress, NO_DERIVED, runChecks, verdictOf } from './verdict'
import type { Derived } from './verdict'

function cell(values: BacktestSweepCell['values'], whole: number, tuning: number | null = null, heldOut: number | null = null): BacktestSweepCell {
  const figures = (total: number, days: number) => ({ ...SUMMARY.figures!, total_percent: total, days })
  return {
    values,
    tuning: tuning === null ? null : figures(tuning, 7),
    held_out: heldOut === null ? null : figures(heldOut, 3),
    whole: figures(whole, 10),
    money: SUMMARY.money!,
    signals: SUMMARY.signals!,
  }
}

/** A stress sweep whose step one later and dearer (the cell at [1][1]) ends at ``step``. */
function stress(step: number): BacktestSweep {
  const later = [1, 3]
  const dearer = [0, 0.01]
  return {
    axes: [
      { kind: 'setting', name: 'order_seconds', values: later },
      { kind: 'setting', name: 'slippage', values: dearer },
    ],
    held_out: 0.3,
    held_out_from: null,
    cells: [cell([1, 0], 4), cell([1, 0.01], 3), cell([3, 0], 2), cell([3, 0.01], step)],
  }
}

/** A run that passes what it can on its own: 40 trades over 25 days, 97 % of random runs beaten, an edge. */
const STRONG: BacktestSummary = {
  ...SUMMARY,
  figures: { ...SUMMARY.figures!, trades: 40, days: 25, sample: 'few' },
  against_random: { ...SUMMARY.against_random!, beaten: 97, repetitions: 100 },
  edge: {
    horizons: [
      { minutes: 5, signals: 60, mean_bp: 2, hit_rate: 55, t: 1.5, independent: 40, random_mean_bp: 0, beaten: 80 },
      { minutes: 15, signals: 60, mean_bp: 6, hit_rate: 60, t: 3.1, independent: 30, random_mean_bp: 0.2, beaten: 98 },
    ],
    unpriced: 0,
  },
}

const stressed = (step: number): Derived => ({
  ...NO_DERIVED,
  stress: { run: listedRun({ id: 'stress-1', kind: 'sweep', stress_of: listedRun().id }), sweep: stress(step) },
})

const states = (checks: ReturnType<typeof runChecks>) => Object.fromEntries(checks.map((check) => [check.id, check.state]))

describe('the verdict of a run', () => {
  it('fails a run of too few trades and days, whose entries random ones match', () => {
    // The fixture: 3 trades over 3 days, 85 % of random runs beaten, no edge measured, not stressed.
    const checks = runChecks(run())
    expect(states(checks)).toEqual({ sample: 'fail', random: 'fail', edge: 'open', stress: 'open', code: 'pass' })
    expect(checks.find((check) => check.id === 'sample')?.detail).toBe(
      '3 trades over 3 days: too few to conclude. Under 30 trades or 20 days, these figures describe those days and nothing else.',
    )
    expect(checks.find((check) => check.id === 'random')?.detail).toBe('Better than 85% of 20 runs at random entries: 95 % is the bar.')
    expect(verdictOf(checks)).toEqual({ tone: 'fail', sentence: 'Not to be believed yet: enough trades and days, beats random entries.' })
  })

  it('passes a run that clears every bar, its stress included', () => {
    const checks = runChecks(run({ summary: STRONG }), stressed(1.2))
    expect(states(checks)).toEqual({ sample: 'pass', random: 'pass', edge: 'pass', stress: 'pass', code: 'pass' })
    expect(checks.find((check) => check.id === 'edge')?.detail).toBe('At its strongest, after 15 min: t 3.1, 98% of random moments beaten.')
    // Enough to pass, not yet to rely on a figure: said as the page says it.
    expect(checks.find((check) => check.id === 'sample')?.detail).toContain('40 trades over 25 days: enough to see a direction')
    expect(verdictOf(checks)).toEqual({ tone: 'pass', sentence: 'It passes every check the research is held to.' })
  })

  it('leaves open what was not asked for, and says which', () => {
    const checks = runChecks(run({ summary: STRONG, stale: true }))
    expect(states(checks)).toMatchObject({ stress: 'open', code: 'open' })
    expect(verdictOf(checks).sentence).toBe(
      'Nothing against it so far; 2 checks are still open: survives later and dearer fills, made with today’s code.',
    )
  })

  it('fails a run that only lives at its own fills, and an edge under the bar', () => {
    const weak: BacktestSummary = {
      ...STRONG,
      edge: { horizons: STRONG.edge!.horizons.map((row) => ({ ...row, t: 2.1 })), unpriced: 0 },
    }
    const checks = runChecks(run({ summary: weak }), stressed(-0.4))
    expect(states(checks)).toMatchObject({ edge: 'fail', stress: 'fail' })
    expect(checks.find((check) => check.id === 'edge')?.detail).toBe(
      'At its strongest, after 15 min: t 2.1, 98% of random moments beaten (t 2.6 and 95 % are the bar).',
    )
  })

  it('on made-up turbos, asks for the model to have been checked against recorded ones', () => {
    const modelled = run({ summary: STRONG, turbos: 'modelled' })
    expect(states(runChecks(modelled))).toMatchObject({ model: 'open' })
    const found: BacktestModelCheck = {
      recorded_trades: 30, modelled_trades: 29, paired: 28, mean_difference_percent: 0.1, median_absolute_difference_percent: 0.3,
      correlation: 0.86, same_exit_percent: 80, median_entry_price: { recorded: 5, modelled: 5.1 }, recorded_total_percent: 3,
      modelled_total_percent: 3.4,
    }
    const checks = runChecks(modelled, { ...NO_DERIVED, modelCheck: { run: listedRun({ id: 'check-1', model_check_of: 'other' }), found } })
    expect(checks.find((check) => check.id === 'model')).toMatchObject({
      state: 'pass',
      detail: 'Checked on 28 trades of the recorded turbos: a correlation of 0.86 between their results.',
      section: 'model-check',
    })
    // On recorded turbos, there is no model to check.
    expect(states(runChecks(run({ summary: STRONG })))).not.toHaveProperty('model')
  })

  it('judges nothing that is not a run of its own, or not done', () => {
    expect(runChecks(run({ status: { state: 'running' } }))).toEqual([])
    expect(runChecks(run({ stress_of: 'other' }))).toEqual([])
    expect(runChecks(run({ model_check_of: 'other' }))).toEqual([])
  })
})

describe('the verdict of a sweep', () => {
  const sweep: BacktestSweep = {
    axes: [{ kind: 'setting', name: 'stoploss_percent', values: [-10, -15] }],
    held_out: 0.3,
    held_out_from: '2026-10-22',
    cells: [cell([-10], 5, 3, 2), cell([-15], 4, 2, -1)],
  }
  const summary: BacktestSummary = {
    ...SUMMARY,
    days: { asked: 30, tested: 30, by_status: { tested: 30 } },
    pbo: { probability: 0.62, splits: 8, combinations: 70, sentence: '' },
    deflated_sharpe: { probability: 0.97, sharpe_daily: 0.2, expected_max_sharpe_daily: 0.1, trials: 12, days: 21, sentence: '' },
  }

  it('says whether the choice held on the days held out, and whether it was fitted', () => {
    const checks = runChecks(run({ kind: 'sweep', summary }), { ...NO_DERIVED, sweep })
    expect(states(checks)).toEqual({ sample: 'pass', 'held-out': 'pass', overfitting: 'fail', deflated: 'pass', code: 'pass' })
    expect(checks.find((check) => check.id === 'overfitting')?.detail).toBe('A probability of 0.62 that the best was fitted (0.5 at most).')
    expect(checks.find((check) => check.id === 'deflated')?.detail).toBe('97% likely to be real after 12 trials.')
    expect(verdictOf(checks).sentence).toBe('Not to be believed yet: little chance of overfitting.')
  })
})

describe('the runs a check reads', () => {
  it('finds the newest stress of a run, and the newest model check done', () => {
    const runs = [
      listedRun({ id: 'c', model_check_of: 'x', status: { state: 'running' } }),
      listedRun({ id: 'b', stress_of: 'r1' }),
      listedRun({ id: 'a', stress_of: 'r1' }),
      listedRun({ id: 'm', model_check_of: 'y' }),
    ]
    expect(newestStress(runs, 'r1')?.id).toBe('b')
    expect(newestStress(runs, 'r2')).toBeNull()
    expect(newestModelCheck(runs)?.id).toBe('m')
  })
})
