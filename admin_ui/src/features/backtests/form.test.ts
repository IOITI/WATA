import type { IPineInput } from 'pinets'
import { describe, expect, it } from 'vitest'

import type { TaRule, TaScriptSummary } from '@/lib/types'
import { FIELDS, OPTIONS, run } from '@/test/backtests'

import {
  axisValues,
  backtestProblems,
  changesOf,
  draftBars,
  draftOfRun,
  fieldError,
  fieldValue,
  newBacktestDraft,
  parseAsset,
  periodDays,
  rangeHint,
  requestOf,
  sweepCells,
  tagsOf,
  walkForwardChoices,
  windowsOf,
  withRule,
} from './form'
import type { BacktestDraft } from './form'

const SCRIPT: TaScriptSummary = {
  id: '8c2df05570d5',
  name: 'EMA cross',
  kind: 'indicator',
  description: '',
  pine_version: 6,
  library_name: null,
  imports: [],
  version: 1,
  versions: [{ number: 1, saved_at: '2026-10-19T10:00:00+00:00', size: 300, sha256: 'x' }],
  created_at: '2026-10-19T10:00:00+00:00',
  updated_at: '2026-10-19T10:00:00+00:00',
}

const META = [
  { id: 'in_0', name: 'Fast length', title: 'Fast length', varId: 'fastLength', type: 'int', defval: 9, minval: 1, maxval: 50 },
  { id: 'in_1', name: 'Show', title: 'Show', varId: 'show', type: 'bool', defval: true },
] as IPineInput[]

const RULE: TaRule = {
  id: 'r00000000001',
  name: 'EMA cross on US100',
  asset: { provider: 'capital', key: 'US100' },
  side: 'ask',
  bars: { type: 'range', ticks: 40 },
  script: { id: SCRIPT.id, version: 1, sha256: 'x' },
  libraries: [],
  inputs: [{ id: 'in_0', var: 'fastLength', title: 'Fast length', value: 12 }],
  alerts: [{ title: 'Cross up', action: 'long' }, { title: 'Cross down', action: 'short' }],
  alert_calls: 'ignore',
  confidence_plot: null,
  indice: 'us100',
  mode: 'shadow',
  created_at: '2026-10-19T10:00:00+00:00',
  updated_at: '2026-10-19T10:00:00+00:00',
  mode_changed_at: '2026-10-19T10:00:00+00:00',
  script_name: 'EMA cross',
  latest: { script: 1, libraries: {} },
}

const PERIOD = { from: '2026-10-14', to: '2026-10-16' }

/** A draft that can be sent: the EMA cross on Capital.com's US100, its two alerts mapped. */
function ready(changes: Partial<BacktestDraft> = {}): BacktestDraft {
  const draft = newBacktestDraft(OPTIONS, SCRIPT, 'capital/US100', PERIOD)
  return { ...draft, rule: { ...draft.rule, alerts: { 'Cross up': 'long', 'Cross down': 'short' } }, ...changes }
}

describe('the asset', () => {
  it('reads a provider and its key, the key may hold a slash', () => {
    expect(parseAsset('capital/US100')).toEqual({ provider: 'capital', key: 'US100' })
    expect(parseAsset('dukascopy/a/b')).toEqual({ provider: 'dukascopy', key: 'a/b' })
    expect(parseAsset('saxo/12')).toEqual({ provider: 'saxo', key: '12' })
    expect(parseAsset('yahoo/NDX')).toBeNull()
    expect(parseAsset('capital/')).toBeNull()
  })

  it('says what a range is in the points of the asset, whose tick is its own', () => {
    expect(rangeHint('60', 0.1)).toBe('R60 is 6 points on this asset, whose tick is 0.1 (a point is R10).')
    expect(rangeHint('600', 0.001)).toBe('R600 is 0.6 points on this asset, whose tick is 0.001 (a point is R1000).')
    expect(rangeHint('100', 0.01)).toBe('R100 is 1 point on this asset, whose tick is 0.01 (a point is R100).')
    expect(rangeHint('100000', 0.1)).toBe('R100000 is 10,000 points on this asset, whose tick is 0.1 (a point is R10).')
    expect(rangeHint('60', null)).toBeNull()
    expect(rangeHint('60', undefined)).toBeNull()
    expect(rangeHint('', 0.1)).toBeNull()
    expect(rangeHint('1.5', 0.1)).toBeNull()
  })
})

