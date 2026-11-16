import { describe, expect, it } from 'vitest'

import type { LivePaperDay } from '@/lib/types'

import { paperState, referenceText } from './livePaper'

const day = (changes: Partial<LivePaperDay>): LivePaperDay => ({
  day: '2026-10-19', real_percent: null, paper_percent: -1.2,
  paper_state: 'done', paper_run_id: 'r1', paper_trades: 3, ...changes,
})

describe('the live settings’ paper days', () => {
  it('says how to choose a reference, and which one Edge Watch judges the live trades against', () => {
    expect(referenceText(null)).toContain('Set as the live reference')
    expect(referenceText({ run_id: 'r', name: 'September', days: 20, done: true, first_day: '2026-09-01', last_day: '2026-09-30', stale: false, made_with: '0.85.0' }))
      .toBe('The live reference: September, 20 days. Edge Watch, above, judges the live trades against it.')
  })

  it('names a paper day without a result', () => {
    expect(paperState(day({}))).toBeNull()
    expect(paperState(day({ paper_state: 'no_entry', paper_percent: 0 }))).toBe('No entry signal')
    expect(paperState(day({ paper_state: 'running', paper_percent: null }))).toBe('Running')
    expect(paperState(day({ paper_state: 'failed', paper_percent: null }))).toBe('Not traded')
    expect(paperState(day({ paper_state: null, paper_percent: null }))).toBe('—')
  })
})
