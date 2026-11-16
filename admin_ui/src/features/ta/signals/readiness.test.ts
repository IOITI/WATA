import { describe, expect, it } from 'vitest'

import type { EdgeOwner, EdgeTrack, EdgeWatchList, RuleCheckSummary, TaRule, TaRuleState } from '@/lib/types'

import { edgeOf, holdingBack, liveReadiness, scriptAsRule, weekdaysBetween } from './readiness'

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
const READY: RuleCheckSummary = {
  verdict: 'ready', checked_at: '2026-10-28T20:00:00Z', counts: {}, worst: null, tested_at: null, tested_bars: null, candidates: 0, as_rule: false,
}
const detail = (checks: ReturnType<typeof liveReadiness>, id: string) => checks.find((check) => check.id === id)?.detail

/** A rule's Edge Watch: its paper trades as its reference led to expect, unless said otherwise. */
function edge(track: Partial<EdgeTrack> = {}): EdgeOwner {
  return {
    owner: 'aaaaaaaaaaaa', name: 'Cross on US100', mode: 'shadow', reference: null, since: '2026-10-10', model: null,
    verdict: track.verdict ?? 'as_expected', main: 'paper',
    tracks: {
      paper: {
        verdict: 'as_expected', reasons: ["its trades are what its reference led to expect"], n: 24, cumulative: 1.2, rank: 40,
        drawdown: -3, drawdown_rank: 60, streak: 2, streak_rank: 70, cusum: 0.4, h: 3, alarm_at: null, trades_to_tell: 90,
        progress: 0.27, settings_differ: [], ...track,
      },
    },
  }
}