describe('a new draft', () => {
  it('starts from the defaults the server gives', () => {
    const draft = newBacktestDraft(OPTIONS, SCRIPT, 'capital/US100', PERIOD)
    expect(draft).toMatchObject({
      from: '2026-10-14', to: '2026-10-16', indice: 'us100', sourceKind: 'pine', asset: 'capital/US100',
      repetitions: '100', seed: '1', sweep: false, heldOut: '0.3', changes: {},
    })
    expect(draft.rule.scriptId).toBe(SCRIPT.id)
    expect(draft.rule.name).toBe('')
  })

  it('takes a signal rule\'s script, asset, bars, inputs and alerts', () => {
    const draft = withRule(newBacktestDraft(OPTIONS, undefined, 'dukascopy/us100', PERIOD), RULE)
    expect(draft.asset).toBe('capital/US100')
    expect(draft.fromRule).toEqual({ id: RULE.id, name: RULE.name })
    expect(draft.rule).toMatchObject({ scriptId: SCRIPT.id, side: 'ask', barsType: 'range', ticks: '40', inputs: { in_0: 12 } })
    expect(draft.rule.alerts).toEqual({ 'Cross up': 'long', 'Cross down': 'short' })
    expect(draftBars(draft)).toEqual({ type: 'range', ticks: 40 })
  })

  it('replays a rule on its own days of history, or on the engine\'s', () => {
    const typed = { ...newBacktestDraft(OPTIONS, undefined, 'capital/US100', PERIOD), changes: { history_days: '3', stoploss_percent: '-8' } }

    expect(withRule(typed, { ...RULE, history_days: 0 }).changes).toEqual({ history_days: '0', stoploss_percent: '-8' })
    expect(withRule(typed, RULE).changes).toEqual({ stoploss_percent: '-8' })
  })

  it('asks for bars of a length typed as Pine names it', () => {
    const draft = newBacktestDraft(OPTIONS, SCRIPT, 'capital/US100', PERIOD)
    const typed = (timeframe: string) => draftBars({ ...draft, rule: { ...draft.rule, barsType: 'time', timeframe } })

    expect(typed('45S')).toEqual({ type: 'time', timeframe: '45S' })
    expect(typed('90s')).toEqual({ type: 'time', timeframe: '90S' })
    expect(typed('2 min')).toEqual({ type: 'time', timeframe: '2' })
    expect(typed('1h')).toEqual({ type: 'time', timeframe: '60' })
  })

  it('takes back what a run was asked, to be asked again', () => {
    const before = run({ request: { ...run().request, changes: { stoploss_percent: -10 }, tags: ['ema', 'october'] } })
    const draft = draftOfRun(before, OPTIONS)
    expect(draft).toMatchObject({
      name: 'Smoke EMA cross (again)', tags: 'ema, october', changes: { stoploss_percent: '-10' }, repetitions: '20', seed: '1',
      sourceKind: 'pine', asset: 'capital/US100',
    })
    expect(draft.rule).toMatchObject({ scriptId: '8c2df05570d5', version: 1, inputs: { in_0: 12 }, timeframe: '1', barsType: 'time' })
    expect(draft.rule.alerts).toEqual({ 'Cross up': 'long', 'Cross down': 'short' })
  })

  it('takes back a replay of the journal with the asset its days were drawn on', () => {
    const journal = run({
      request: {
        ...run().request,
        source: { kind: 'journal', signals: 40, entries: 31 },
        chart: { asset: { provider: 'dukascopy', key: 'us100' }, side: 'bid', bars: { type: 'time', timeframe: '5' } },
        sweep: { axes: [{ kind: 'setting', name: 'stoploss_percent', values: [-10, -15] }], held_out: 0.25, walk_forward: { train_days: 15, test_days: 5 } },
      },
    })
    const draft = draftOfRun(journal, OPTIONS)
    expect(draft).toMatchObject({ sourceKind: 'journal', asset: 'dukascopy/us100', sweep: true, heldOut: '0.25', walkForward: '15/5' })
    expect(draft.rule.timeframe).toBe('5')
    expect(draft.axes).toEqual([{ kind: 'setting', name: 'stoploss_percent', values: '-10, -15' }])
  })
})

