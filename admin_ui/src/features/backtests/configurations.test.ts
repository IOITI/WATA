import { describe, expect, it } from 'vitest'

import { configuration, configurationRun, listedRun, RUN_ID } from '@/test/backtests'
import type { BacktestNightly } from '@/lib/types'

import {
  bringUpToDate,
  configurationFilterOf,
  configurationJudged,
  daysBetween,
  distinctSaid,
  earlierStress,
  filterConfigurations,
  historyTotals,
  keptSentence,
  lastSentence,
  nextSentence,
  nightlySentence,
  windowSentence,
} from './configurations'

function nightly(changes: Partial<BacktestNightly> = {}): BacktestNightly {
  return {
    on: true, from: '2026-10-15', since: '2026-11-19T06:00:00Z', telegram: false, most_days: 365, last: null, up_to_date: false,
    next: { day: '2026-11-19', from: '2026-10-15', to: '2026-11-19', waits: null },
    ...changes,
  }
}

describe('the list of configurations', () => {
  const mine = configuration({ id: 'aaaaaaaaaaaa', favourite: true, tags: ['kernel'], nightly: nightly() })
  const claudes = configuration({
    id: 'bbbbbbbbbbbb', made_by: ['claude'], mine: false, experiments: ['a4b020'], turbos: 'modelled',
    runs: [configurationRun({ state: 'failed' })], latest: listedRun({ stale: true }), nightly: nightly({ on: false }),
  })
  const items = [mine, claudes]

  it('is narrowed as its address says', () => {
    const narrowed = (query: string) => filterConfigurations(items, configurationFilterOf(new URLSearchParams(query))).map((item) => item.id)
    expect(narrowed('favourite=yes')).toEqual(['aaaaaaaaaaaa'])
    expect(narrowed('by=claude')).toEqual(['bbbbbbbbbbbb'])
    expect(narrowed('by=me')).toEqual(['aaaaaaaaaaaa'])
    expect(narrowed('experiment=a4b020')).toEqual(['bbbbbbbbbbbb'])
    expect(narrowed('turbos=modelled')).toEqual(['bbbbbbbbbbbb'])
    expect(narrowed('code=older')).toEqual(['bbbbbbbbbbbb'])
    expect(narrowed('state=failed')).toEqual(['bbbbbbbbbbbb'])
    expect(narrowed('tag=kernel')).toEqual(['aaaaaaaaaaaa'])
    expect(narrowed('nightly=on')).toEqual(['aaaaaaaaaaaa'])
    expect(narrowed('nightly=off')).toEqual(['bbbbbbbbbbbb'])
    expect(narrowed('nightly=sometimes')).toEqual(['aaaaaaaaaaaa', 'bbbbbbbbbbbb'])
  })

  it('is narrowed by verdict as the runs are', () => {
    const verdicts = new Map([['aaaaaaaaaaaa', 'pass' as const]])
    expect(filterConfigurations(items, configurationFilterOf(new URLSearchParams('verdict=pass')), verdicts).map((item) => item.id)).toEqual([
      'aaaaaaaaaaaa',
    ])
  })

  it('judges each configuration by its latest result, read with the runs that judge it', () => {
    const stress = listedRun({ id: 'stress', kind: 'sweep', stress_of: RUN_ID })
    const judged = configurationJudged([configuration({ judging: [stress] }), configuration({ id: 'none', latest: null })])
    expect([...judged.keys()]).toEqual([configuration().id])
    const stressCheck = (found: typeof judged) => found.get(configuration().id)?.checks.find((check) => check.id === 'stress')?.detail
    // Its stress is read: what the check says is not what it says of a run never stressed.
    expect(stressCheck(judged)).not.toEqual(stressCheck(configurationJudged([configuration()])))
  })
})

