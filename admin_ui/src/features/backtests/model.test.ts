import { describe, expect, it } from 'vitest'

import type {
  BacktestAgainstRandom,
  BacktestCalibrationResult,
  BacktestDay,
  BacktestFigures,
  BacktestRun,
  BacktestRunListed,
  BacktestSweep,
  BacktestTrade,
  BacktestTradePath,
  PriceKeepStatus,
} from '@/lib/types'

import {
  axisValues,
  calibrationSentences,
  cellName,
  commonLeverages,
  compounded,
  dailyFromBacktest,
  dayToDate,
  differing,
  distribution,
  executionSentence,
  fieldError,
  figuresAt,
  formatBytes,
  heat,
  heldLonger,
  isActive,
  isReplay,
  isSweep,
  journalSentence,
  keepSentence,
  leveragesOf,
  localDay,
  METRICS,
  middleLeverage,
  mixedPrices,
  numberOf,
  parameterChanges,
  pathView,
  pricesWording,
  randomSentence,
  randomVerdict,
  roomSentences,
  ruleChanges,
  runName,
  sampleWarning,
  staleSentence,
  sweepGrid,
  sweepVerdict,
  tally,
  typed,
  whatDiffers,
  wording,
} from './model'

const run = (changes: Partial<BacktestRunListed> = {}): BacktestRunListed => ({
  id: '20261012-213045-a1b2c3',
  from: '2026-09-14',
  to: '2026-10-09',
  strategy: 'kernel_vwap',
  parameters: {},
  rules: {},
  turbo: { entry_price: 4.4, spread: 0.03, leverages: [80, 110, 140] },
  note: '',
  status: { state: 'done', created_at: '2026-10-12T21:30:45Z' },
  headline: null,
  ...changes,
})

const trade = (exit: string, performance: number): BacktestTrade => ({
  entry_time: exit,
  exit_time: exit,
  direction: 'long',
  entry_reason: 'cross',
  confidence: null,
  entry_index: 30000,
  exit_index: 30010,
  index_points: 10,
  performance_percent: performance,
  max_performance_percent: performance,
  exit_reason: 'Daily profit target',
})

describe('what a run is called', () => {
  it('is its note when it has one', () => {
    expect(runName(run({ note: 'higher target' }))).toBe('higher target')
  })

  it('is what it changed otherwise, parameters then rules', () => {
    const changed = run({ parameters: { entry_mode: 'cross_catchup' }, rules: { day_profit_target: 3, trailing_enabled: false } })

    expect(whatDiffers(changed)).toBe('entry mode cross_catchup · day profit target 3 · trailing enabled off')
    expect(runName(changed)).toBe(whatDiffers(changed))
    // Dukascopy's history goes without saying; Capital.com's recorded prices do not.
    expect(whatDiffers({ ...changed, prices: 'capital' })).toBe(
      'on Capital.com prices · entry mode cross_catchup · day profit target 3 · trailing enabled off',
    )
  })

  it('says so when it changed nothing', () => {
    expect(runName(run())).toBe('Defaults and live rules')
  })
})

describe('a replay of the signals WATA received', () => {
  it('is told from a strategy, the first runs having no kind', () => {
    expect(isReplay(run({ kind: 'signals' }))).toBe(true)
    expect([isReplay(run({ kind: 'strategy' })), isReplay(run())]).toEqual([false, false])
  })

  it('is named for what it is, with the rules it changed', () => {
    expect(runName(run({ kind: 'signals' }))).toBe('Real signals')
    expect(runName(run({ kind: 'signals', rules: { day_profit_target: 3 } }))).toBe('Real signals · day profit target 3')
    expect(runName(run({ kind: 'signals', note: 'September' }))).toBe('September')
  })

  it('says what the trader made of the signals, the usual outcomes first', () => {
    expect(journalSentence({ refused: 11, ignored: 1, executed: 2, unknown: 3 })).toBe(
      "Of the 17 signals replayed, WATA's journal has 2 executed, 11 refused, 1 ignored, 3 unknown.",
    )
    expect(journalSentence({ executed: 1 })).toBe("Of the 1 signal replayed, WATA's journal has 1 executed.")
    expect(journalSentence({})).toBe('No signal was replayed.')
  })

  it('says how much longer than WATA the model held a trade', () => {
    expect([0, 45, -75, 120, -1500, 5400].map(heldLonger)).toEqual([
      'as long', '45 s longer', '75 s shorter', '2 min longer', '25 min shorter', '1.5 h longer',
    ])
  })
})

