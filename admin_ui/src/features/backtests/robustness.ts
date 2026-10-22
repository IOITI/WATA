import type { NamedCurve } from '@/components/charts/PerformanceCharts'
import { formatDay, formatNumber, formatPercent } from '@/lib/format'
import type { BacktestRisk, BacktestStress, BacktestSummary, BacktestSweep, BacktestSweepAxis, BacktestWalkForward } from '@/lib/types'

import { axisName } from './sweep'

/**
 * What says whether a result holds beyond the days it was found on, in
 * sentences (src/backtest/robustness.py makes the figures). Pure.
 */

/** Below this many days, nothing is brought to a year (ANNUALISED_FROM_DAYS). */
export const ANNUALISED_FROM_DAYS = 20

export interface RiskRow {
  label: string
  value: string
}

function ratio(value: number | null): string {
  return formatNumber(value, 2)
}

/** The risk figures of a run, as rows of label and value. */
export function riskRows(risk: BacktestRisk): RiskRow[] {
  return [
    { label: 'Sharpe ratio, a year', value: ratio(risk.sharpe) },
    { label: 'Sortino ratio, a year (losing days only)', value: ratio(risk.sortino) },
    { label: 'A year at this pace, over the worst drawdown', value: ratio(risk.calmar) },
    { label: 'A year at this pace', value: formatPercent(risk.yearly_percent, true) },
    {
      label: 'Longest under a former high',
      value: `${formatNumber(risk.longest_under_water_days)} day${risk.longest_under_water_days === 1 ? '' : 's'}`,
    },
    { label: 'The worst 5 % of days, on average', value: formatPercent(risk.worst_5_percent_days, true) },
    { label: 'Skew, and tails (3 for a normal law)', value: `${ratio(risk.skew)}, ${ratio(risk.kurtosis)}` },
    { label: 'A win for a loss, on average', value: ratio(risk.payoff) },
    { label: 'A winner kept of its best', value: formatPercent(risk.exit_efficiency, false, 0) },
    {
      label: 'Trades, and hours held, a day',
      value: `${formatNumber(risk.trades_per_day, 2)}, ${formatNumber(risk.hours_held_per_day, 2)}`,
    },
  ]
}

/** Why the figures brought to a year are missing, when they are. */
export function riskNote(risk: BacktestRisk, days: number): string | null {
  if (days < ANNUALISED_FROM_DAYS) {
    return `${days} day${days === 1 ? '' : 's'} traded: under ${ANNUALISED_FROM_DAYS}, nothing is brought to a year.`
  }
  if (risk.sharpe === null) return 'Every day gave the same result: no ratio to a spread of days.'
  return null
}

/** What the same days give in another order, and without the best of them. */
export function stressSentences(stress: BacktestStress): string[] {
  const sentences: string[] = []
  const drawn = stress.monte_carlo
  if (drawn) {
    sentences.push(
      `The same days drawn again ${formatNumber(drawn.draws)} times, in another order: the worst drawdown is ` +
        `${formatPercent(drawn.median, true)} in the middle draw and ${formatPercent(drawn.worst_5_percent, true)} ` +
        `or worse in 5 % of them; in the order they came, ${formatPercent(drawn.actual, true)}.`,
    )
  }
  const without = stress.without_best_days
  if (without.length > 0) {
    const named = ['best day', 'best two', 'best three']
    sentences.push(
      'Without its ' +
        without.map((entry) => `${named[entry.removed - 1] ?? `best ${entry.removed}`}: ${formatPercent(entry.total_percent, true)}`).join('; ') +
        '.',
    )
  }
  return sentences
}

export interface StressVerdict {
  sentence: string
  /** Whether one step worse on both still makes something: none when the grid has no such step. */
  holds: boolean | null
}

function later(axis: BacktestSweepAxis, index: number): string {
  const first = Number(axis.values[0])
  return `${formatNumber(Number(axis.values[index]) - first, 0)} s`
}

function dearer(axis: BacktestSweepAxis, index: number): string {
  const first = Number(axis.values[0])
  return `${formatNumber(Number(axis.values[index]) - first, 2)} €`
}

