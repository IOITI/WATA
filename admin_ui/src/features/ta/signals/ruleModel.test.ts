import type { IPineInput } from 'pinets'
import { describe, expect, it } from 'vitest'

import type { TaComparisonRow, TaRule } from '@/lib/types'
import { run } from '@/test/backtests'

import {
  barsLabel,
  comparisonMarkers,
  draftOfRule,
  draftProblems,
  historyLabel,
  inputValueOf,
  mappedAlerts,
  newDraft,
  ruleChanges,
  ruleDraftOfRun,
  ruleFields,
  updatesOf,
} from './ruleModel'

function rule(changes: Partial<TaRule> = {}): TaRule {
  return {
    id: 'aaaaaaaaaaaa',
    name: 'Cross on US100',
    asset: { provider: 'capital', key: 'US100' },
    side: 'bid',
    bars: { type: 'time', timeframe: '1' },
    script: { id: 'cccccccccccc', version: 2, sha256: 'x' },
    libraries: [{ id: 'dddddddddddd', name: 'Bands', version: 1, sha256: 'y' }],
    inputs: [{ id: 'in_0', var: 'fast', title: 'Fast', value: 12 }],
    alerts: [
      { title: 'Enter Long', action: 'long' },
      { title: 'Exit', action: 'close-position' },
    ],
    alert_calls: 'ignore',
    confidence_plot: null,
    indice: 'us100',
    mode: 'shadow',
    created_at: '2026-10-19T10:00:00+00:00',
    updated_at: '2026-10-19T10:00:00+00:00',
    mode_changed_at: '2026-10-19T10:00:00+00:00',
    script_name: 'Cross',
    latest: { script: 2, libraries: { Bands: 1 } },
    ...changes,
  }
}

const META = [
  { id: 'in_0', name: 'Fast', title: 'Fast', varId: 'fast', type: 'int', defval: 9, minval: 1 },
  { id: 'in_1', name: 'Source', title: 'Source', varId: 'src', type: 'source', defval: 'close' },
  { id: 'in_2', name: 'Show', title: 'Show', varId: 'show', type: 'bool', defval: true },
] as IPineInput[]

describe('a signal rule', () => {
  it('says its bars, the versions it could run, and what makes signals', () => {
    expect(barsLabel({ type: 'time', timeframe: '15' })).toBe('15 min')
    expect(barsLabel({ type: 'time', timeframe: '60' })).toBe('1 hour')
    expect(barsLabel({ type: 'range', ticks: 60 })).toBe('Range 60')
    expect(updatesOf(rule())).toEqual({ script: null, libraries: [] })
    expect(updatesOf(rule({ latest: { script: 4, libraries: { Bands: 3 } } }))).toEqual({
      script: 4,
      libraries: [{ name: 'Bands', from: 1, to: 3 }],
    })
    expect(mappedAlerts(rule({ alert_calls: 'message' }))).toEqual([
      '“Enter Long” → long',
      '“Exit” → close positions',
      'every alert() call → what its message says',
    ])
  })

  it('is saved with only the inputs it changes, and only the alerts that make signals', () => {
    const draft = draftOfRule(rule())
    draft.inputs = { in_0: 9, in_1: 'hl2', in_2: true }
    draft.alerts = { ...draft.alerts, Quiet: 'ignore' }

    const fields = ruleFields(draft, META)

    // The defaults stay the script's: 9 and true are not saved.
    expect(fields.inputs).toEqual([{ id: 'in_1', var: 'src', title: 'Source', value: 'hl2' }])
    expect(fields.alerts).toEqual(rule().alerts)
    expect(fields).toMatchObject({ asset: { provider: 'capital', key: 'US100' }, bars: { type: 'time', timeframe: '1' }, indice: 'us100' })
    // Without the script's inputs, what the rule set is kept as it is.
    expect(ruleFields(draftOfRule(rule()), null).inputs).toEqual(rule().inputs)
  })

  it('reads a typed value as its input\'s type takes it', () => {
    expect(inputValueOf(META[0], '14')).toBe(14)
    expect(inputValueOf(META[0], '1.5')).toBeNull()
    expect(inputValueOf(META[0], '0')).toBeNull()
    expect(inputValueOf(META[0], '')).toBeNull()
    expect(inputValueOf({ type: 'float' }, '0.25')).toBe(0.25)
    expect(inputValueOf(META[2], false)).toBe(false)
    expect(inputValueOf(META[1], 'open')).toBe('open')
  })

  it('says what stops a draft from being saved', () => {
    const draft = newDraft(undefined, undefined, [])

    expect(draftProblems(draft, [])).toEqual([
      'The rule needs a name.',
      'Choose the script it runs.',
      'Choose the asset it runs on.',
      'Choose the indice its signals are for.',
    ])
    const taken = { ...draftOfRule(rule()), name: 'cross ON us100', barsType: 'range' as const, ticks: '0' }
    expect(draftProblems(taken, ['Cross on US100'])).toEqual([
      'A rule named “cross ON us100” already exists.',
      'Range bars are 1 to 100000 ticks.',
    ])
  })

  it('starts a new rule on the feed\'s instrument, with its label as the indice when the trader takes it', () => {
    const script = { id: 'cccccccccccc', name: 'Cross', version: 3 } as Parameters<typeof newDraft>[0]

    expect(newDraft(script, { epic: 'US100', label: 'us100' }, ['de40', 'us100'])).toMatchObject({
      name: 'Cross on US100',
      scriptId: 'cccccccccccc',
      version: 3,
      epic: 'US100',
      indice: 'us100',
    })
  })

  it('lists what a save changes, for the review', () => {
    const before = rule()
    const draft = draftOfRule(before)
    draft.side = 'ask'
    draft.version = 3
    draft.alerts = { 'Enter Long': 'long' }

    const changes = ruleChanges(before, ruleFields(draft, null), (id) => (id === 'cccccccccccc' ? 'Cross' : id))

    expect(changes).toEqual([
      { label: 'Script', before: 'Cross v2', after: 'Cross v3' },
      { label: 'Asset', before: 'US100 · bid', after: 'US100 · ask' },
      { label: 'Signals', before: '“Enter Long” → long; “Exit” → close positions', after: '“Enter Long” → long' },
    ])
    expect(ruleChanges(null, ruleFields(draft, null), () => 'Cross').map((change) => change.label)).toEqual([
      'Name', 'Script', 'Asset', 'Bars', 'Indice', 'Inputs', 'Signals', 'Confidence', 'History',
    ])
  })

  it('keeps its own days of history, or the engine\'s when it is left empty', () => {
    const own = draftOfRule(rule({ history_days: 0 }))
    const engines = { ...own, historyDays: ' ' }

    expect(own.historyDays).toBe('0')
    expect(draftOfRule(rule()).historyDays).toBe('')
    // No day before the restart is a history of its own.
    expect(ruleFields(own, null).history_days).toBe(0)
    expect(ruleFields(engines, null).history_days).toBeNull()
    expect(ruleChanges(rule({ history_days: 0 }), ruleFields(engines, null), () => 'Cross')).toEqual([
      { label: 'History', before: 'None: from the restart', after: 'The engine’s' },
    ])
    expect([historyLabel(1), historyLabel(3), historyLabel(undefined)]).toEqual(['1 day', '3 days', 'The engine’s'])
    for (const typed of ['31', '1.5', '-1', 'two']) {
      expect(draftProblems({ ...own, historyDays: typed }, [])).toEqual(['Its history is 0 to 30 days, or left empty for the engine’s.'])
    }
  })
})

