import { describe, expect, it } from 'vitest'

import { FIELDS, listedRun, run, SUMMARY } from '@/test/backtests'

import {
  collapseAttempts,
  comparable,
  compareCurves,
  comparisonWarnings,
  curveDays,
  daysSentence,
  differingInputs,
  differingSettings,
  distribution,
  failureLine,
  filterRuns,
  heldFor,
  localDay,
  progressSentence,
  randomSentence,
  randomVerdict,
  researchSentence,
  roomSentences,
  runFilterOf,
  ruleLabel,
  sampleWarning,
  settingChanges,
  showValue,
  sourceLabel,
  tally,
} from './model'

describe('what a run is', () => {
  it('names its signals, asset and bars', () => {
    expect(sourceLabel(listedRun())).toBe('EMA cross v1 · Capital.com US100 · 1 min')
    expect(sourceLabel(listedRun({ source: { kind: 'journal', script: null, from_rule: null, signals: 40 } }))).toBe('Signals WATA received')
  })

  it('says where it is while it runs', () => {
    expect(progressSentence(null)).toBe('Starting.')
    expect(progressSentence({ phase: 'signals', done: 0, total: 3, day: null })).toBe('Running the script over the days of the period.')
    expect(progressSentence({ phase: 'days', done: 1, total: 3, day: '2026-10-15' })).toBe('Trading the recorded turbos: day 2 of 3 (2026-10-15).')
    // Past the last day, it does not count a day more than there are.
    expect(progressSentence({ phase: 'days', done: 3, total: 3, day: null })).toBe('Trading the recorded turbos: day 3 of 3.')
    expect(progressSentence({ phase: 'report', done: 3, total: 3, day: null })).toBe('Writing what it found.')
  })

  it('names a refusal in words, and one it does not know by its key', () => {
    expect(ruleLabel('day_profit')).toBe('The day made enough')
    expect(ruleLabel('no_turbo_in_range')).toBe('No turbo in the price range')
    expect(ruleLabel('something_new')).toBe('something new')
    expect(ruleLabel(null)).toBe('-')
  })
})

describe('settings', () => {
  it('shows a value with its unit, but not a clock', () => {
    const stop = FIELDS.find((field) => field.name === 'stoploss_percent')
    const close = FIELDS.find((field) => field.name === 'close_position_time')
    expect(showValue(-12, stop)).toBe('-12 %')
    expect(showValue('21:30', close)).toBe('21:30')
    expect(showValue(true)).toBe('On')
    expect(showValue(false)).toBe('Off')
    expect(showValue(null)).toBe('-')
  })

  it('lists what a run changed, beside the live value it had then', () => {
    expect(settingChanges({ stoploss_percent: -10, unknown_one: 3 }, { stoploss_percent: -15 }, FIELDS)).toEqual([
      { name: 'stoploss_percent', label: 'Stop-loss', before: '-15 %', after: '-10 %' },
      { name: 'unknown_one', label: 'unknown one', before: '-', after: '3' },
    ])
  })

  it('takes the field\'s value when the run kept no live value', () => {
    expect(settingChanges({ day_profit_target: 2 }, {}, FIELDS)[0].before).toBe('1.7 %')
  })
})

describe('the curves', () => {
  it('gives the performance charts their days in percent or in money', () => {
    const percent = curveDays(SUMMARY.curve ?? [], 'percent', 10250)
    expect(percent.map((day) => [day.day_date, day.profit_loss, day.cumulative_profit_loss])).toEqual([
      ['2026-10-14', 1.72, 1.72],
      ['2026-10-15', 1.7, 3.45],
      ['2026-10-16', 1.7, 5.21],
    ])
    const money = curveDays(SUMMARY.curve ?? [], 'money', 10250)
    expect(money.map((day) => [day.profit_loss, Number(day.cumulative_profit_loss.toFixed(2))])).toEqual([
      [175.86, 175.86],
      [177.12, 352.98],
      [179.95, 532.93],
    ])
  })

  it('counts a tally, the most frequent first, with each one\'s share', () => {
    expect(tally({ market_hours: 51, day_profit: 59 })).toEqual([
      { name: 'day_profit', count: 59, share: 59 / 110 },
      { name: 'market_hours', count: 51, share: 51 / 110 },
    ])
    expect(tally(undefined)).toEqual([])
  })
})