describe('whether a rule is ready to go live', () => {
  it('counts the weekdays after a day', () => {
    // Wednesday 21 to Monday 26 October: Thursday, Friday and Monday.
    expect(weekdaysBetween('2026-10-21', '2026-10-26')).toBe(3)
    expect(weekdaysBetween('2026-10-21', '2026-10-21')).toBe(0)
  })

  it('says of a rule a day in shadow, with no signal yet, what is still missing', () => {
    // The server's rule on 22 October: in shadow since the evening before, no signal, no reference run.
    const checks = liveReadiness(rule(), state(), null, Date.parse('2026-10-22T07:03:21+02:00'), ZONE)

    expect(states(checks)).toEqual({ shadow: 'open', signals: 'fail', edge_watch: 'fail', engine: 'pass', rule_check: 'open', script: 'pass' })
    expect(detail(checks, 'shadow')).toBe('In shadow since 21 Oct 2026: 1 weekday of 5.')
    expect(detail(checks, 'signals')).toBe('None yet: what it would send is not known.')
    expect(detail(checks, 'edge_watch')).toBe('No reference run to judge its paper trades against (set one from a run of the rule).')
    expect(detail(checks, 'engine')).toBe('Running, its last bar 22 Oct, 07:02.')
  })

  it('counts its week in shadow again from a change of what it runs', () => {
    const now = Date.parse('2026-10-29T09:00:00Z')
    const changed = liveReadiness(rule({ run_changed_at: '2026-10-27T10:00:00Z' }), state(), undefined, now, ZONE)
    expect(states(changed).shadow).toBe('open')
    expect(detail(changed, 'shadow')).toBe('What it runs changed on 27 Oct 2026: 2 weekdays in shadow since, of 5.')
    const before = liveReadiness(rule({ run_changed_at: '2026-10-20T10:00:00Z' }), state(), undefined, now, ZONE)
    expect(detail(before, 'shadow')).toBe('In shadow since 21 Oct 2026: 6 weekdays of 5.')
  })

  it('passes a rule a week in shadow whose paper trades are what its reference led to expect', () => {
    const checks = liveReadiness(rule(), state({ last_alert_at: '2026-10-28T14:00:00Z' }), edge(), Date.parse('2026-10-29T09:00:00Z'), ZONE, READY)
    expect(states(checks)).toEqual({ shadow: 'pass', signals: 'pass', edge_watch: 'pass', engine: 'pass', rule_check: 'pass', script: 'pass' })
    expect(detail(checks, 'edge_watch')).toBe('As expected after 24 trades, +1.20%: consistent with its reference, which is no proof of an edge.')
  })

  it('fails a rule the engine cannot run, whose paper trades look random, and says a newer version is saved', () => {
    const checks = liveReadiness(
      rule({ mode: 'off', latest: { script: 6, libraries: {} } }),
      state({ state: 'error', detail: 'Undeclared identifier (script, line 4)' }),
      edge({ verdict: 'broken', n: 31, rank: 2, cumulative: -18.4 }),
      Date.parse('2026-10-29T09:00:00Z'),
      ZONE,
      { ...READY, verdict: 'repaints', worst: { id: 'repaints-at-the-bar', level: 'repaints', message: '…' } },
    )
    expect(states(checks)).toEqual({ shadow: 'fail', signals: 'fail', edge_watch: 'fail', engine: 'fail', rule_check: 'fail', script: 'open' })
    expect(detail(checks, 'engine')).toBe('Failing: Undeclared identifier (script, line 4).')
    expect(detail(checks, 'edge_watch')).toBe("Its paper trades look like random entries', not like its reference's.")
    expect(detail(checks, 'script')).toBe('Version 6 of its script is saved: it runs version 5.')
    // All but the script's version hold it back, as the server's unmet_readiness does.
    expect(holdingBack(checks).map((check) => check.id)).toEqual(['shadow', 'signals', 'edge_watch', 'engine', 'rule_check'])
  })

  it('holds back what the server holds back: a week in shadow and its paper trades until they pass, the others once they failed', () => {
    const fresh = liveReadiness(rule(), state(), edge({ verdict: 'too_early', n: 7 }), Date.parse('2026-10-22T07:03:21+02:00'), ZONE)
    expect(states(fresh).edge_watch).toBe('open')
    expect(detail(fresh, 'edge_watch')).toBe('7 paper trade(s) since its reference of 20.')
    expect(holdingBack(fresh).map((check) => check.id)).toEqual(['shadow', 'signals', 'edge_watch'])

    // A week in shadow, a signal journaled, its paper trades as expected, a newer script saved: nothing held.
    const ready = liveReadiness(
      rule({ latest: { script: 6, libraries: {} } }),
      state({ last_alert_at: '2026-10-28T14:00:00Z' }),
      edge(),
      Date.parse('2026-10-29T09:00:00Z'),
      ZONE,
      READY,
    )
    expect(holdingBack(ready)).toEqual([])
    // Edge Watch not read (the backtests off): held back, as the server holds it.
    const unread = liveReadiness(rule(), state({ last_alert_at: '2026-10-28T14:00:00Z' }), undefined, Date.parse('2026-10-29T09:00:00Z'), ZONE, READY)
    expect(holdingBack(unread).map((check) => check.id)).toEqual(['edge_watch'])
  })

  it('finds a rule’s Edge Watch in the list: null without a reference, undefined while not read', () => {
    const list: EdgeWatchList = { live: null, rules: { aaaaaaaaaaaa: edge() }, judged_at: '2026-10-29T09:00:00Z', guard: { on: false, least_trades: 20 } }
    expect(edgeOf(list, 'aaaaaaaaaaaa', true)).toBe(list.rules.aaaaaaaaaaaa)
    expect(edgeOf(list, 'bbbbbbbbbbbb', true)).toBeNull()
    expect(edgeOf(undefined, 'aaaaaaaaaaaa', true)).toBeUndefined()
    expect(edgeOf(list, 'aaaaaaaaaaaa', false)).toBeUndefined()
  })

  it('says its script as a rule as the server holds it: what cannot be one, repaints, or was never tested as it runs', () => {
    const said = (changes: Partial<RuleCheckSummary>) => scriptAsRule({ ...READY, ...changes })

    expect(said({ verdict: 'needs_alerts', worst: { id: 'no-alerts', level: 'blocks', message: '…' } })).toMatchObject({
      state: 'fail', detail: 'Its script cannot be a rule as it is: no alert.',
    })
    expect(said({ verdict: 'blocked', worst: { id: 'live-ticker', level: 'blocks', message: '…' } }).detail).toBe(
      'Its script cannot be a rule as it is: a series WATA reads only in backtests.',
    )
    expect(said({ verdict: 'may_repaint' })).toMatchObject({
      state: 'fail', detail: 'Its script may repaint, and was never tested on recorded bars as this rule runs it.',
    })
    expect(said({ verdict: 'may_repaint', as_rule: true }).detail).toBe(
      'Its script may repaint: its run test as this rule runs it had no alert to compare.',
    )
    expect(said({ verdict: 'to_change', as_rule: true })).toMatchObject({
      state: 'pass', detail: 'Its script can be a signal rule, tested on recorded bars as this rule runs it.',
    })
    // Being read, or made by the server first: nothing to hold back on yet, the server checks it again.
    expect(scriptAsRule(undefined).state).toBe('open')
    expect(scriptAsRule(null).detail).toBe('Its rule check is being made: the server makes it before the switch.')
  })
})