describe('a rule made from a backtest', () => {
  const instruments = [{ epic: 'US100', label: 'us100' }, { epic: 'US500', label: 'us500' }]
  const TICKS: Record<string, number> = { 'capital/US100': 0.1, 'dukascopy/us100': 0.001 }
  const tickOf = (ref: string) => TICKS[ref] ?? null

  it('runs the script, version, inputs, alerts and bars the run traded, in shadow from the dialog', () => {
    const draft = ruleDraftOfRun(run(), instruments, tickOf)
    expect(draft).toMatchObject({
      name: 'EMA cross on US100',
      scriptId: '8c2df05570d5',
      version: 1,
      epic: 'US100',
      side: 'bid',
      barsType: 'time',
      timeframe: '1',
      indice: 'us100',
      inputs: { in_0: 12 },
      inputNames: { in_0: { var: 'fastLength', title: 'Fast length' } },
      alerts: { 'Cross up': 'long', 'Cross down': 'short' },
      alertCalls: 'ignore',
      historyDays: '',
    })
  })

  it('reads a run on Dukascopy on the Capital.com instrument of its indice, the range as the same move in points', () => {
    const base = run()
    const dukascopy = run({
      request: {
        ...base.request,
        chart: { asset: { provider: 'dukascopy', key: 'us100' }, side: 'bid', bars: { type: 'range', ticks: 6000 } },
        changes: { history_days: 0 },
      },
    })
    expect(ruleDraftOfRun(dukascopy, instruments, tickOf)).toMatchObject({ epic: 'US100', barsType: 'range', ticks: '60', historyDays: '0' })
    // A tick not known: the range as it was.
    expect(ruleDraftOfRun(dukascopy, instruments, () => null)).toMatchObject({ ticks: '6000' })
  })

  it('has nothing to make of a replay of the journal', () => {
    const base = run()
    expect(ruleDraftOfRun(run({ request: { ...base.request, source: { kind: 'journal', signals: 40, entries: 20 } } }), instruments, tickOf)).toBeNull()
  })
})

describe('the comparison on the chart', () => {
  it('marks WATA above the bars and TradingView below, a signal left alone in the warning colour', () => {
    const rows = [
      { at: '2026-10-19T09:31:00Z', action: 'long', wata: { bar_time: '2026-10-19T09:31:00Z' }, tradingview: { signal_timestamp: '2026-10-19T09:32:00Z' }, delta_seconds: 60 },
      { at: '2026-10-19T10:00:00Z', action: 'short', wata: { bar_time: '2026-10-19T10:00:00Z' }, tradingview: null, delta_seconds: null },
      { at: '2026-10-19T23:00:00Z', action: 'close-position', wata: null, tradingview: { signal_timestamp: '2026-10-19T23:00:00Z' }, delta_seconds: null },
    ] as unknown as TaComparisonRow[]
    const shown = Date.parse('2026-10-19T20:00:00Z') / 1000

    const markers = comparisonMarkers(rows, (instant) => (instant < shown ? Math.floor(instant / 60) * 60 : null))

    expect(markers.map((marker) => [marker.text, marker.position, marker.shape, marker.tone])).toEqual([
      ['WATA long', 'aboveBar', 'arrowUp', 'entry'],
      ['TV long', 'belowBar', 'arrowUp', 'neutral'],
      ['WATA short', 'aboveBar', 'arrowDown', 'warning'],
    ])
  })
})
