import { describe, expect, it } from 'vitest'

import type { RuleCheckReport, RuleChecksState, RuleCheckSummary, RuleFinding, RuleTested } from '@/lib/types'

import { ruleCheckOf, triedRuleCheck } from '../engine/ruleCheck'

import {
  badgeOf,
  checkOf,
  draftReport,
  notesOf,
  placeSaid,
  sectionsOf,
  settingsSaid,
  summarySaid,
  testedSaid,
  testingOf,
  verdictSaid,
  verdictState,
} from './ruleCheckModel'

function finding(changes: Partial<RuleFinding> = {}): RuleFinding {
  return {
    id: 'bar-end', area: 'repaint', level: 'risk', message: 'Its alerts read last_bar_index.', fix: 'Replace such a window by `true`.',
    places: [{ file: null, line: 3, column: 7 }], count: 1, excerpt: 'per = last_bar_index - bar_index <= 2000', through: ['per'],
    ...changes,
  }
}

const TESTED: RuleTested = {
  settings: { asset: 'capital/US100', side: 'bid', bars: { type: 'time', timeframe: '1' }, inputs: {}, history_days: 1, rule: null },
  at: '2026-10-31T21:00:00Z', spoken: '29 and 30 Oct', checkpoints: { planned: 26, run: 26, skipped: 0, stopped: false },
}

function report(changes: Partial<RuleCheckReport> = {}): RuleCheckReport {
  return {
    digest: 'a'.repeat(64), about: { script: { id: 'cccccccccccc', version: 2 } }, checked_at: '2026-10-31T20:00:00Z',
    kind: 'indicator', pine_version: 6, alerts: [{ title: 'Long', message: 'Long', line: 8 }], alert_calls: [], candidates: [],
    plots: [], whole_because: null, tradingview_rules: [], findings: [], verdict: 'ready', tested: null, tests: [],
    ...changes,
  }
}