describe('how far a run can be trusted', () => {
  const found: BacktestAgainstRandom = {
    repetitions: 200, seed: 1, entries: 13, beaten: 64, median: 3.4, low: -13.6, high: 3.43, level: 90,
    worst: -27.75, best: 3.43, trades: 3, win_rate: 66.7, totals: [-10, -10, 0, 0, 0, 5, 10],
  }

  it('says when it rests on too few trades or days, and nothing when there are enough', () => {
    expect(sampleWarning('too_few', 2, 3)).toBe(
      '2 trades over 3 days: too few to conclude. Under 30 trades or 20 days, these figures describe those days and nothing else.',
    )
    expect(sampleWarning('too_few', 1, 1)).toMatch(/^1 trade over 1 day: too few/)
    expect(sampleWarning('few', 45, 30)).toMatch(/^45 trades over 30 days: enough to see a direction, not to rely on a figure/)
    expect(sampleWarning('fair', 200, 100)).toBeNull()
    // A run made before this was measured says nothing of it.
    expect(sampleWarning(undefined, 2, 3)).toBeNull()
  })

  it('stands above the random entries, below them, or among them', () => {
    expect([100, 95, 94.9, 50, 5.1, 5, 0].map(randomVerdict)).toEqual([
      'above', 'above', 'inside', 'inside', 'inside', 'below', 'below',
    ])
  })

  it('puts the random totals in bars of equal width, and its own total among them', () => {
    const bins = distribution(found.totals, 0, 4)

    expect(bins.map((bin) => [bin.from, bin.to, bin.count, bin.marked])).toEqual([
      [-10, -5, 2, false], [-5, 0, 0, false], [0, 5, 3, true], [5, 10, 2, false],
    ])
    expect(bins.reduce((sum, bin) => sum + bin.count, 0)).toBe(found.totals.length)
  })

  it('makes room for a total beyond every random one', () => {
    const bins = distribution([0, 1, 2], 8, 4)

    expect([bins[0].from, bins[3].to]).toEqual([0, 8])
    expect(bins.map((bin) => [bin.count, bin.marked])).toEqual([[2, false], [1, false], [0, false], [0, true]])
  })

  it('draws one bar when every result is the same, and none without a result', () => {
    expect(distribution([1.7, 1.7], 1.7)).toEqual([{ from: 1.2, to: 2.2, count: 2, marked: true }])
    expect(distribution([], 1.7)).toEqual([])
  })

  it('says in a sentence what the random entries were and what they gave', () => {
    expect(randomSentence(found)).toBe(
      'The same days traded 200 times with 13 entries at random moments, long or short by a coin, under the same rules: ' +
        'half of them ended above +3.40%, 90 in 100 between −13.60% and +3.43%.',
    )
    expect(randomSentence({ ...found, entries: 1 })).toContain('with 1 entry at random moments')
  })

  it('reads from the trades how much room a stop and a target have', () => {
    const figures = {
      winners_lowest: { median: -0.68, tenth: -4.2 }, losers_highest: { median: 0.9, tenth: 3 },
    } as BacktestFigures

    expect(roomSentences(figures)).toEqual([
      'Half of the winning trades never fell below −0.68%, nine in ten never below −4.20%: a stop tighter than that would have sold winners.',
      'Half of the losing trades had been above +0.90% before they lost, one in ten above +3.00%.',
    ])
    expect(roomSentences({ winners_lowest: null, losers_highest: null } as BacktestFigures)).toEqual([])
  })
})

