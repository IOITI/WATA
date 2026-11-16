import { describe, expect, it } from 'vitest'

import type { BacktestPaper, EdgeOwner } from '@/lib/types'

import { paperLine } from './paper'

const ENTRY: BacktestPaper['rules'][string] = {
  days: [
    { day: '2026-10-19', run_id: 'r3', state: 'done', day_percent: 0.4, trades: 2 },
    { day: '2026-10-16', run_id: 'r2', state: 'done', day_percent: -6, trades: 3 },
    { day: '2026-10-15', run_id: 'r1', state: 'queued', day_percent: null, trades: null },
  ],
  reference: { run_id: 'ref', name: 'EMA cross, 3 months', days: 60, done: true, first_day: '2026-07-14', last_day: '2026-10-09', stale: false, made_with: '0.85.0' },
}

describe('a rule traded on paper', () => {
  it('says its days, the last one, and its reference before Edge Watch has judged it', () => {
    expect(paperLine(ENTRY, 'shadow')).toEqual({
      total: '2 days, −5.60%',
      report: null,
      last: 'Last, 19 Oct: +0.40% in 2 trades',
      reference: 'Its reference (EMA cross, 3 months): Edge Watch judges its paper trades after it.',
      tone: 'neutral',
    })
  })

  it('says what Edge Watch says of its paper trades, in its tone', () => {
    const edge: EdgeOwner = {
      owner: 'r', name: 'EMA cross', mode: 'shadow', reference: null, since: '2026-10-10', model: null, verdict: 'watch', main: 'paper',
      tracks: {
        paper: {
          verdict: 'watch', reasons: [], n: 24, cumulative: -3.1, rank: 4, drawdown: -6, drawdown_rank: 10, streak: 4, streak_rank: 9,
          cusum: 2, h: 5, alarm_at: null, trades_to_tell: 80, progress: 0.3, settings_differ: [],
        },
      },
    }
    expect(paperLine(ENTRY, 'shadow', edge)).toMatchObject({ reference: 'Edge Watch: Worth watching after 24 trades, −3.10%.', tone: 'warning' })
    const fine = { ...edge, tracks: { paper: { ...edge.tracks.paper!, verdict: 'as_expected' as const } } }
    expect(paperLine(ENTRY, 'shadow', fine)?.tone).toBe('ok')
  })

  it('without a reference, or before its first day', () => {
    expect(paperLine({ ...ENTRY, reference: null }, 'shadow')?.reference).toBe('No reference run: set one from a run made from this rule.')
    expect(paperLine(undefined, 'shadow')?.total).toBe('Each finished day, once the market is closed')
    expect(paperLine(undefined, 'live')).toBeNull()
  })
})

describe('a rule with its report', () => {
  const REPORT = {
    run_id: '20261020-213000-abcdef', from: '2026-10-14', to: '2026-10-19', made_at: '2026-10-20T21:05:00Z', signals: 12,
    versions: [3], headline: {
      total_percent: 1.18, trades: 9, win_rate: 55.5, worst_drawdown: -2.1, sample: 'few' as const, beaten: null,
      days_tested: 4, profit_loss: 11.8, cells: null,
    },
  }

  it('says the report since it was made, compounded, in place of its days added up', () => {
    const line = paperLine({ ...ENTRY, report: REPORT, next_report: null }, 'shadow')

    expect(line?.report).toBe('Since 14 Oct: +1.18% in 9 trades')
    // Its days are still said, the last one linked.
    expect(line?.last).toBe('Last, 19 Oct: +0.40% in 2 trades')
  })

  it('says it even before the days kept come back', () => {
    expect(paperLine({ days: [], reference: null, report: REPORT, next_report: null }, 'live')?.report).toBe('Since 14 Oct: +1.18% in 9 trades')
  })
})