describe('a setting as typed', () => {
  const field = (name: string) => FIELDS.find((entry) => entry.name === name)!

  it('reads each kind of setting', () => {
    expect(fieldValue(field('stoploss_percent'), ' -12.5 ')).toBe(-12.5)
    expect(fieldValue(field('max_trades_per_day'), '2.5')).toBeNull()
    expect(fieldValue(field('max_trades_per_day'), '')).toBeNull()
    expect(fieldValue(field('close_position_time'), '21:30')).toBe('21:30')
    expect(fieldValue(field('close_position_time'), '25:00')).toBeNull()
    expect(fieldValue(field('trailing_enabled'), 'false')).toBe(false)
    expect(fieldValue(field('stop_fill'), 'level')).toBe('level')
    expect(fieldValue(field('stop_fill'), 'best')).toBeNull()
  })

  it('says why a value cannot be used', () => {
    expect(fieldError(field('stoploss_percent'), '-150')).toBe('-100 at least.')
    expect(fieldError(field('stoploss_percent'), '5')).toBe('-0.1 at most.')
    expect(fieldError(field('stoploss_percent'), 'ten')).toBe('A number.')
    expect(fieldError(field('max_trades_per_day'), '1.5')).toBe('A whole number.')
    expect(fieldError(field('close_position_time'), '9h')).toBe('A time of day, HH:MM.')
    expect(fieldError(field('stop_fill'), 'x')).toBe('One of worst, level.')
    expect(fieldError(field('stoploss_percent'), '-10')).toBeNull()
  })

  it('sends only what differs from the live value', () => {
    const draft = ready({ changes: { stoploss_percent: '-15', day_profit_target: '2', trailing_enabled: 'false', unknown: '3' } })
    expect(changesOf(draft, FIELDS)).toEqual({ day_profit_target: 2, trailing_enabled: false })
  })
})