describe('prices kept up to date by the service', () => {
  const keep = (changes: Partial<PriceKeepStatus>): PriceKeepStatus => ({
    enabled: true, days: 120, state: 'due', missing: 12, settled: 0, failures: 0, next_at: null, limits: { days: 730 },
    ...changes,
  })
  const moment = (iso: string) => iso.slice(11, 16)

  it('says what it is doing, or waiting for', () => {
    expect(keepSentence(keep({ enabled: false, state: 'off' }), moment)).toBe('Prices are fetched only when asked for below.')
    expect(keepSentence(keep({ state: 'fetching' }), moment)).toBe('Fetching: 12 days still to complete.')
    expect(keepSentence(keep({ state: 'up_to_date', missing: 0 }), moment)).toMatch(/^Up to date/)
    expect(keepSentence(keep({ state: 'waiting_for_close', missing: 1 }), moment)).toBe(
      '1 day to fetch, once the market has closed: nothing is downloaded while WATA may be trading.',
    )
    expect(keepSentence(keep({ state: 'pausing', next_at: '2026-10-13T02:05:00Z' }), moment)).toBe(
      '12 days to fetch. Next download at 02:05: a few days at a time.',
    )
    expect(keepSentence(keep({ state: 'due' }), moment)).toBe('12 days to fetch: the next download starts within a minute.')
  })

  it('says how long the source is left alone after it stopped answering', () => {
    expect(keepSentence(keep({ state: 'backing_off', failures: 1, next_at: '2026-10-13T02:00:00Z' }), moment)).toBe(
      '12 days to fetch. The source stopped answering 1 time in a row: left alone until 02:00.',
    )
    expect(keepSentence(keep({ state: 'backing_off', failures: 3, next_at: null }), moment)).toContain('3 times in a row: left alone until later.')
  })
})

describe('the turbo measured on real trades', () => {
  const measured: BacktestCalibrationResult = {
    trades: 42, used: 38, without_prices: 3, unreadable: 0, set_aside: 1, fitted: true, reason: null, sample: 'few',
    entry_price: 5.1, entry_price_low: 4.2, entry_price_high: 7.9, points_per_unit: 104.2, points_per_unit_low: 96.5,
    points_per_unit_high: 113.3, level: 90, spread: 0.04, median_error: 0.3, ninth_error: 1.1,
  }

  it('is said with the trades it rests on and how far it is from them', () => {
    expect(calibrationSentences(measured)).toEqual([
      '42 closed trades: 38 used, 3 without index prices for their day, 1 set aside as unlike the others.',
      'WATA paid 5.1 for a turbo (most between 4.2 and 7.9).',
      '1.00 of turbo for 104.2 index points (90 in 100: 96.5 to 113.3), spread 0.04.',
      'Half of the real trades are within 0.3 points of percent of what this turbo gives, nine in ten within 1.1.',
    ])
  })

  it('is said to be a first idea on few trades, and unbounded when they do not bound it', () => {
    const sentences = calibrationSentences({ ...measured, trades: 8, used: 8, without_prices: 0, set_aside: 0, sample: 'too_few', points_per_unit_high: null })

    expect(sentences[0]).toBe('8 closed trades: 8 used.')
    expect(sentences[2]).toContain('96.5 to no upper bound')
    expect(sentences.at(-1)).toBe('Under 30 trades: a first idea, to be measured again when there are more.')
  })

  it('says why no turbo could be measured', () => {
    const none: BacktestCalibrationResult = {
      trades: 2, used: 2, without_prices: 0, unreadable: 0, set_aside: 0, fitted: false,
      reason: '2 trade(s) with prices: 5 at least are needed', sample: 'too_few',
      entry_price: 4.4, entry_price_low: 4.4, entry_price_high: 4.5,
    }

    expect(calibrationSentences(none).at(-1)).toBe('No turbo could be measured: 2 trade(s) with prices: 5 at least are needed.')
  })
})