/** A stress sweep (order time × worse price) in a sentence: the run as it was, one step worse, and the worst. */
export function stressVerdict(sweep: BacktestSweep): StressVerdict | null {
  const [rows, columns] = sweep.axes
  if (!rows || !columns) return null
  const width = columns.values.length
  const totals = sweep.cells.map((cell) => cell.whole?.total_percent ?? null)
  const own = totals[0]
  if (own === null || own === undefined) return null
  const known = totals.filter((total): total is number => total !== null)
  const positive = known.filter((total) => total > 0).length
  let worst = 0
  totals.forEach((total, index) => {
    if (total !== null && total < (totals[worst] ?? Infinity)) worst = index
  })
  const step = totals[width + 1]
  const parts = [`As it was run: ${formatPercent(own, true)}.`]
  if (step !== null && step !== undefined && rows.values.length > 1 && width > 1) {
    parts.push(`Each order ${later(rows, 1)} later and ${dearer(columns, 1)} dearer a turbo: ${formatPercent(step, true)}.`)
  }
  parts.push(
    `Above zero in ${positive} of the ${known.length} combinations; the worst, ${later(rows, Math.floor(worst / width))} later and ` +
      `${dearer(columns, worst % width)} dearer: ${formatPercent(totals[worst], true)}.`,
  )
  return { sentence: parts.join(' '), holds: step === null || step === undefined ? null : step > 0 }
}

/** The labels a stress sweep's axes are shown with. */
export const STRESS_AXES: Record<string, string> = {
  order_seconds: 'Order to fill (s)',
  slippage: 'Worse price a turbo (€)',
}

/** A walk-forward in a sentence. */
export function walkForwardSentence(walk: BacktestWalkForward, axes: readonly BacktestSweepAxis[], labels: Record<string, string> = {}): string {
  const windows = walk.windows.length
  const chosen = new Set(walk.windows.map((window) => JSON.stringify(window.values))).size
  const names = axes.map((axis) => axisName(axis, labels).toLowerCase()).join(' and ')
  return (
    `Choosing the ${names} that did best on the ${walk.train_days} day${walk.train_days === 1 ? '' : 's'} before, ` +
    `again every ${walk.test_days} day${walk.test_days === 1 ? '' : 's'}, made ${formatPercent(walk.total_percent, true)} ` +
    `over the ${walk.days} days after each choice (worst drawdown ${formatPercent(walk.worst_drawdown, true)}). ` +
    `The best single combination, known only afterwards, made ${formatPercent(walk.best_in_hindsight_percent, true)} on them. ` +
    `${windows} window${windows === 1 ? '' : 's'}, ${chosen} different choice${chosen === 1 ? '' : 's'}.`
  )
}

/**
 * The walk-forward's days one after the other, beside the combination that did
 * best over the same days (known only afterwards): the gap is what choosing costs.
 */
export function walkForwardCurves(sweep: BacktestSweep, walk: BacktestWalkForward): NamedCurve[] {
  const chosen: NamedCurve = {
    id: 'walk',
    name: 'Chosen again each window',
    points: walk.curve.map(([day, value]) => ({ day, value })),
  }
  const days = walk.curve.map(([day]) => day)
  const candidates = sweep.cells
    .filter((cell) => cell.curve)
    .map((cell) => {
      const byDay = new Map((cell.curve ?? []).map(([day, percent]) => [day, percent]))
      let level = 1
      return days.map((day) => {
        level *= 1 + (byDay.get(day) ?? 0) / 100
        return { day, value: Math.round((level - 1) * 10_000) / 100 }
      })
    })
  if (candidates.length === 0 || days.length === 0) return [chosen]
  const last = (points: { value: number }[]) => points[points.length - 1].value
  const best = candidates.reduce((kept, points) => (last(points) > last(kept) ? points : kept))
  return [chosen, { id: 'hindsight', name: 'The best, known afterwards', points: best }]
}

/** "13 Oct to 17 Oct" */
export function daysRange([first, last]: readonly [string, string]): string {
  return first === last ? formatDay(first, false) : `${formatDay(first, false)} to ${formatDay(last, false)}`
}

export interface ChoiceTrust {
  sentence: string
  /** The figure is low enough to doubt the choice. */
  doubtful: boolean
}

/** A sweep's probability of overfitting above this is not believed. */
export const MAX_OVERFITTING = 0.5
/** Nor the deflated Sharpe ratio of its best combination under this. */
export const MIN_DEFLATED_SHARPE = 0.95

/** What the figures of a sweep's choice say: deflated Sharpe ratio and probability of overfitting. */
export function choiceTrust(summary: Pick<BacktestSummary, 'deflated_sharpe' | 'pbo'>): ChoiceTrust[] {
  const found: ChoiceTrust[] = []
  if (summary.deflated_sharpe) {
    found.push({ sentence: summary.deflated_sharpe.sentence, doubtful: summary.deflated_sharpe.probability < MIN_DEFLATED_SHARPE })
  }
  if (summary.pbo) found.push({ sentence: summary.pbo.sentence, doubtful: summary.pbo.probability > MAX_OVERFITTING })
  return found
}
