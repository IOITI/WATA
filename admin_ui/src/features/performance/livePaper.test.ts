import { describe, expect, it } from 'vitest'

import type { LivePaperDay } from '@/lib/types'

import { outsideDays, paperState, referenceText } from './livePaper'

const day = (changes: Partial<LivePaperDay>): LivePaperDay => ({
  day: '2026-10-19', real_percent: null, real_outside: null, paper_percent: -1.2, paper_outside: false,
  paper_state: 'done', paper_run_id: 'r1', paper_trades: 3, ...changes,
})

describe('the live settings’ paper days', () => {
  it('says how to choose a reference, and what a reference’s range is', () => {
    expect(referenceText(null)).toContain('Set as the live reference')
    expect(referenceText({ run_id: 'r', name: 'September', days: 3, low: null, median: null, high: null }))
      .toBe('September has 3 days with a result: 5 are needed for a range.')
    expect(referenceText({ run_id: 'r', name: 'September', days: 20, low: -2, median: 0.1, high: 1.5 }))
      .toBe('Set against September: its days made −2.00% to +1.50% (5 % to 95 % of 20 days).')
  })

  it('names a paper day without a result', () => {
    expect(paperState(day({}))).toBeNull()
    expect(paperState(day({ paper_state: 'no_entry', paper_percent: 0 }))).toBe('No entry signal')
    expect(paperState(day({ paper_state: 'running', paper_percent: null }))).toBe('Running')
    expect(paperState(day({ paper_state: 'failed', paper_percent: null }))).toBe('Not traded')
    expect(paperState(day({ paper_state: null, paper_percent: null }))).toBe('—')
  })

  it('lists the days outside the range', () => {
    expect(outsideDays([day({ day: '2026-10-20', real_outside: true }), day({}), day({ day: '2026-10-16', paper_outside: true })]))
      .toEqual(['2026-10-20', '2026-10-16'])
  })
})