describe('what is said of a nightly re-run', () => {
  it('counts both of its days', () => {
    expect(daysBetween('2026-10-15', '2026-11-18')).toBe(35)
    expect(windowSentence('2026-10-15', '2026-11-18', 365)).toBe('15 Oct 2026 to 18 Nov 2026, 35 days (365 at most, then its first day moves on)')
  })

  it('says whether it is on, from when and how far', () => {
    expect(nightlySentence(null)).toBe('')
    expect(nightlySentence(nightly({ on: false }))).toMatch(/^Not made again by itself/)
    expect(nightlySentence(nightly())).toBe('Made again after each trading day, from 15 Oct 2026: 365 days at most, then its first day moves on.')
  })

  it('says when the next comes and what it waits for', () => {
    expect(nextSentence(nightly({ on: false }))).toBe('')
    expect(nextSentence(nightly({ next: { day: '2026-11-19', from: '2026-10-15', to: '2026-11-19', waits: 'market' } }))).toBe(
      'Next: 15 Oct to 19 Nov, once Thursday’s trading hours are over.',
    )
    expect(nextSentence(nightly({ next: { day: '2026-11-19', from: '2026-10-15', to: '2026-11-19', waits: 'line' } }))).toMatch(/runs you asked for/)
    expect(nextSentence(nightly())).toBe('Next: 15 Oct to 19 Nov, in a few minutes.')
    const failed = nightly({
      next: { day: '2026-11-19', from: '2026-10-15', to: '2026-11-19', waits: 'failed' },
      last: { day: '2026-11-19', run: RUN_ID, asked_at: '2026-11-19T21:00:00Z', state: 'failed', error: 'Stopped', seconds: 10, by_hand: false },
    })
    expect(nextSentence(failed)).toMatch(/^The run to 19 Nov did not finish/)
    expect(lastSentence(failed)).toBe('Its run to 19 Nov did not finish: Stopped.')
  })

  it('says how its last one ended', () => {
    const last = { day: '2026-11-18', run: RUN_ID, asked_at: '2026-11-18T21:00:00Z', state: 'done' as const, error: null, seconds: 410, by_hand: true }
    expect(lastSentence(nightly({ last }))).toBe('Its run to 18 Nov took 6 min 50 s, asked by hand.')
    expect(lastSentence(nightly({ last: { ...last, run: null, state: null, error: 'WATA received no signal of entry in that period' } }))).toBe(
      'The run to 18 Nov was not asked, asked by hand: WATA received no signal of entry in that period.',
    )
    expect(lastSentence(nightly())).toBe('')
  })
})

describe('a configuration’s runs', () => {
  it('say why a nightly run stayed beside the newer one', () => {
    expect(keptSentence(configurationRun({ nightly: true, kept: ['favourite', 'reference'] }))).toBe('kept: a favourite, a reference')
    expect(keptSentence(configurationRun({ nightly: false, kept: ['reference'] }))).toBe('')
  })

  it('say what sets a configuration apart from those of its strategy', () => {
    expect(distinctSaid([{ name: 'capital', value: 100 }, { name: 'trailing_enabled', value: false }])).toBe('capital 100 · trailing enabled off')
  })

  it('chart what its nightly runs said, oldest first', () => {
    const line = { run: 'x', name: null, made_at: null, from: '2026-10-15', days: 1, trades: 1, profit_loss: 1, beaten: 1,
      worst_drawdown: 0, win_rate: 1, seconds: 1, version: null, by_hand: false }
    expect(historyTotals([{ ...line, to: '2026-11-19', total_percent: 3 }, { ...line, to: '2026-11-18', total_percent: null },
      { ...line, to: '2026-11-17', total_percent: 1 }])).toEqual([1, 3])
  })

  it('name the newest stress of an earlier run when the latest has none', () => {
    const stressed = configurationRun({ id: 'old-stress', derived: 'stress', to: '2026-11-12' })
    expect(earlierStress(configuration({ runs: [configurationRun(), stressed] }))?.id).toBe('old-stress')
    expect(earlierStress(configuration({ runs: [stressed], judging: [listedRun({ stress_of: RUN_ID })] }))).toBeNull()
  })
})

describe('bringing a configuration up to date', () => {
  it('is possible once a trading day is over that no run covers', () => {
    expect(bringUpToDate(configuration(), '2026-10-20')).toEqual({ can: true, why: 'Asks its run to 20 Oct now, in line with your runs' })
  })

  it('is not, said why, for a sweep, a run being made, or days covered already', () => {
    expect(bringUpToDate(configuration({ why_no_nightly: 'A sweep is not made again' }), '2026-10-20').can).toBe(false)
    expect(bringUpToDate(configuration({ nightly: nightly({ next: { day: 'x', from: 'x', to: 'x', waits: 'under_way' } }) }), '2026-10-20').why).toMatch(/being made/)
    expect(bringUpToDate(configuration({ nightly: nightly({ up_to_date: true }) }), '2026-10-20').why).toMatch(/covers the days up to 20 Oct/)
  })
})
