import { describe, expect, it } from 'vitest'

import type { BacktestReconciliationSummary } from '@/lib/types'

import { reconciliationVerdict } from './reconciliation'

const SUMMARY: BacktestReconciliationSummary = {
  paired: 20,
  backtest_only: 1,
  live_only: 2,
  backtest_only_live_outcomes: { refused: 1 },
  live_only_backtest_fates: { refused: 1, not_replayed: 1 },
  mean_result_difference: 0.3,
  median_result_difference: 0.25,
  correlation: 0.9,
  same_exit_percent: 85,
  same_turbo_percent: 90,
  median_entry_seconds: 1.4,
  backtest_total_percent: 12.4,
  real_total_percent: 9.1,
}

describe('a replay beside the real trades', () => {
  it('says how far apart they are, and what each side did of the other’s trades', () => {
    const found = reconciliationVerdict(SUMMARY)
    expect(found.tone).toBe('close')
    expect(found.sentences).toEqual([
      '20 trades set beside WATA’s positions of the same signals: the backtest made +12.40% where WATA made +9.10%; a backtest trade made 0.25 points more than WATA’s in the middle.',
      'The same exit 85 % of the time, the same turbo 90 %; bought 1.4 s after WATA in the middle.',
      'Only the backtest traded 1: 1 refused by WATA.',
      'Only WATA traded 2: 1 refused by the backtest’s rules, 1 outside the days replayed.',
    ])
  })

  it('says when the backtest bought before WATA', () => {
    expect(reconciliationVerdict({ ...SUMMARY, median_entry_seconds: -1.4 }).sentences[1]).toBe(
      'The same exit 85 % of the time, the same turbo 90 %; bought 1.4 s before WATA in the middle.',
    )
  })

  it('is apart when the exits differ or many trades are one side’s only', () => {
    expect(reconciliationVerdict({ ...SUMMARY, same_exit_percent: 50 }).tone).toBe('apart')
    expect(reconciliationVerdict({ ...SUMMARY, live_only: 8 }).tone).toBe('apart')
    expect(reconciliationVerdict({ ...SUMMARY, paired: 0 })).toEqual({
      tone: 'none',
      sentences: ['No backtest trade could be set beside a real position of the same signal.'],
    })
  })
})