describe('the rule check of a script, said', () => {
  it('says its verdict in a sentence, and as an icon', () => {
    expect(verdictSaid(report())).toBe('As its source reads, it can be a signal rule as it is.')
    expect(verdictSaid(report({ tested: TESTED }))).toBe(
      'It can be a signal rule as it is: its alerts were the same live and in a backtest on the days tested.',
    )
    expect(verdictSaid(report({ verdict: 'to_change', findings: [finding({ id: 'mintick', area: 'wata', level: 'change' })] }))).toBe(
      'It can be a signal rule once 1 thing below is changed.',
    )
    expect(verdictSaid(report({ verdict: 'needs_alerts', candidates: [
      { title: 'Up', condition: 'up', call: 'plotshape', line: 4, at: { line: 4, where: 'after' }, risks: [] },
    ] }))).toBe('It needs alerts before it can be a signal rule: it draws 1 event an alert could be raised on.')
    expect(verdictSaid(report({ verdict: 'repaints' }))).toBe('It repaints: its alerts are not the same live and in a backtest.')
    expect([verdictState(report()), verdictState(report({ verdict: 'may_repaint' })), verdictState(report({ verdict: 'repaints' }))])
      .toEqual(['pass', 'open', 'fail'])
    // Tested, and no alert came to compare: still not known.
    const nothing = finding({ id: 'no-alert-compared', level: 'note', message: 'At the 40 bars tested…', fix: null, places: [] })
    expect(verdictSaid(report({ verdict: 'may_repaint', tested: TESTED, findings: [nothing] }))).toBe(
      'It may repaint: at the bars its run test ended at, no alert came, live or in a backtest, to compare.',
    )
  })

  it('shows each finding to act on as a check, where it is and what to change', () => {
    const check = checkOf(finding({ count: 3 }))

    expect(check).toMatchObject({ id: 'bar-end', label: 'The last bars', state: 'fail' })
    expect(check.detail).toBe('Line 3 (and 2 more): Its alerts read last_bar_index. Replace such a window by `true`.')
    expect(placeSaid(finding({ places: [{ file: 'Gate', line: 3, column: 1 }] }))).toBe('In the library Gate, line 3')
    expect(placeSaid(finding({ places: [] }))).toBeNull()
  })

  it('says each part passed, or not known before a run test, when nothing in it is to act on', () => {
    const untested = sectionsOf(report())
    expect(untested.map((section) => section.checks.map((check) => `${check.label}: ${check.state}`))).toEqual([
      ['Nothing that reaches its alerts repaints: open'],
      ['1 alertcondition: pass'],
      ['Not run on recorded bars yet: open'],
    ])

    const same = finding({ id: 'same-live-and-backtest', level: 'note', message: 'At the 26 bars tested on 29 and 30 Oct, its alerts were the same live and in a backtest.' })
    const tested = sectionsOf(report({ tested: TESTED, findings: [same, finding({ level: 'note', tested: true } as Partial<RuleFinding>)] }))
    expect(tested[0].checks).toEqual([{ id: 'same', label: 'The same live and in a backtest', state: 'pass', detail: same.message }])
    expect(tested[2].checks[0]).toMatchObject({ label: 'Runs on WATA', state: 'pass', detail: 'It ran on 29 and 30 Oct.' })
    // What is only worth knowing is listed apart; the test's own sentence is said in its part.
    expect(notesOf(report({ findings: [same, finding({ level: 'note' })] })).map((entry) => entry.id)).toEqual(['bar-end'])
  })

  it('says what a run test was made on', () => {
    expect(testedSaid(null)).toBe('Not tested on recorded bars yet: only its source was read.')
    expect(testedSaid(TESTED)).toBe("Tested on 29 and 30 Oct, on Capital.com's US100, 1 min bars, 1 day of history: 26 bars checked.")
    expect(testedSaid({ ...TESTED, failure: { message: 'x is na', file: null, line: 4 } })).toBe(
      "Tested on Capital.com's US100, 1 min bars, 1 day of history: it failed there.",
    )
    expect(settingsSaid({ ...TESTED.settings, bars: { type: 'range', ticks: 60 }, history_days: null })).toBe("Capital.com's US100, Range 60 bars")
  })

  it('gives a list or a card its badge, and says what it rests on', () => {
    const summary: RuleCheckSummary = {
      verdict: 'repaints', checked_at: null, counts: { repaints: 1 }, worst: { id: 'repaints-at-the-bar', level: 'repaints', message: 'At 3 of the 26 bars tested…' },
      tested_at: '2026-10-31T21:00:00Z', tested_bars: '1', candidates: 0,
    }

    expect(badgeOf(summary)).toEqual({ label: 'Repaints', tone: 'danger' })
    expect(badgeOf(null)).toBeNull()
    expect(summarySaid(summary)).toBe('At 3 of the 26 bars tested… Tested on 1 min bars.')
    expect(summarySaid({ ...summary, tested_bars: 'R60' })).toBe('At 3 of the 26 bars tested… Tested on Range 60 bars.')
    expect(summarySaid({ ...summary, worst: null, tested_at: null })).toBe('Not tested on recorded bars.')
  })

  it('knows whether a run test of it waits or runs', () => {
    const about = { script: { id: 'cccccccccccc', version: 2 } }
    const state: RuleChecksState = {
      available: true, queued: [{ about, settings: TESTED.settings, asked_at: 'x' }], testing: null, this_hour: 0, an_hour: 6, checking: 0, last_refusal: null,
    }

    expect(testingOf(state, about)).toBe('queued')
    expect(testingOf({ ...state, queued: [], testing: { about, settings: TESTED.settings } }, about)).toBe('testing')
    expect(testingOf(state, { script: { id: 'cccccccccccc', version: 3 } })).toBeNull()
    expect(testingOf(undefined, about)).toBeNull()
  })

  it("reads a source's report in the page, as the server would of it saved", () => {
    const read = draftReport(ruleCheckOf('//@version=6\nindicator("x")\nup = close > open\nplotshape(up, "Up")\n'))

    expect(read.verdict).toBe('needs_alerts')
    expect(read.candidates.map((candidate) => candidate.title)).toEqual(['Up'])
    expect(read.tested).toBeNull()
    // What the pages read it through gives the same report, and none, rather than a page that breaks, should the check fail.
    expect(triedRuleCheck('//@version=6\nindicator("x")\nplot(close)\n')?.verdict).toBe('needs_alerts')
  })
})