describe('a sweep\'s axes', () => {
  it('reads the values of a setting, on and off included', () => {
    expect(axisValues({ kind: 'setting', name: 'stoploss_percent', values: '-5, -10,-15' }, FIELDS, null, 12)).toEqual({ values: [-5, -10, -15] })
    expect(axisValues({ kind: 'setting', name: 'trailing_enabled', values: 'on, off' }, FIELDS, null, 12)).toEqual({ values: [true, false] })
  })

  it('says what is wrong with them', () => {
    const setting = (values: string, most = 12) => axisValues({ kind: 'setting', name: 'stoploss_percent', values }, FIELDS, null, most)
    expect(setting('-5')).toEqual({ error: 'Two values at least, separated by commas.' })
    expect(setting('-5, -6, -7', 2)).toEqual({ error: '2 values at most.' })
    expect(setting('-5, -5')).toEqual({ error: 'Each value once.' })
    expect(setting('-5, 4')).toEqual({ error: '4: -0.1 at most.' })
    expect(axisValues({ kind: 'setting', name: '', values: '1, 2' }, FIELDS, null, 12)).toEqual({ error: 'Choose a setting.' })
  })

  it('reads the values of a script input by its type and bounds', () => {
    expect(axisValues({ kind: 'input', name: 'in_0', values: '8, 12' }, FIELDS, META, 12)).toEqual({ values: [8, 12] })
    expect(axisValues({ kind: 'input', name: 'in_0', values: '8, 80' }, FIELDS, META, 12)).toEqual({ error: '80: not a value of this input.' })
    expect(axisValues({ kind: 'input', name: 'in_1', values: 'true, false' }, FIELDS, META, 12)).toEqual({ values: [true, false] })
    expect(axisValues({ kind: 'input', name: 'in_9', values: '1, 2' }, FIELDS, META, 12)).toEqual({ error: 'Choose an input of the script.' })
  })

  it('counts the combinations', () => {
    expect(sweepCells([{ values: [1, 2, 3] }, { values: [1, 2] }])).toBe(6)
    expect(sweepCells([])).toBe(0)
  })

  it('asks for a walk-forward by its windows, the one of a run asked again among them', () => {
    expect(windowsOf('10/5')).toEqual({ train_days: 10, test_days: 5 })
    expect(windowsOf('')).toBeNull()
    expect(windowsOf('10/0')).toBeNull()
    expect(walkForwardChoices('').map((choice) => choice.value)).toEqual(['', '5/1', '10/5', '20/5', '40/10'])
    expect(walkForwardChoices('15/5').at(-1)).toEqual({ value: '15/5', label: 'choose on 15 days, keep 5' })
  })
})

describe('what stops a draft', () => {
  it('lets a whole draft through', () => {
    expect(backtestProblems(ready(), OPTIONS, META)).toEqual([])
  })

  it('wants a period, in order, and not too long', () => {
    expect(backtestProblems(ready({ from: '' }), OPTIONS, META)).toEqual(['Choose the period.'])
    expect(backtestProblems(ready({ from: '2026-10-16', to: '2026-10-14' }), OPTIONS, META)).toEqual(['The period ends before it starts.'])
    expect(backtestProblems(ready({ from: '2026-01-01', to: '2026-10-14' }), OPTIONS, META)).toEqual(['A period of 92 days at most.'])
    expect(periodDays('2026-10-14', '2026-10-16')).toBe(3)
  })

  it('wants an asset, range bars of a sensible size, a script and an alert made a signal', () => {
    const base = ready()
    expect(backtestProblems({ ...base, asset: '' }, OPTIONS, META)).toEqual(['Choose the asset.'])
    expect(backtestProblems({ ...base, rule: { ...base.rule, barsType: 'range', ticks: '0' } }, OPTIONS, META)).toEqual([
      'Range bars are 1 to 100000 ticks.',
    ])
    expect(backtestProblems({ ...base, rule: { ...base.rule, scriptId: '' } }, OPTIONS, META)).toEqual(['Choose the script.'])
    expect(backtestProblems({ ...base, rule: { ...base.rule, alerts: { 'Cross up': 'ignore' } } }, OPTIONS, META)).toEqual([
      'Make at least one alert of the script a signal.',
    ])
    // The messages of alert() calls may be the signals instead.
    expect(backtestProblems({ ...base, rule: { ...base.rule, alerts: {}, alertCalls: 'message' } }, OPTIONS, META)).toEqual([])
    // A replay of the journal has no script.
    expect(backtestProblems({ ...base, sourceKind: 'journal', rule: { ...base.rule, scriptId: '', alerts: {} } }, OPTIONS, META)).toEqual([])
  })

  it('wants bars of a length WATA makes, from ten seconds to a day', () => {
    const base = ready()
    const length = (timeframe: string) => backtestProblems({ ...base, rule: { ...base.rule, barsType: 'time', timeframe } }, OPTIONS, META)

    expect(length('45S')).toEqual([])
    expect(length('90s')).toEqual([])
    for (const wrong of ['5s', '2d', 'soon', '']) {
      expect(length(wrong)).toEqual(['Bars of 10 seconds to a day: write their length as 45s, 90s or 2m.'])
    }
  })

  it('names the setting typed wrong', () => {
    expect(backtestProblems(ready({ changes: { stoploss_percent: '3' } }), OPTIONS, META)).toEqual(['Stop-loss: -0.1 at most.'])
  })

  it('checks the random entries, or the sweep', () => {
    expect(backtestProblems(ready({ repetitions: '501' }), OPTIONS, META)).toEqual(['0 to 500 repetitions with entries at random.'])
    expect(backtestProblems(ready({ seed: '-1' }), OPTIONS, META)).toEqual(['The seed is a whole number.'])
    expect(backtestProblems(ready({ sweep: true }), OPTIONS, META)).toEqual(['A sweep tries the values of one or two settings.'])
    const big = ready({
      sweep: true,
      heldOut: '0.9',
      axes: [
        { kind: 'setting', name: 'stoploss_percent', values: '-1, -2, -3, -4, -5, -6, -7, -8, -9' },
        { kind: 'setting', name: 'day_profit_target', values: '1, 2, 3, 4, 5, 6, 7, 8' },
      ],
    })
    expect(backtestProblems(big, OPTIONS, META)).toEqual(['72 combinations: 64 at most.', 'The days held out are 10 to 50 % of them.'])
    const journal = ready({ sweep: true, sourceKind: 'journal', axes: [{ kind: 'input', name: 'in_0', values: '8, 12' }] })
    expect(backtestProblems(journal, OPTIONS, META)).toEqual(['A replay of the journal has no script inputs to try.'])
  })
})

