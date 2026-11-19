import { formatPercent } from 'ioiti-ui'

import type { BacktestFigures, BacktestSweep, BacktestSweepAxis, BacktestSweepCell, BacktestSweepGrid, BacktestValue } from '@/lib/types'

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
export function cellName(axes: readonly BacktestSweepAxis[], cell: Pick<BacktestSweepCell, 'values'>, labels: Record<string, string> = {}): string {
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
export function sweepVerdict(sweep: BacktestSweepGrid, labels: Record<string, string> = {}): SweepVerdict | null {
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

// ── plateaus instead of peaks (src/admin/sweep_plateau.py, which the MCP and the AI agent read) ──

export interface Plateau {
  /** Each cell's plateau: the median tuning total of the cell and its neighbours. */
  scores: (number | null)[]
  best: number
  robust: number
  bestNeighboursMedian: number | null
  gridMedian: number
  /** A best above zero whose neighbours are at or under zero, or under half of it: one step away, most of it is gone. */
  knifeEdge: boolean
}

function numericValues(values: readonly BacktestValue[]): boolean {
  return values.length > 0 && values.every((value) => typeof value === 'number')
}

function median(values: number[]): number {
  const ordered = [...values].sort((first, second) => first - second)
  const middle = Math.floor(ordered.length / 2)
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2
}

const round2 = (value: number) => Math.round(value * 100) / 100

/** The cells one step away on each numeric axis, in the order of the axis's values (cells row by row, last axis fastest). */
export function plateauNeighbours(axes: readonly Pick<BacktestSweepAxis, 'values'>[], index: number): number[] {
  const shape = axes.map((axis) => axis.values.length)
  const place: number[] = []
  let rest = index
  for (let number = shape.length - 1; number >= 0; number -= 1) {
    place.unshift(rest % shape[number])
    rest = Math.floor(rest / shape[number])
  }
  const found: number[] = []
  axes.forEach((axis, number) => {
    if (!numericValues(axis.values)) return
    const order = axis.values.map((_, position) => position).sort((first, second) => (axis.values[first] as number) - (axis.values[second] as number))
    const rank = order.indexOf(place[number])
    for (const step of [-1, 1]) {
      if (rank + step < 0 || rank + step >= order.length) continue
      const moved = [...place]
      moved[number] = order[rank + step]
      found.push(moved.reduce((total, position, at) => total * shape[at] + position, 0))
    }
  })
  return found
}

/** A sweep's plateaus, its best and its robust pick, and whether the best stands on a knife edge; null without a numeric axis. */
export function plateauOf(grid: Pick<BacktestSweepGrid, 'axes' | 'cells'> | null): Plateau | null {
  if (!grid || !grid.axes.some((axis) => numericValues(axis.values))) return null
  const totals = grid.cells.map((cell) => cell.tuning?.total_percent ?? null)
  const known = totals.flatMap((total, index) => (total === null ? [] : [{ index, total }]))
  if (known.length === 0) return null
  const scores = totals.map((total, index) => {
    if (total === null) return null
    const around = plateauNeighbours(grid.axes, index).flatMap((other) => (totals[other] === null ? [] : [totals[other] as number]))
    return round2(median([total, ...around]))
  })
  const pick = (entries: { index: number; value: number }[]) =>
    entries.reduce((kept, entry) => (entry.value > kept.value ? entry : kept)).index
  const best = pick(known.map(({ index, total }) => ({ index, value: total })))
  const robust = pick(scores.flatMap((score, index) => (score === null ? [] : [{ index, value: score }])))
  const aroundBest = plateauNeighbours(grid.axes, best).flatMap((other) => (totals[other] === null ? [] : [totals[other] as number]))
  const gridMedian = median(known.map(({ total }) => total))
  const bestNeighboursMedian = aroundBest.length > 0 ? median(aroundBest) : null
  return {
    scores,
    best,
    robust,
    bestNeighboursMedian: bestNeighboursMedian === null ? null : round2(bestNeighboursMedian),
    gridMedian: round2(gridMedian),
    knifeEdge:
      (totals[best] as number) > 0 &&
      bestNeighboursMedian !== null &&
      (bestNeighboursMedian <= 0 || bestNeighboursMedian < (totals[best] as number) / 2),
  }
}