describe('how far a run can be trusted', () => {
  it('warns of a small sample, and says nothing of a fair one', () => {
    expect(sampleWarning('too_few', 3, 3)).toBe('3 trades over 3 days: too few to conclude. Under 30 trades or 20 days, these figures describe those days and nothing else.')
    expect(sampleWarning('few', 1, 1)).toMatch(/^1 trade over 1 day: enough to see a direction/)
    expect(sampleWarning('fair', 300, 80)).toBeNull()
  })

  it('places a run among the random entries', () => {
    expect(randomVerdict(97)).toBe('above')
    expect(randomVerdict(95)).toBe('above')
    expect(randomVerdict(50)).toBe('inside')
    expect(randomVerdict(5)).toBe('below')
  })

  it('bins the random totals and marks the run\'s own, even beyond them all', () => {
    const bins = distribution([1, 2, 3, 4], 10, 3)
    // From 1 to 10 in three: 1 to 4, 4 to 7, 7 to 10.
    expect(bins.map((bin) => bin.count)).toEqual([3, 1, 0])
    expect(bins.map((bin) => bin.marked)).toEqual([false, false, true])
    expect(distribution([], 1)).toEqual([])
    expect(distribution([2, 2], 2)).toEqual([{ from: 1.5, to: 2.5, count: 2, marked: true }])
  })

  it('says what the random entries were', () => {
    expect(randomSentence(SUMMARY.against_random!)).toBe(
      'The same days traded 20 times with its 113 signals of entry put at random moments of the hours they came at, long or short by a coin, ' +
        'on the same recorded turbos and under the same rules: half of them ended above +5.19%, 90 in 100 between −26.15% and +5.25%.',
    )
  })

  it('says where a stop and a target would have cut', () => {
    expect(roomSentences(SUMMARY.figures!)).toEqual([
      'Half of the winning trades never fell below −3.66%, nine in ten never below −5.79%: a stop tighter than that would have sold winners.',
    ])
    expect(roomSentences({ winners_lowest: null, losers_highest: { median: 2.1, tenth: 4 } })).toEqual([
      'Half of the losing trades had been above +2.10% before they lost, one in ten above +4.00%.',
    ])
  })

  it('says which days were traded and why the others were not', () => {
    expect(daysSentence(SUMMARY.days)).toBe('3 of the 3 days asked had turbos recorded and were traded.')
    expect(daysSentence({ asked: 5, tested: 2, by_status: { tested: 2, no_recorded_turbos: 2, closed: 1 } })).toBe(
      '2 of the 5 days asked had turbos recorded and were traded; 2 had no turbo recorded; 1 the market was closed.',
    )
  })
})

describe('times', () => {
  it('says how long a trade was held', () => {
    expect(heldFor(45)).toBe('45 s')
    expect(heldFor(966)).toBe('16 min')
    expect(heldFor(3 * 3600)).toBe('3.0 h')
  })

  it('gives the local day of a moment', () => {
    // Midnight past in Paris, the day before in UTC.
    expect(localDay('2026-10-13T22:30:00Z', 'Europe/Paris')).toBe('2026-10-14')
  })
})

describe('runs side by side', () => {
  const first = run({ id: '20261019-100000-aaaaaa', name: 'Stop 15' })
  const second = run({
    id: '20261019-110000-bbbbbb',
    name: 'Stop 10',
    settings: { settings: { ...first.settings.settings, stoploss_percent: -10, trailing_enabled: false }, changed: {}, live_before: {} },
  })

  it('keeps the finished runs among those ticked, four at most, oldest first', () => {
    const runs = [
      listedRun({ id: '20261019-120000-cccccc' }),
      listedRun({ id: '20261019-100000-aaaaaa' }),
      listedRun({ id: '20261019-110000-bbbbbb', status: { state: 'running' } }),
      listedRun({ id: '20261019-130000-dddddd', kind: 'sweep' }),
    ]
    const selected = new Set(runs.map((entry) => entry.id))
    expect(comparable(runs, selected).map((entry) => entry.id)).toEqual(['20261019-100000-aaaaaa', '20261019-120000-cccccc'])
  })

  it('lists the settings they did not share, with each one\'s value', () => {
    expect(differingSettings([first, second], FIELDS)).toEqual([
      { name: 'stoploss_percent', label: 'Stop-loss', values: ['-15 %', '-10 %'] },
      { name: 'trailing_enabled', label: 'Trailing stop', values: ['On', 'Off'] },
    ])
    expect(differingSettings([first, first], FIELDS)).toEqual([])
  })

  it('lists the inputs of their scripts they did not share; a replay of the journal has none', () => {
    const journal = run({ request: { ...first.request, source: { kind: 'journal', signals: 40, entries: 30 } } })
    expect(differingInputs([first, journal])).toEqual([{ name: 'in_0', label: 'Input: Fast length', values: ['12', '-'] }])
    expect(differingInputs([first, first])).toEqual([])
  })

  it('draws each run\'s days one applied on the other', () => {
    expect(compareCurves([first])).toEqual([
      {
        id: first.id,
        name: 'Stop 15',
        points: [
          { day: '2026-10-14', value: 1.72 },
          { day: '2026-10-15', value: 3.45 },
          { day: '2026-10-16', value: 5.21 },
        ],
      },
    ])
  })

  it('warns when they did not trade the same days or the same turbos', () => {
    expect(comparisonWarnings([first, second])).toEqual([])
    expect(comparisonWarnings([first, { ...second, to: '2026-10-20', indice: 'de40', stale: true }])).toEqual([
      'These runs did not cover the same days: part of what differs between them is the days.',
      'These runs traded the turbos of different indices.',
      'One of them was made by other code than this admin’s: it could come out otherwise today.',
    ])
  })
})