describe('the request', () => {
  it('sends a script with the inputs that differ from its defaults', () => {
    const draft = ready({ name: '  EMA 12 ', tags: 'ema, ema, october', changes: { stoploss_percent: '-10' } })
    draft.rule = { ...draft.rule, inputs: { in_0: 12, in_1: true } }
    expect(requestOf(draft, OPTIONS, META)).toEqual({
      name: 'EMA 12',
      tags: ['ema', 'october'],
      from: '2026-10-14',
      to: '2026-10-16',
      indice: 'us100',
      source: {
        kind: 'pine',
        script: { id: SCRIPT.id, version: 1 },
        asset: { provider: 'capital', key: 'US100' },
        side: 'bid',
        bars: { type: 'time', timeframe: '1' },
        inputs: [{ id: 'in_0', var: 'fastLength', title: 'Fast length', value: 12 }],
        alerts: [{ title: 'Cross up', action: 'long' }, { title: 'Cross down', action: 'short' }],
        alert_calls: 'ignore',
        confidence_plot: null,
        from_rule: null,
      },
      changes: { stoploss_percent: -10 },
      baseline: { repetitions: 100, seed: 1 },
    })
  })

  it('sends a replay of the journal with the chart its days are drawn on, and a sweep without random entries', () => {
    const draft = ready({
      sourceKind: 'journal',
      asset: 'dukascopy/us100',
      sweep: true,
      axes: [{ kind: 'setting', name: 'stoploss_percent', values: '-10, -15' }],
      walkForward: '10/5',
    })
    const body = requestOf(draft, OPTIONS, META)
    expect(body.source).toEqual({ kind: 'journal' })
    expect(body.chart).toEqual({ asset: { provider: 'dukascopy', key: 'us100' }, side: 'bid', bars: { type: 'time', timeframe: '1' } })
    expect(body.sweep).toEqual({
      axes: [{ kind: 'setting', name: 'stoploss_percent', values: [-10, -15] }],
      held_out: 0.3,
      walk_forward: { train_days: 10, test_days: 5 },
    })
    expect(body.baseline).toBeUndefined()
    expect(body.name).toBeUndefined()
  })

  it('keeps each tag once', () => {
    expect(tagsOf(' a, b ,, a')).toEqual(['a', 'b'])
  })
})
