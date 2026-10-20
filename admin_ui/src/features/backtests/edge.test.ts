import { describe, expect, it } from 'vitest'

import type { BacktestEdge, BacktestEdgeHorizon } from '@/lib/types'

import { basisPoints, edgeRows, edgeSplits, edgeVerdict, horizonLabel } from './edge'

function horizon(minutes: number, changes: Partial<BacktestEdgeHorizon> = {}, beaten: number | null = 60): BacktestEdgeHorizon {
  return {
    minutes,
    signals: 40,
    mean_bp: 1.2,
    median_bp: 0.8,
    hit_rate: 52,
    best_bp: 9,
    worst_bp: -7,
    independent: 31,
    t: 0.9,
    random: { mean_bp: 0.1, hit_rate: 50, beaten, draws: 20 },
    ...changes,
  }
}

function edge(horizons: BacktestEdgeHorizon[]): BacktestEdge {
  const group = (key: string, value: unknown) => ({
    [key]: value,
    ...Object.fromEntries(horizons.map((row) => [String(row.minutes), { signals: 20, mean_bp: row.minutes / 10, hit_rate: 55 }])),
  })
  return {
    horizons,
    by_hour: [group('hour', 9), group('hour', 15)],
    by_direction: [group('direction', 'long')],
    by_fate: [group('fate', 'executed'), group('fate', 'not_taken')],
    unpriced: 0,
    draws: 20,
    seed: 1,
  } as BacktestEdge
}

describe('the edge of the entries', () => {
  it('is an edge only where most random draws are beaten with a t-statistic of 2.6, the best of five horizons being picked', () => {
    const found = edgeVerdict(edge([horizon(1), horizon(15, { mean_bp: 3.2, hit_rate: 54, t: 2.7 }, 97.5), horizon(60)]))
    expect(found.tone).toBe('edge')
    expect(found.minutes).toBe(15)
    expect(found.sentence).toBe(
      "After 15 min the asset had moved +3.20 bp in the signals' direction on average, 54 % of them right, against +0.10 bp at random moments of the same hours: better than 97.5 % of the random draws (t 2.7 over the 31 signals whose windows do not overlap).",
    )
  })

  it('is not one when the signals do no better than chance, or are too few', () => {
    const none = edgeVerdict(edge([horizon(1), horizon(5, {}, 72)]))
    expect(none.tone).toBe('none')
    expect(none.sentence).toMatch(/After 5 min .* what the trades made came from the exits and the days\.$/)
    // Beating the draws without signals apart enough is no edge either, nor a t of 2 at the best of five horizons.
    expect(edgeVerdict(edge([horizon(1, { t: null }, 99)])).sentence).toMatch(/or the signals are too few apart\.$/)
    expect(edgeVerdict(edge([horizon(1, { t: 2.4 }, 100)])).tone).toBe('none')
    expect(edgeVerdict(edge([horizon(1, { t: 2.4 }, 100)])).sentence).toContain('better than all of the random draws')
    const few = edgeVerdict(edge([horizon(1, { signals: 6 })]))
    expect(few).toEqual({ tone: 'unknown', minutes: null, sentence: '6 entry signals with a price of the asset: too few to tell an edge, which takes dozens.' })
  })

  it('splits the moves by hour, direction, and whether the trader took the signal', () => {
    const found = edge([horizon(1), horizon(60)])
    expect(edgeRows(found, 'hour').map((row) => row.label)).toEqual(['09:00', '15:00'])
    expect(edgeRows(found, 'fate')).toEqual([
      { label: 'Taken', signals: 20, moves: [0.1, 6] },
      { label: 'Not taken', signals: 20, moves: [0.1, 6] },
    ])
  })

  it('splits by the kind of day once a run has measured it', () => {
    const found = edge([horizon(1), horizon(60)])
    expect(edgeSplits(found)).toEqual(['fate', 'direction', 'hour'])
    const withDays = {
      ...found,
      by_day: { news: [{ group: 'CPI', '1': { signals: 3, mean_bp: 2, hit_rate: 66 }, '60': { signals: 3, mean_bp: 5, hit_rate: 66 } }], gap: [] },
    } as unknown as BacktestEdge
    expect(edgeSplits(withDays)).toEqual(['fate', 'direction', 'hour', 'news'])
    expect(edgeRows(withDays, 'news')).toEqual([{ label: 'CPI', signals: 3, moves: [2, 5] }])
  })

  it('names its horizons and basis points', () => {
    expect([horizonLabel(5), horizonLabel(60)]).toEqual(['5 min', '1 h'])
    expect([basisPoints(3.2), basisPoints(-0.5), basisPoints(0), basisPoints(null)]).toEqual(['+3.20 bp', '−0.50 bp', '0.00 bp', '-'])
  })
})