describe('how a run was made', () => {
  it('says how its fills were taken, and nothing when they were as kind as the bars', () => {
    expect(executionSentence({ capital: 10000, fill_delay: 0, slippage: 0 })).toBeNull()
    expect(executionSentence(undefined)).toBeNull()
    expect(executionSentence({ capital: 0, fill_delay: 30, slippage: 0 })).toBe('Each decision is filled 30 s after the next ten seconds.')
    expect(executionSentence({ capital: 0, fill_delay: 0, slippage: 1 })).toBe('Each fill loses 1 index point.')
    expect(executionSentence({ capital: 0, fill_delay: 20, slippage: 2.5 })).toBe(
      'Each decision is filled 20 s after the next ten seconds, and each fill loses 2.5 index points.',
    )
  })

  it('says why it would come out otherwise today, and nothing when it would not or cannot be told', () => {
    expect(staleSentence(null)).toBeNull()
    expect(staleSentence(undefined)).toBeNull()
    expect(staleSentence({ code: false, prices: false, version: '0.12.0' })).toBeNull()
    expect(staleSentence({ code: true, prices: false, version: '0.12.0' })).toBe(
      'This run would come out otherwise today: the code that replays has changed since (it was made with WATA 0.12.0). Run it again before comparing it with a new one.',
    )
    expect(staleSentence({ code: false, prices: true, version: null })).toContain('today: the prices of its period are not the ones it read.')
    expect(staleSentence({ code: true, prices: true, version: null })).toContain('WATA of another version), and the prices')
  })

  it('writes a size on disk in the unit that reads best', () => {
    expect([0, 999, 1000, 48_200, 600_000, 14_000_000, 2_340_000_000].map(formatBytes)).toEqual([
      '0 B', '999 B', '1.0 kB', '48.2 kB', '600 kB', '14.0 MB', '2.3 GB',
    ])
    expect(formatBytes(undefined)).toBe('-')
  })
})

