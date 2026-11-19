/**
 * Why a run wins or loses, as its page says it: the plateau of a sweep's best (src/admin/sweep_plateau.py), what
 * separated its winners from its losers (src/admin/trade_autopsy.py), and what its day's caps add beside its twin
 * without them, with the tail they hide (src/admin/day_tail.py). Pure.
 */

import { formatNumber, formatPercent } from 'ioiti-ui'

import type { BacktestAutopsy, BacktestDecomposition, BacktestSweepAxis, BacktestSweepPlateau, BacktestTailFigures } from '@/lib/types'

import { cellName } from './sweep'

const percent = (value: number | null | undefined) => (value === null || value === undefined ? '—' : formatPercent(value, true))
const share = (value: number | null | undefined) => (value === null || value === undefined ? '—' : `${formatNumber(value, 1)} %`)

/** What a sweep's plateau says: its best on a plateau or a knife edge, the robust pick, and the walk-forward on plateaus. */
export function plateauSentences(plateau: BacktestSweepPlateau, axes: BacktestSweepAxis[], labels: Record<string, string> = {}): string[] {
  const name = (values: BacktestSweepPlateau['best_values']) => cellName(axes, { values }, labels)
  const sentences: string[] = []
  if (plateau.best_total !== null && plateau.best_total <= 0) {
    // Nothing gained: no plateau nor pick to speak of.
    return [`Its best, ${name(plateau.best_values)}, made ${percent(plateau.best_total)}: no gain for its neighbours to keep.`]
  }
  if (plateau.best_neighbours_median === null) {
    sentences.push(`Its best, ${name(plateau.best_values)}, has no neighbour to judge it by.`)
  } else if (plateau.knife_edge) {
    sentences.push(
      `Its best, ${name(plateau.best_values)} (${percent(plateau.best_total)}), stands on a knife edge: its neighbours made ${percent(plateau.best_neighbours_median)} at the median. One step away, most of it is gone.`,
    )
  } else {
    sentences.push(`Its best, ${name(plateau.best_values)}, stands on a plateau: its neighbours made ${percent(plateau.best_neighbours_median)} at the median.`)
  }
  if (plateau.robust !== plateau.best) {
    sentences.push(
      `The robust pick, the best among its neighbours, is ${name(plateau.robust_values)}: ${percent(plateau.robust_total)}` +
        (plateau.robust_held_out !== null ? `, ${percent(plateau.robust_held_out)} on the days held out (the best: ${percent(plateau.best_held_out)}).` : '.'),
    )
  }
  if (plateau.robust_walk_forward) {
    sentences.push(
      `Chosen on plateaus window after window: ${percent(plateau.robust_walk_forward.total_percent)} over ${plateau.robust_walk_forward.days} days` +
        (plateau.walk_forward_percent !== null && plateau.walk_forward_percent !== undefined ? `, against ${percent(plateau.walk_forward_percent)} chosen on peaks.` : '.'),
    )
  }
  return sentences
}

/** The exits read in-sample: how many losers had first been up, how many winners first down. */
export function exitSentences(exits: BacktestAutopsy['exits']): string[] {
  const sentences: string[] = []
  if (exits.losers > 0) {
    sentences.push(
      `Of its ${exits.losers} losing trade${exits.losers === 1 ? '' : 's'}, ${share(exits.losers_up_first['1'])} had first been up 1 %, ${share(exits.losers_up_first['2'])} 2 %, ${share(exits.losers_up_first['3'])} 3 %.`,
    )
  }
  if (exits.winners > 0) {
    sentences.push(
      `Of its ${exits.winners} winning trade${exits.winners === 1 ? '' : 's'}, ${share(exits.winners_down_first['3'])} had first been down 3 %, ${share(exits.winners_down_first['5'])} 5 %.`,
    )
  }
  return sentences
}

/** A feature's cut as it reads, or why it has none. */
export function featureSaid(feature: BacktestAutopsy['features'][number]): string {
  if (feature.said) return feature.said
  return feature.why ? `No cut: ${feature.why}` : 'No cut'
}

/** What the caps add and what the entries add, beside the twin without them. */
export function capsSentences(found: BacktestDecomposition): string[] {
  const { with_caps: withCaps, without_caps: withoutCaps } = found
  return [
    `With its day's caps: ${percent(withCaps.entries)} in ${withCaps.trades ?? '?'} trades, against ${percent(withCaps.random)} for its random entries (median).`,
    `Without them: ${percent(withoutCaps.entries)} in ${withoutCaps.trades ?? '?'} trades, against ${percent(withoutCaps.random)}.`,
    `The caps add ${percent(found.caps_add)}; its entries add ${percent(found.entries_add_with_caps)} over random ones with the caps, ${percent(found.entries_add_without_caps)} without them.`,
  ]
}

export interface TailRow {
  label: string
  observed: string
  bestFirst: string
  results: string
}

/** The tail's figures side by side: the run's own days, then its twin's days replayed under its caps both ways. */
export function tailRows(tail: BacktestDecomposition['tail']): TailRow[] {
  const columns = (read: (figures: BacktestTailFigures) => string) => ({
    observed: read(tail.observed),
    bestFirst: tail.bounds.best_first ? read(tail.bounds.best_first) : '—',
    results: tail.bounds.results ? read(tail.bounds.results) : '—',
  })
  return [
    { label: 'Days that lost', ...columns((figures) => share(figures.losing_share)) },
    ...tail.levels.map((level) => ({
      label: `Days at ${formatNumber(level)} % or worse`,
      ...columns((figures) => share(figures.at_or_under[String(level)])),
    })),
    { label: 'The worst 1 % of days at', ...columns((figures) => percent(figures.p1)) },
    { label: 'Days at their target', ...columns((figures) => share(figures.at_target_share)) },
  ]
}
