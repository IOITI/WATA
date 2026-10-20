import { formatPercent } from '@/lib/format'
import type { BacktestFigures, BacktestSweep, BacktestSweepAxis, BacktestSweepCell, BacktestValue } from '@/lib/types'

/** A sweep's combinations as a grid, its tints, and what it says about choosing a value. Pure. */

export interface Metric {
  id: string
  label: string
  read: (figures: BacktestFigures) => number | null
  /** Above and below zero mean gain and loss; else more is only more. */
  signed: boolean
  percent: boolean
}

export const METRICS: Metric[] = [
  { id: 'total', label: 'All days', read: (figures) => figures.total_percent, signed: true, percent: true },
  { id: 'expectancy', label: 'Per trade', read: (figures) => figures.expectancy, signed: true, percent: true },
  { id: 'win_rate', label: 'Winning', read: (figures) => figures.win_rate, signed: false, percent: true },
  { id: 'drawdown', label: 'Worst drawdown', read: (figures) => figures.worst_drawdown, signed: true, percent: true },
  { id: 'trades', label: 'Trades', read: (figures) => figures.trades, signed: false, percent: false },
]

export type Part = 'tuning' | 'held_out' | 'whole'

export interface GridCell {
  /** Its place among the cells of the sweep: what a run of its own is asked by. */
  index: number
  value: number | null
}

export interface Grid {
  rows: BacktestValue[]
  columns: BacktestValue[]
  cells: GridCell[][]
}

/** The cells of a sweep as a table: a row for each value of its first axis, a column for each of its second. */
export function sweepGrid(sweep: BacktestSweep, metric: Metric, part: Part): Grid {
  const [first, second] = sweep.axes
  const columns: BacktestValue[] = second ? second.values : ['']
  const cells = first.values.map((_, row) =>
    columns.map((__, column) => {
      const index = row * columns.length + column
      const figures = sweep.cells[index]?.[part]
      return { index, value: figures ? metric.read(figures) : null }
    }),
  )
  return { rows: first.values, columns, cells }
}

export interface Heat {
  tone: 'positive' | 'negative' | 'neutral'
  strength: number
}

/** How strongly a cell is tinted among the values of its grid. */
export function heat(value: number | null, all: readonly (number | null)[], signed: boolean): Heat {
  const known = all.filter((entry): entry is number => entry !== null)
  if (value === null || known.length === 0) return { tone: 'neutral', strength: 0 }
  if (signed) {
    const furthest = Math.max(...known.map(Math.abs))
    if (furthest === 0 || value === 0) return { tone: 'neutral', strength: 0 }
    return { tone: value > 0 ? 'positive' : 'negative', strength: Math.abs(value) / furthest }
  }
  const least = Math.min(...known)
  const most = Math.max(...known)
  return { tone: 'neutral', strength: most === least ? 0 : (value - least) / (most - least) }
}

export function axisName(axis: BacktestSweepAxis, labels: Record<string, string> = {}): string {
  return labels[axis.name] ?? (axis.kind === 'input' ? `input ${axis.name}` : axis.name.replace(/_/g, ' '))
}

/** What a cell was traded with: "stop-loss -5, input in_0 12". */
export function cellName(axes: readonly BacktestSweepAxis[], cell: BacktestSweepCell, labels: Record<string, string> = {}): string {
  return axes.map((axis, number) => `${axisName(axis, labels).toLowerCase()} ${String(cell.values[number])}`).join(', ')
}

export interface SweepVerdict {
  best: number
  sentence: string
  held: boolean | null
}

/**
 * What a sweep says about choosing a value: the combination that did best on
 * the days a choice may be made on, and where it then stood on the days held out.
 */
export function sweepVerdict(sweep: BacktestSweep, labels: Record<string, string> = {}): SweepVerdict | null {
  const order = (part: Part) =>
    sweep.cells
      .map((cell, index) => ({ index, total: cell[part]?.total_percent ?? null, days: cell[part]?.days ?? 0 }))
      .filter((entry): entry is { index: number; total: number; days: number } => entry.total !== null)
      .sort((first, second) => second.total - first.total)
  const tuning = order('tuning')
  if (tuning.length === 0) return null
  const best = tuning[0]
  const name = cellName(sweep.axes, sweep.cells[best.index], labels)
  const chosen = `On the ${best.days} day${best.days === 1 ? '' : 's'} a value may be chosen on, the best was ${name}: ${formatPercent(best.total, true)}.`
  const heldOut = order('held_out')
  if (heldOut.length === 0) {
    return { best: best.index, held: null, sentence: `${chosen} Too few days to hold any out: nothing here says it would do best again.` }
  }
  const rank = heldOut.findIndex((entry) => entry.index === best.index) + 1
  if (rank === 0) {
    return { best: best.index, held: null, sentence: `${chosen} It has no figures on the days held out: nothing here says it would do best again.` }
  }
  const then = heldOut[rank - 1]
  const held = rank <= Math.ceil(heldOut.length / 2)
  const place = rank === 1 ? 'the best again' : `number ${rank} of ${heldOut.length}`
  return {
    best: best.index,
    held,
    sentence:
      `${chosen} On the ${then.days} day${then.days === 1 ? '' : 's'} held out it made ${formatPercent(then.total, true)}: ${place}. ` +
      (held ? 'It held up on days that had no say in the choice.' : 'It did not hold up on days that had no say in the choice: it was fitted to the first ones.'),
  }
}