describe('a sweep', () => {
  const part = (total: number, trades = 10) => ({ total_percent: total, trades, win_rate: 60, worst_drawdown: -3, expectancy: total / trades }) as BacktestFigures
  const sweep = (heldOut: (number | null)[] = [2.2, 3.1, -4.2, 1.5]): BacktestSweep => ({
    axes: [
      { kind: 'rule', name: 'day_profit_target', values: [1.7, 3] },
      { kind: 'parameter', name: 'kernel_bandwidth', values: [8, 15] },
    ],
    held_out: { share: 0.3, from: heldOut[0] === null ? null : '2026-09-20', days: heldOut[0] === null ? 0 : 6, tuning_days: 14 },
    cells: [[1.7, 8, 4.1], [1.7, 15, 5.0], [3, 8, 9.6], [3, 15, -2.0]].map(([target, bandwidth, tuning], index) => ({
      values: [target, bandwidth], tuning: part(tuning), held_out: heldOut[index] === null ? null : part(heldOut[index] as number, 5), whole: part(tuning),
    })),
  })
  const total = METRICS[0]

  it('is named for what it gave several values to', () => {
    const run_ = run({ kind: 'sweep', axes: sweep().axes, rules: { stoploss_percent: -10 } })

    expect(isSweep(run_)).toBe(true)
    expect(runName(run_)).toBe('Sweep of day profit target × kernel bandwidth · stoploss percent -10')
    expect(whatDiffers(run({ kind: 'sweep', replays: 'signals', axes: sweep().axes.slice(0, 1) }))).toBe('Sweep of day profit target on real signals')
    expect(isSweep(run())).toBe(false)
  })

  it('lays its cells out with a row for each value of the first setting and a column for each of the second', () => {
    const grid = sweepGrid(sweep(), total, 'tuning')

    expect([grid.rows, grid.columns]).toEqual([[1.7, 3], [8, 15]])
    expect(grid.cells.map((row) => row.map((cell) => [cell.index, cell.value]))).toEqual([
      [[0, 4.1], [1, 5.0]], [[2, 9.6], [3, -2.0]],
    ])
    expect(sweepGrid(sweep(), total, 'held_out').cells[1].map((cell) => cell.value)).toEqual([-4.2, 1.5])
    // Trades, for the same cells.
    expect(sweepGrid(sweep(), METRICS[4], 'held_out').cells[0].map((cell) => cell.value)).toEqual([5, 5])
  })

  it('is one column of cells when only one setting was given values', () => {
    const one = { ...sweep(), axes: sweep().axes.slice(0, 1), cells: sweep().cells.slice(0, 2).map((cell) => ({ ...cell, values: cell.values.slice(0, 1) })) }

    const grid = sweepGrid(one, total, 'tuning')

    expect([grid.rows, grid.columns]).toEqual([[1.7, 3], ['']])
    expect(grid.cells.map((row) => row.map((cell) => cell.index))).toEqual([[0], [1]])
    // No part held out: no figure, not a zero.
    expect(sweepGrid(sweep([null, null, null, null]), total, 'held_out').cells[0][0].value).toBeNull()
  })

  it('tints a gain or a loss by how far it is from zero, and a count by where it stands', () => {
    const totals = [4.1, 5.0, 9.6, -2.0]

    expect(heat(9.6, totals, true)).toEqual({ tone: 'positive', strength: 1 })
    expect(heat(-2.0, totals, true).tone).toBe('negative')
    expect(heat(-2.0, totals, true).strength).toBeCloseTo(0.208, 3)
    expect(heat(0, totals, true)).toEqual({ tone: 'neutral', strength: 0 })
    expect(heat(null, totals, true)).toEqual({ tone: 'neutral', strength: 0 })
    expect([heat(5, [5, 10, 15], false), heat(10, [5, 10, 15], false), heat(15, [5, 10, 15], false)]).toEqual([
      { tone: 'neutral', strength: 0 }, { tone: 'neutral', strength: 0.5 }, { tone: 'neutral', strength: 1 },
    ])
    // Every cell the same, or none known: nothing stands out.
    expect(heat(12, [12, 12], false).strength).toBe(0)
    expect(heat(0, [0, 0], true).strength).toBe(0)
  })

  it('names a cell by the values it was replayed with', () => {
    expect(cellName(sweep().axes, sweep().cells[2])).toBe('day profit target 3, kernel bandwidth 8')
  })

  it('says where the best of the first days stood on the days held out', () => {
    const lost = sweepVerdict(sweep())

    expect(lost).toMatchObject({ best: 2, held: false })
    expect(lost?.sentence).toBe(
      'On the 14 days a value may be chosen on, the best was day profit target 3, kernel bandwidth 8: +9.60%. ' +
        'On the 6 days held out it made −4.20%: number 4 of 4. ' +
        'It did not hold up on days that had no say in the choice: it was fitted to the first ones.',
    )
    const held = sweepVerdict(sweep([2.2, 3.1, 6.0, 1.5]))
    expect(held).toMatchObject({ best: 2, held: true })
    expect(held?.sentence).toContain('it made +6.00%: the best again. It held up on days that had no say in the choice.')
    // The second of four is still among the better half.
    expect(sweepVerdict(sweep([2.2, 3.1, 2.5, 1.5]))?.held).toBe(true)
    expect(sweepVerdict(sweep([2.2, 3.1, 2.0, 2.1]))?.held).toBe(false)
  })

  it('says that nothing can be told of a choice when no day was held out', () => {
    const verdict = sweepVerdict(sweep([null, null, null, null]))

    expect(verdict).toMatchObject({ best: 2, held: null })
    expect(verdict?.sentence).toContain('too few days to hold any out: nothing here says that value would do best again.')
    expect(sweepVerdict({ ...sweep(), cells: [] })).toBeNull()
  })

  it('reads the values typed for a setting, or says why they cannot be used', () => {
    const bandwidth = { kind: 'integer', least: 2, most: 500 }

    expect(axisValues('8, 15 ,25', bandwidth, 12)).toEqual({ values: [8, 15, 25] })
    expect(axisValues('1, 1.7', { kind: 'number', least: 0.01, most: 100 }, 12)).toEqual({ values: [1, 1.7] })
    expect(axisValues('on, OFF', { kind: 'boolean', least: null, most: null }, 12)).toEqual({ values: [true, false] })
    expect(axisValues('cross, cross_catchup', { kind: 'text', least: null, most: null, choices: ['cross', 'cross_catchup'] }, 12)).toEqual({
      values: ['cross', 'cross_catchup'],
    })
    expect(axisValues('8', bandwidth, 12)).toEqual({ error: 'Two values at least, separated by commas.' })
    expect(axisValues('8, 8', bandwidth, 12)).toEqual({ error: 'Each value once.' })
    expect(axisValues('1,2,3,4', bandwidth, 3)).toEqual({ error: '3 values at most.' })
    expect(axisValues('8, 1000', bandwidth, 12)).toEqual({ error: '1000: Between 2 and 500.' })
    expect(axisValues('8, 9.5', bandwidth, 12)).toEqual({ error: '9.5: A whole number is needed here.' })
    expect(axisValues('on, maybe', { kind: 'boolean', least: null, most: null }, 12)).toEqual({ error: 'On or off.' })
    expect(axisValues('cross, crossing', { kind: 'text', least: null, most: null, choices: ['cross', 'cross_catchup'] }, 12)).toEqual({
      error: 'One of cross, cross_catchup.',
    })
  })
})

