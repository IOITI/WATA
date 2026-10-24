import { describe, expect, it } from 'vitest'

import type { BacktestPaper, TaRule, TaRuleState } from '@/lib/types'

import { holdingBack, liveReadiness, weekdaysBetween } from './readiness'

const ZONE = 'Europe/Paris'

function rule(changes: Partial<TaRule> = {}): TaRule {
  return {
    id: 'aaaaaaaaaaaa', name: 'Cross on US100', asset: { provider: 'capital', key: 'US100' }, side: 'bid',
    bars: { type: 'range', ticks: 60 }, script: { id: 'cccccccccccc', version: 5, sha256: 'x' }, libraries: [], inputs: [],
    alerts: [{ title: 'Up', action: 'long' }], alert_calls: 'ignore', confidence_plot: null, indice: 'us100', mode: 'shadow',
    created_at: '2026-10-21T20:30:20Z', updated_at: '2026-10-21T20:30:20Z', mode_changed_at: '2026-10-21T20:30:20Z',
    script_name: 'Cross', latest: { script: 5, libraries: {} },
    ...changes,
  }
}

function state(changes: Partial<TaRuleState> = {}): TaRuleState {
  return {
    updated_at: '2026-10-22T05:03:11Z', state: 'running', detail: null, mode: 'shadow', script_version: 5, bars: 1200,
    last_bar_time: '2026-10-22T05:02:40Z', last_alert_at: null, figures: {},
    ...changes,
  }
}

const states = (checks: ReturnType<typeof liveReadiness>) => Object.fromEntries(checks.map((check) => [check.id, check.state]))
const detail = (checks: ReturnType<typeof liveReadiness>, id: string) => checks.find((check) => check.id === id)?.detail

describe('whether a rule is ready to go live', () => {
  it('counts the weekdays after a day', () => {
    // Wednesday 21 to Monday 26 October: Thursday, Friday and Monday.
    expect(weekdaysBetween('2026-10-21', '2026-10-26')).toBe(3)
    expect(weekdaysBetween('2026-10-21', '2026-10-21')).toBe(0)
  })

  it('says of a rule a day in shadow, with no signal yet, what is still missing', () => {
    // The server's rule on 22 October: in shadow since the evening before, no signal, one day on paper, no reference.
    const paper: BacktestPaper['rules'][string] = {
      days: [{ day: '2026-10-21', run_id: '20261021-221446-206dc7', state: 'done', day_percent: 0, trades: 0, outside: null }],
      reference: null,
    }
    const checks = liveReadiness(rule(), state(), paper, Date.parse('2026-10-22T07:03:21+02:00'), ZONE)

    expect(states(checks)).toEqual({ shadow: 'open', signals: 'fail', paper: 'open', engine: 'pass', script: 'pass' })
    expect(detail(checks, 'shadow')).toBe('In shadow since 21 Oct 2026: 1 weekday of 5.')
    expect(detail(checks, 'signals')).toBe('None yet: what it would send is not known.')
    expect(detail(checks, 'paper')).toBe('1 day on paper, and no reference run to set them against: make one a run of this rule.')
    expect(detail(checks, 'engine')).toBe('Running, its last bar 22 Oct, 07:02.')
  })

  it('passes a rule a week in shadow, its days on paper within its reference’s range', () => {
    const paper: BacktestPaper['rules'][string] = {
      days: [
        { day: '2026-10-28', run_id: 'r2', state: 'done', day_percent: 0.4, trades: 2, outside: false },
        { day: '2026-10-27', run_id: 'r1', state: 'done', day_percent: -0.2, trades: 1, outside: false },
      ],
      reference: { run_id: 'ref', name: 'Cross, 3 months', days: 60, low: -1.5, median: 0.2, high: 2.75 },
    }
    const checks = liveReadiness(rule(), state({ last_alert_at: '2026-10-28T14:00:00Z' }), paper, Date.parse('2026-10-29T09:00:00Z'), ZONE)
    expect(states(checks)).toEqual({ shadow: 'pass', signals: 'pass', paper: 'pass', engine: 'pass', script: 'pass' })
  })

  it('fails a rule the engine cannot run, with days outside its range, and says a newer version is saved', () => {
    const paper: BacktestPaper['rules'][string] = {
      days: [{ day: '2026-10-28', run_id: 'r2', state: 'done', day_percent: -6, trades: 3, outside: true }],
      reference: { run_id: 'ref', name: null, days: 60, low: -1.5, median: 0.2, high: 2.75 },
    }
    const checks = liveReadiness(
      rule({ mode: 'off', latest: { script: 6, libraries: {} } }),
      state({ state: 'error', detail: 'Undeclared identifier (script, line 4)' }),
      paper,
      Date.parse('2026-10-29T09:00:00Z'),
      ZONE,
    )
    expect(states(checks)).toEqual({ shadow: 'fail', signals: 'fail', paper: 'fail', engine: 'fail', script: 'open' })
    expect(detail(checks, 'engine')).toBe('Failing: Undeclared identifier (script, line 4).')
    expect(detail(checks, 'paper')).toBe('1 of 1 day on paper fell outside the range of its reference run.')
    expect(detail(checks, 'script')).toBe('Version 6 of its script is saved: it runs version 5.')
    // All but the script's version hold it back, as the server's unmet_readiness does.
    expect(holdingBack(checks).map((check) => check.id)).toEqual(['shadow', 'signals', 'paper', 'engine'])
  })

  it('holds back what the server holds back: a week in shadow until it has passed, the others once they failed', () => {
    const fresh = liveReadiness(rule(), state(), undefined, Date.parse('2026-10-22T07:03:21+02:00'), ZONE)
    expect(holdingBack(fresh).map((check) => check.id)).toEqual(['shadow', 'signals'])

    // A week in shadow, a signal journaled, nothing on paper yet, a newer script saved: nothing held.
    const ready = liveReadiness(
      rule({ latest: { script: 6, libraries: {} } }),
      state({ last_alert_at: '2026-10-28T14:00:00Z' }),
      undefined,
      Date.parse('2026-10-29T09:00:00Z'),
      ZONE,
    )
    expect(states(ready)).toEqual({ shadow: 'pass', signals: 'pass', paper: 'open', engine: 'pass', script: 'open' })
    expect(holdingBack(ready)).toEqual([])
  })
})
