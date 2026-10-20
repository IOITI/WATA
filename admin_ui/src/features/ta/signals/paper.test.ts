import { describe, expect, it } from 'vitest'

import type { BacktestPaper } from '@/lib/types'

import { paperLine } from './paper'

const ENTRY: BacktestPaper['rules'][string] = {
  days: [
    { day: '2026-10-19', run_id: 'r3', state: 'done', day_percent: 0.4, trades: 2, outside: false },
    { day: '2026-10-16', run_id: 'r2', state: 'done', day_percent: -6, trades: 3, outside: true },
    { day: '2026-10-15', run_id: 'r1', state: 'queued', day_percent: null, trades: null, outside: null },
  ],
  reference: { run_id: 'ref', name: 'EMA cross, 3 months', days: 60, low: -1.5, median: 0.2, high: 2.75 },
}

describe('a rule traded on paper', () => {
  it('says its days, the last one, and those outside its reference', () => {
    expect(paperLine(ENTRY, 'shadow')).toEqual({
      total: '2 days, −5.60%',
      last: 'Last, 19 Oct: +0.40% in 2 trades',
      reference: 'Its reference (EMA cross, 3 months) made −1.50% to +2.75% on 90 % of its days.',
      outside: 1,
      tone: 'warning',
    })
  })

  it('without a reference, or before its first day', () => {
    expect(paperLine({ ...ENTRY, reference: null }, 'shadow')?.reference).toBe('No reference run: set one from a run made from this rule.')
    expect(paperLine(undefined, 'shadow')?.total).toBe('Each finished day, once the market is closed')
    expect(paperLine(undefined, 'live')).toBeNull()
  })
})