describe('one trade followed', () => {
  const path = (points: BacktestTradePath['points'], levels: Partial<BacktestTradePath['levels']> = {}): BacktestTradePath => ({
    id: 'a', leverage: 110, timezone: 'Europe/Paris', trade: trade('2026-10-12T07:00:00Z', 1.7), day_before_percent: 0, points,
    levels: { stop: -15, take_profit: 60, day_target: 1.7, trailing_activation: 5, trailing_drawdown: 8, ...levels },
  })

  it('draws the levels the trade came near, and names the others', () => {
    // From −0.7 % to +1.7 %: the day target is in it, the stop and the take-profit far beyond.
    const view = pathView(path([[0, 0.4, -0.7, 0.6, null], [10, 1.7, 0.4, 1.7, null]]))

    expect(view.shown.map((level) => level.id)).toEqual(['day_target'])
    expect(view.away.map((level) => [level.id, level.value])).toEqual([['stop', -15], ['take_profit', 60]])
    expect(view.domain[0]).toBeCloseTo(-0.892, 3)
    expect(view.domain[1]).toBeCloseTo(1.892, 3)
  })

  it('draws the stop of a trade that went to it', () => {
    const view = pathView(path([[0, -3, -4, 0, null], [10, -15.4, -15.4, -3, null]]))

    expect(view.shown.map((level) => [level.id, level.tone])).toEqual([['stop', 'negative'], ['day_target', 'positive']])
    expect(view.domain[0]).toBeLessThan(-15.4)
  })

  it('gives a trade that went nowhere a point of percent each way to be seen in', () => {
    const view = pathView(path([[0, 0, 0, 0, null]], { day_target: 0.5 }))

    expect(view.shown.map((level) => level.id)).toEqual(['day_target'])
    expect(view.domain).toEqual([-0.08, 0.58])
  })
})

