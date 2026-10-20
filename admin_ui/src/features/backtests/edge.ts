import { formatNumber } from '@/lib/format'
import type { BacktestDayKind, BacktestEdge, BacktestEdgeHorizon } from '@/lib/types'

/** What the moves after a run's entry signals say (src/backtest/edge.py). Pure. */

/** Fewer signals than this at every horizon say nothing of an edge. */
export const LEAST_SIGNALS = 10
/**
 * The t-statistic an edge needs at the horizon it is said of: the strongest of
 * five is picked, so the 5 % a single test is allowed is shared among them (1 %).
 */
export const EDGE_T = 2.6

export function horizonLabel(minutes: number): string {
  return minutes >= 60 && minutes % 60 === 0 ? `${minutes / 60} h` : `${minutes} min`
}

/** Basis points, signed: "+3.20 bp". */
export function basisPoints(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-'
  return `${value > 0 ? '+' : value < 0 ? '−' : ''}${formatNumber(Math.abs(value), 2)} bp`
}

export interface EdgeVerdict {
  tone: 'edge' | 'none' | 'unknown'
  sentence: string
  /** The horizon the sentence speaks of. */
  minutes: number | null
}

function strongest(horizons: readonly BacktestEdgeHorizon[]): BacktestEdgeHorizon | null {
  const counted = horizons.filter((row) => row.signals >= LEAST_SIGNALS && row.random.beaten !== null)
  if (counted.length === 0) return null
  return counted.reduce((kept, row) => {
    const [a, b] = [row.random.beaten ?? 0, kept.random.beaten ?? 0]
    return a > b || (a === b && (row.t ?? -Infinity) > (kept.t ?? -Infinity)) ? row : kept
  })
}

/**
 * Whether the entries move the asset their way further than random moments of
 * the same hours: said of the horizon where they do best against them, an edge
 * only with 95 % of the draws beaten and a t-statistic of ``EDGE_T``.
 */
export function edgeVerdict(edge: BacktestEdge): EdgeVerdict {
  const most = Math.max(0, ...edge.horizons.map((row) => row.signals))
  const best = strongest(edge.horizons)
  if (best === null) {
    return {
      tone: 'unknown',
      minutes: null,
      sentence: `${most} entry signal${most === 1 ? '' : 's'} with a price of the asset: too few to tell an edge, which takes dozens.`,
    }
  }
  const beaten = best.random.beaten ?? 0
  const t = best.t === null ? 'no t-statistic' : `t ${formatNumber(best.t, 1)}`
  const said =
    `After ${horizonLabel(best.minutes)} the asset had moved ${basisPoints(best.mean_bp)} in the signals' direction on average, ` +
    `${formatNumber(best.hit_rate ?? 0, 0)} % of them right, against ${basisPoints(best.random.mean_bp)} at random moments of the same hours: ` +
    `better than ${beaten >= 100 ? 'all' : `${formatNumber(beaten, 1)} %`} of the random draws (${t} over the ${best.independent} signals whose windows do not overlap).`
  if (beaten >= 95 && (best.t ?? 0) >= EDGE_T) return { tone: 'edge', minutes: best.minutes, sentence: said }
  return {
    tone: 'none',
    minutes: best.minutes,
    sentence:
      `${said} Not enough to tell from chance: what the trades made came from the exits and the days` +
      (beaten >= 95 ? ', or the signals are too few apart.' : '.'),
  }
}

export interface EdgeRow {
  label: string
  signals: number
  /** Mean moves by horizon. */
  moves: (number | null)[]
}

export type EdgeSplit = 'hour' | 'direction' | 'fate' | BacktestDayKind

const DAY_KINDS: BacktestDayKind[] = ['volatility', 'day_type', 'gap', 'us_open', 'news']

/** The splits a run's edge has: the kinds of day only once runs measured them. */
export function edgeSplits(edge: BacktestEdge): EdgeSplit[] {
  return ['fate', 'direction', 'hour', ...DAY_KINDS.filter((kind) => (edge.by_day?.[kind] ?? []).length > 0)]
}

/** A split of the moves (by hour, direction, taken or not, kind of day) as rows, the horizons as columns. */
export function edgeRows(edge: BacktestEdge, by: EdgeSplit): EdgeRow[] {
  const groups = by === 'hour' ? edge.by_hour : by === 'direction' ? edge.by_direction : by === 'fate' ? edge.by_fate : (edge.by_day?.[by] ?? [])
  const key = by === 'hour' || by === 'direction' || by === 'fate' ? by : 'group'
  const label = (value: unknown) =>
    by === 'hour' ? `${String(value).padStart(2, '0')}:00` : by === 'fate' ? (value === 'executed' ? 'Taken' : 'Not taken') : String(value)
  return groups.map((group) => {
    const minutes = edge.horizons.map((row) => String(row.minutes))
    return {
      label: label(group[key]),
      signals: Math.max(0, ...minutes.map((key) => group[key]?.signals ?? 0)),
      moves: minutes.map((key) => group[key]?.mean_bp ?? null),
    }
  })
}