describe('the research files', () => {
  const status = {
    enabled: true, keep_days: 30, state: 'up_to_date' as const, exporting: null, days: 3, first: '2026-10-15', last: '2026-10-17',
    bytes: 42_000_000, error: null, location: 'research/days', limits: { keep_days: 365 },
  }

  it('says what is written and what is kept', () => {
    expect(researchSentence(status)).toBe(
      'Research files on: 3 days written (15 Oct 2026 to 17 Oct 2026), 42.0 MB; the last 30 are kept. Every finished day is written.',
    )
    expect(researchSentence({ ...status, state: 'waiting_for_close' })).toMatch(/wait for the market to close\.$/)
    expect(researchSentence({ ...status, state: 'exporting', exporting: '2026-10-18' })).toMatch(/Writing 18 Oct 2026\.$/)
    expect(researchSentence({ ...status, days: 0, first: null, last: null })).toMatch(/^Research files on: No day written yet;/)
  })

  it('says what switching them on does', () => {
    expect(researchSentence({ ...status, enabled: false, state: 'off' })).toMatch(/^Each finished day can be written as files/)
  })
})

describe('the list of runs', () => {
  const failed = (id: string, name: string) =>
    listedRun({ id, name, status: { state: 'failed', error: `Traceback\n  raise RunnerDown(...)\nThe backtest failed: stopped by the kernel (${id})` } })

  it('collapses the failed attempts of a request under the run that came after them', () => {
    // The server's list of 22 Oct, newest first: E7 E3 done after nine failed attempts, E7 E2 failed three times.
    const runs = [
      listedRun({ id: 'e3-done', name: 'E7 E3' }),
      ...Array.from({ length: 9 }, (_, index) => failed(`e3-${index}`, 'E7 E3')),
      failed('e2-c', 'E7 E2'),
      failed('e2-b', 'E7 E2'),
      failed('e2-a', 'E7 E2'),
      listedRun({ id: 'other', name: 'E6 C' }),
    ]
    const { shown, earlier } = collapseAttempts(runs)

    expect(shown.map((run) => run.id)).toEqual(['e3-done', 'e2-c', 'other'])
    expect(earlier.get('e3-done')).toHaveLength(9)
    expect(earlier.get('e2-c')?.map((run) => run.id)).toEqual(['e2-b', 'e2-a'])
    // Another period is another request.
    expect(collapseAttempts([listedRun({ id: 'a' }), failed('b', 'Smoke EMA cross')]).shown).toHaveLength(1)
    expect(collapseAttempts([listedRun({ id: 'a' }), { ...failed('b', 'Smoke EMA cross'), to: '2026-10-17' }]).shown).toHaveLength(2)
    expect(failureLine(failed('x', 'E7 E2'))).toBe('The backtest failed: stopped by the kernel (x)')
  })

  it('narrows the list as its address says, and ignores what it does not know', () => {
    const runs = [
      listedRun({ id: 'mine' }),
      listedRun({ id: 'claudes', made_by: 'claude', experiment: 'f59bc8', turbos: 'modelled', stale: true }),
      failed('broken', 'E7 E2'),
    ]
    const ids = (query: string) => filterRuns(runs, runFilterOf(new URLSearchParams(query))).map((run) => run.id)

    expect(ids('')).toEqual(['mine', 'claudes', 'broken'])
    expect(ids('by=claude')).toEqual(['claudes'])
    expect(ids('by=me&state=done')).toEqual(['mine'])
    expect(ids('state=failed')).toEqual(['broken'])
    expect(ids('experiment=f59bc8&turbos=modelled&code=older')).toEqual(['claudes'])
    expect(ids('state=nonsense&by=someone')).toEqual(['mine', 'claudes', 'broken'])
  })
})