describe('what a run changed', () => {
  it('sets each parameter against the default of the strategy', () => {
    const changes = parameterChanges(run({ parameters: { kernel_bandwidth: 8 } }), [
      { name: 'kernel_bandwidth', kind: 'integer', default: 15, help: '', least: 2, most: 500, choices: [], advanced: false },
    ])

    expect(changes).toEqual([{ name: 'kernel_bandwidth', label: 'Kernel bandwidth', before: '15', after: '8' }])
  })

  it('sets each rule against what the live configuration said then, under the name the form gives it', () => {
    const changed = run({ rules: { day_profit_target: 3, trailing_enabled: false }, rules_before: { day_profit_target: 1.7, trailing_enabled: true } })

    expect(ruleChanges(changed, [{ name: 'day_profit_target', kind: 'number', label: 'Daily target', least: 0, most: 100 }])).toEqual([
      { name: 'day_profit_target', label: 'Daily target', before: '1.7', after: '3' },
      { name: 'trailing_enabled', label: 'Trailing enabled', before: 'on', after: 'off' },
    ])
  })

  it('still names a change whose old value was not kept', () => {
    expect(ruleChanges(run({ rules: { max_trades_per_day: 3 } }))[0]).toMatchObject({ before: '-', after: '3' })
  })
})

describe('the figures of a run', () => {
  const whole = {
    ...run(),
    summary: {
      runs: [
        { turbo: { points_per_unit: 80 }, summary: { trades: 1 } },
        { turbo: { points_per_unit: 110 }, summary: { trades: 2 } },
      ],
    },
  } as unknown as BacktestRun

  it('are read for one leverage', () => {
    expect(figuresAt(whole, 110)?.trades).toBe(2)
    expect(figuresAt(whole, 140)).toBeNull()
  })

  it('are shown first for the middle leverage', () => {
    expect(middleLeverage([80, 110, 140])).toBe(110)
    expect(middleLeverage([80, 110])).toBe(110)
    expect(middleLeverage([110])).toBe(110)
  })

  it('change by themselves only while the run waits or runs', () => {
    expect(['queued', 'running'].map((state) => isActive(state as never))).toEqual([true, true])
    expect(['done', 'failed', 'cancelled', 'interrupted'].some((state) => isActive(state as never))).toBe(false)
  })
})

describe('the days of a run for the charts', () => {
  const days: BacktestDay[] = [
    { day: '2026-10-12', entries: 2, closed: 2, day_percent: -13.56 },
    { day: '2026-10-13', entries: 1, closed: 1, day_percent: 1.7 },
  ]
  const trades = [
    trade('2026-10-12T07:00:00Z', 1.7),
    trade('2026-10-12T08:00:00Z', -15),
    // Half past midnight in Paris is already the 13th.
    trade('2026-10-12T22:30:00Z', 1.7),
  ]

  it('carry each result and all of them so far, one applied on the other', () => {
    const daily = dailyFromBacktest(days, trades, 'Europe/Paris')

    expect(daily.map((day) => [day.day_date, day.profit_loss, day.cumulative_profit_loss])).toEqual([
      ['2026-10-12', -13.56, -13.56],
      ['2026-10-13', 1.7, -12.09],
    ])
  })

  it('count the trades won and lost on the day they were closed, in the trading timezone', () => {
    const daily = dailyFromBacktest(days, trades, 'Europe/Paris')

    expect(daily.map((day) => [day.trade_count, day.win_count, day.loss_count])).toEqual([
      [2, 1, 1],
      [1, 1, 0],
    ])
  })

  it('apply percentages on one another', () => {
    expect(compounded([])).toBe(0)
    expect(compounded([10, -10])).toBe(-1)
    expect(compounded([1.7, 1.7])).toBe(3.43)
  })

  it('know which day a moment is in a timezone', () => {
    expect(localDay('2026-10-12T22:30:00Z', 'Europe/Paris')).toBe('2026-10-13')
    expect(localDay('2026-10-12T22:30:00Z', 'UTC')).toBe('2026-10-12')
  })
})

describe('why trades were entered, left or refused', () => {
  it('lists the reasons, the most frequent first, with the share of each', () => {
    expect(tally({ Stoploss: 1, 'Daily profit target': 3 })).toEqual([
      { reason: 'Daily profit target', count: 3, share: 0.75 },
      { reason: 'Stoploss', count: 1, share: 0.25 },
    ])
    expect(tally({})).toEqual([])
  })
})

describe('runs side by side', () => {
  const first = run({ id: 'a', parameters: { entry_mode: 'cross_catchup', kernel_bandwidth: 8 }, rules: { day_profit_target: 3 } })
  const second = run({ id: 'b', parameters: { kernel_bandwidth: 8 }, turbo: { entry_price: 4.4, spread: 0.03, leverages: [110, 140, 200] } })

  it('show the parameters and rules that are not the same in all of them', () => {
    expect(differing([first, second])).toEqual({ parameters: ['entry_mode'], rules: ['day_profit_target'] })
    expect(differing([first, first])).toEqual({ parameters: [], rules: [] })
  })

  it('are compared on the leverages they were all made with', () => {
    expect(commonLeverages([first, second])).toEqual([110, 140])
    expect(commonLeverages([])).toEqual([])
  })
})

describe('what is typed in the form', () => {
  const bandwidth = { kind: 'integer', least: 2, most: 500 }

  it('is a number, or not yet', () => {
    expect(numberOf(' 4.4 ')).toBe(4.4)
    expect(numberOf('')).toBeNull()
    expect(numberOf('4,4')).toBeNull()
    expect(numberOf('-')).toBeNull()
  })

  it('is refused with the reason when the field cannot take it', () => {
    expect(fieldError('8', bandwidth)).toBeNull()
    expect(fieldError('eight', bandwidth)).toBe('A number is needed here.')
    expect(fieldError('8.5', bandwidth)).toBe('A whole number is needed here.')
    expect(fieldError('1', bandwidth)).toBe('Between 2 and 500.')
    expect(fieldError('-15', { kind: 'number', least: -100, most: -0.1 })).toBeNull()
    expect(fieldError('', { kind: 'text', least: null, most: null })).toBe('Needed.')
    expect(fieldError('08:00', { kind: 'text', least: null, most: null })).toBeNull()
  })

  it('becomes the kind of value the server takes', () => {
    expect(typed('8', 'integer')).toBe(8)
    expect(typed('6.0', 'number')).toBe(6)
    expect(typed(' cross ', 'text')).toBe('cross')
  })

  it('gives the leverages as numbers, each once and above zero', () => {
    expect(leveragesOf('80, 110,140')).toEqual([80, 110, 140])
    expect(leveragesOf('110')).toEqual([110])
    expect(leveragesOf('')).toBeNull()
    expect(leveragesOf('80, 80')).toBeNull()
    expect(leveragesOf('80, lots')).toBeNull()
    expect(leveragesOf('0, 110')).toBeNull()
  })
})

describe('names and days', () => {
  it('writes a parameter name as words', () => {
    expect(wording('min_band_width')).toBe('Min band width')
    expect(wording('kernel_vwap')).toBe('Kernel vwap')
  })

  it('reads a calendar day as a date of the browser, whatever its timezone', () => {
    const date = dayToDate('2026-10-12')

    expect([date.getFullYear(), date.getMonth(), date.getDate()]).toEqual([2026, 9, 12])
  })
})

describe('the prices a run is made on', () => {
  it('are said in words, Dukascopy’s for a run made before there was a choice', () => {
    expect(pricesWording({ prices: 'capital' })).toBe('Capital.com’s recorded prices')
    expect(pricesWording({ prices: 'dukascopy' })).toBe('Dukascopy’s history')
    expect(pricesWording({})).toBe('Dukascopy’s history')
  })

  it('are told apart when runs compared were not made on the same ones', () => {
    expect(mixedPrices([{ prices: 'capital' }, {}])).toBe(true)
    expect(mixedPrices([{ prices: 'dukascopy' }, {}])).toBe(false)
    expect(mixedPrices([{ prices: 'capital' }, { prices: 'capital' }])).toBe(false)
  })
})

