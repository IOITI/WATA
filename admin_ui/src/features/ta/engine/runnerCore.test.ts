/// <reference types="node" />
import { createHash } from 'node:crypto'

import { describe, expect, it, vi } from 'vitest'

import type { SeriesAsk } from './provider'
import { codeOf, detailOf, differencesOf, HELD_HEAP_SHARE, MAX_RUNTIME_WARNINGS, plotValuesAt, Runner, wholeBecause } from './runnerCore'
import type { BarRow, HeapMeasure, PinnedLibrary, RunnerResponse } from './runnerCore'

// PineTS as it is, counting the indicators made: each one is a compile of a script.
const made = vi.hoisted(() => ({ indicators: 0 }))
vi.mock('pinets', async (importOriginal) => {
  const pinets = await importOriginal<typeof import('pinets')>()
  class Counted extends pinets.Indicator {
    constructor(...args: ConstructorParameters<typeof pinets.Indicator>) {
      super(...args)
      made.indicators += 1
    }
  }
  return { ...pinets, Indicator: Counted }
})

const sha256 = (text: string) => createHash('sha256').update(text, 'utf8').digest('hex')
const MINUTE = 60_000
const T0 = Date.UTC(2026, 9, 15, 6, 0)

/** Bars that rise for a while then fall: the averages cross both ways. */
function bars(count: number, from = 0): BarRow[] {
  return Array.from({ length: count }, (_, offset) => {
    const index = from + offset
    const close = 25_000 + 40 * Math.sin(index / 12)
    return [T0 + index * MINUTE, close - 1, close + 2, close - 3, close, 5, T0 + (index + 1) * MINUTE]
  })
}

const SCRIPT = `//@version=6
indicator("Cross")
import someone/Maths/1 as maths
fast = ta.ema(close, input.int(5, "Fast"))
slow = ta.ema(close, 20)
plot(maths.half(fast + slow), "Mid")
alertcondition(ta.crossover(fast, slow), title = "Up", message = '{"action": "long"}')
alertcondition(ta.crossunder(fast, slow), title = "Down", message = '{"action": "short"}')
`

const MATHS = `//@version=6
library("Maths")

export half(float value) =>
    value / 2
`

const library: PinnedLibrary = { id: 'aaaaaaaaaaaa', name: 'Maths', version: 2, source: MATHS, sha256: sha256(MATHS) }
const nothingElse: SeriesAsk = async (symbol) => {
  throw new Error(`No bars of ${symbol}`)
}

function runner(ask: SeriesAsk = nothingElse, heap?: HeapMeasure) {
  return new Runner(ask, sha256, { pinets: '0.11.1', node: 'v24' }, heap)
}

function load(target: Runner, rule: string, rows: BarRow[], changes: Record<string, unknown> = {}) {
  return target.handle({
    id: 1,
    op: 'load',
    rule,
    script: { source: SCRIPT, sha256: sha256(SCRIPT) },
    libraries: [library],
    inputs: { in_0: 4 },
    ticker: 'CAPITAL_US100',
    timeframe: '1',
    bars: rows,
    ...changes,
  } as Parameters<Runner['handle']>[0])
}

function ok<T extends RunnerResponse>(response: RunnerResponse): Extract<T, { ok: true }> {
  if (!response.ok) throw new Error(`Refused: ${response.error.message}`)
  return response as Extract<T, { ok: true }>
}

describe('the runner of the TA engine', () => {
  it('says what it runs', async () => {
    expect(await runner().handle({ id: 7, op: 'hello' })).toEqual({ id: 7, ok: true, op: 'hello', pinets: '0.11.1', node: 'v24' })
  })

  it('runs a rule on its history with the library it pinned, and lists every alert', async () => {
    const target = runner()

    const loaded = ok(await load(target, 'r1', bars(200)))

    expect(loaded).toMatchObject({ op: 'load', kind: 'indicator', title: 'Cross', bars: 200 })
    if (loaded.op !== 'load') return
    expect(loaded.inputs.map((input) => [input.id, input.title])).toEqual([['in_0', 'Fast']])
    expect(new Set(loaded.alerts.map((alert) => alert.title))).toEqual(new Set(['Up', 'Down']))
    expect(loaded.alerts[0]).toMatchObject({ type: 'alertcondition', message: expect.stringContaining('"action"') })
    // The plots at the bars of the alerts: what a confidence or a message's {{plot}} reads.
    expect(Object.keys(loaded.plots.Mid)).toEqual(expect.arrayContaining(loaded.alerts.map((alert) => String(alert.time))))
    expect(target.loaded()).toEqual(['r1'])
  })

  it('gives for each new bar what a run over all the bars gives', async () => {
    const all = bars(260)
    const whole = ok(await load(runner(), 'whole', all))
    const target = runner()
    ok(await load(target, 'stepped', all.slice(0, 200)))

    const added = []
    for (let index = 200; index < 260; index += 1) {
      const appended = ok(await target.handle({ id: index, op: 'append', rule: 'stepped', bars: [all[index]] }))
      if (appended.op === 'append') added.push(...appended.alerts)
    }

    const from = all[200][0]
    const expected = whole.op === 'load' ? whole.alerts.filter((alert) => alert.time >= from) : []
    expect(added).toEqual(expected)
    expect(added.length).toBeGreaterThan(0)
  })

  it('compiles a rule once while its script and inputs stay the same', async () => {
    const target = runner()
    const before = made.indicators
    ok(await load(target, 'r1', bars(200)))
    // Another day of the rule, as a replay loads it: other bars, the same script and inputs.
    const again = ok(await load(target, 'r1', bars(200, 120)))
    expect(made.indicators - before).toBe(1)

    // What a runner that compiles the script afresh answers on the same bars.
    const fresh = ok(await load(runner(), 'r1', bars(200, 120)))
    if (again.op !== 'load' || fresh.op !== 'load') return
    expect(again.alerts.length).toBeGreaterThan(0)
    expect(again.alerts).toEqual(fresh.alerts)
    expect(again.plots).toEqual(fresh.plots)
    expect(again.inputs).toEqual(fresh.inputs)

    // Other inputs, or another rule, are compiled for themselves.
    ok(await load(target, 'r1', bars(200, 120), { inputs: { in_0: 6 } }))
    ok(await load(target, 'r2', bars(200, 120)))
    expect(made.indicators - before).toBe(4)
  })

  it('answers only the alerts and plots of the bars added', async () => {
    const target = runner()
    ok(await load(target, 'r1', bars(100)))

    const appended = ok(await target.handle({ id: 2, op: 'append', rule: 'r1', bars: bars(3, 100) }))

    expect(appended).toMatchObject({ op: 'append', bars: 103 })
    if (appended.op !== 'append') return
    expect(appended.alerts.every((alert) => alert.time >= bars(1, 100)[0][0])).toBe(true)
    expect(Object.keys(appended.plots.Mid)).toEqual(bars(3, 100).map((bar) => String(bar[0])).reverse())
  })

  it('refuses a source that is not the version pinned, and a library it did not pin', async () => {
    const tampered = await load(runner(), 'r1', bars(10), { script: { source: `${SCRIPT}// edited\n`, sha256: sha256(SCRIPT) } })
    const unpinned = await load(runner(), 'r1', bars(10), { libraries: [] })

    expect(tampered).toMatchObject({ ok: false, error: { message: 'The source of the script is not the version the rule pinned' } })
    expect(unpinned).toMatchObject({ ok: false, error: { message: expect.stringContaining('No library "Maths"'), file: 'script', line: 3 } })
  })

  it('says where a script does not compile, in its own lines', async () => {
    const broken = SCRIPT.replace('ta.ema(close, 20)', 'ta.ema(close, 20')

    const response = await load(runner(), 'r1', bars(10), { script: { source: broken, sha256: sha256(broken) } })

    expect(response).toMatchObject({ ok: false, error: { file: 'script', line: expect.any(Number) } })
  })

  it('refuses bars out of order, and bars for a rule it does not hold', async () => {
    const target = runner()
    ok(await load(target, 'r1', bars(20)))

    const backwards = await target.handle({ id: 3, op: 'append', rule: 'r1', bars: bars(1, 5) })
    const unknown = await target.handle({ id: 4, op: 'append', rule: 'r2', bars: bars(1, 20) })
    await target.handle({ id: 5, op: 'unload', rule: 'r1' })

    expect(backwards).toMatchObject({ ok: false, error: { message: expect.stringContaining('out of order') } })
    expect(unknown).toMatchObject({ ok: false, error: { message: 'No rule r2 is loaded' } })
    expect(target.loaded()).toEqual([])
  })

  it('asks the engine for the series of another symbol', async () => {
    const asked: string[] = []
    const source = '//@version=6\nindicator("x")\nplot(request.security("CAPITAL_DE40", "5", close), "Other")\n'
    const target = runner(async (symbol, timeframe) => {
      asked.push(`${symbol} ${timeframe}`)
      return bars(40).map(([openTime, open, high, low, close, volume]) => ({
        openTime, open, high, low, close: close - 1000, volume, closeTime: openTime + 5 * MINUTE,
      }))
    })

    const loaded = ok(await load(target, 'r1', bars(40), { script: { source, sha256: sha256(source) }, libraries: [] }))

    expect(asked).toEqual(['CAPITAL_DE40 5'])
    expect(loaded.ok).toBe(true)
  })
})

// A script that reads much of what PineTS keeps from bar to bar: averages, bands, an anchored VWAP, var
// state and arrays, a library function whose weights are made on the first bar, times in Paris, labels,
// alerts of both kinds.
const MANY = `//@version=6
indicator("Many", max_labels_count = 5)
import someone/Weights/1 as weights
fast = ta.ema(close, input.int(5, "Fast"))
slow = ta.sma(close, 20)
r = ta.rsi(close, 14)
[middle, upper, lower] = ta.bb(close, 20, 2)
paris = hour(time, "Europe/Paris") * 60 + minute(time, "Europe/Paris")
anchor = paris == 8 * 60 or bar_index % 97 == 0
[vwap, vwapUp, vwapDown] = ta.vwap(hlc3, anchor, 1)
var float peak = na
peak := na(peak) or anchor ? high : math.max(peak, high)
var crosses = array.new<int>()
up = ta.crossover(fast, slow)
down = ta.crossunder(fast, slow)
if up
    array.push(crosses, bar_index)
    label.new(bar_index, high, str.tostring(r, "#.##"))
eight = timestamp("Europe/Paris", year, month, dayofmonth, 8, 0, 0)
plot(weights.smooth(close, 8), "Smooth")
plot(r, "RSI")
plot(ta.atr(3), "ATR")
plot(vwapUp - vwapDown, "VWAP width")
plot(upper - lower, "Bands")
plot(ta.highest(high, 30) - ta.lowest(low, 30), "Range")
plot(peak, "Peak")
plot(array.size(crosses), "Crosses")
plot(nz(ta.barssince(down), -1), "Since down")
plot(time >= eight ? paris : -paris, "Paris")
alertcondition(up, title = "Up", message = '{"action": "long"}')
alertcondition(down, title = "Down", message = '{"action": "short"}')
if ta.crossover(r, 60)
    alert("RSI over 60 at " + str.tostring(close), alert.freq_once_per_bar)
`

const WEIGHTS = `//@version=6
library("Weights")

weights(int size) =>
    var w = array.new<float>()
    if barstate.isfirst
        for i = 0 to size - 1
            array.push(w, 1.0 / (i + 1))
    w

export smooth(series float source, int size) =>
    w = weights(size)
    float total = 0.0
    float sum = 0.0
    for i = 0 to math.min(size - 1, bar_index)
        total += nz(source[i]) * array.get(w, i)
        sum += array.get(w, i)
    total / sum
`

const weightsLibrary: PinnedLibrary = { id: 'bbbbbbbbbbbb', name: 'Weights', version: 1, source: WEIGHTS, sha256: sha256(WEIGHTS) }

/** Ten-minute bars over two days, so that Paris's 08:00 comes twice. */
function tenMinuteBars(count: number): BarRow[] {
  const step = 10 * MINUTE
  return Array.from({ length: count }, (_, index) => {
    const close = 25_000 + 60 * Math.sin(index / 9) + 25 * Math.sin(index / 2.3)
    return [T0 + index * step, close - 4, close + 6, close - 7, close, 3 + (index % 5), T0 + (index + 1) * step]
  })
}

function loadMany(target: Runner, rule: string, rows: BarRow[], changes: Record<string, unknown> = {}) {
  return load(target, rule, rows, {
    script: { source: MANY, sha256: sha256(MANY) }, libraries: [weightsLibrary], inputs: {}, timeframe: '10', ...changes,
  })
}

/** An answer without what differs from one run to the next: its time, and whether its run went on or was whole. */
function result(response: RunnerResponse) {
  const answer = ok(response) as Record<string, unknown>
  const { milliseconds, continued, whole, id, ...rest } = answer
  void milliseconds
  void continued
  void whole
  void id
  return rest
}

/** What a runner holds, as only a test reaches it: a held run's context. */
function heldContext(target: Runner, rule: string): { alerts: { time: number }[] } {
  const rules = (target as unknown as { rules: Map<string, { held: { context: { alerts: { time: number }[] } } | null }> }).rules
  const held = rules.get(rule)?.held
  if (!held) throw new Error(`${rule} holds no run`)
  return held.context
}

describe('continued runs', () => {
  // A whole run at each of the new bars of the runner it is compared with: some seconds.
  it('continue a rule\'s run on each new bar and answer what a whole run over all the bars answers', { timeout: 30_000 }, async () => {
    // Paris's second 08:00 (the anchored VWAP starts again) is the 145th bar: among the new ones.
    const all = tenMinuteBars(200)
    const continuing = runner()
    const whole = runner()
    const loaded = ok(await loadMany(continuing, 'r1', all.slice(0, 120), { continued: true }))
    ok(await loadMany(whole, 'r1', all.slice(0, 120)))
    expect(loaded).toMatchObject({ op: 'load', continued: true, whole: null })

    let alerts = 0
    // One bar at a time, then a few at once, as the engine sends them after a slow tick.
    for (const [from, to] of [...Array.from({ length: 60 }, (_, index) => [120 + index, 121 + index]), [180, 185], [185, 200]]) {
      const request = { id: from, op: 'append' as const, rule: 'r1', bars: all.slice(from, to) }
      const continued = await continuing.handle(request)
      const expected = await whole.handle(request)
      expect(continued).toMatchObject({ continued: true, whole: null })
      expect(result(continued)).toEqual(result(expected))
      if (continued.ok && continued.op === 'append') alerts += continued.alerts.length
    }
    // Alerts were compared too, not plots alone.
    expect(alerts).toBeGreaterThan(2)
    expect(continuing.continuing()).toEqual(['r1'])
  })

  it('run whole a script whose value at a bar a whole run gives otherwise, and say why', async () => {
    const source = MANY.replace('plot(r, "RSI")', 'plot(barstate.islast ? r : na, "RSI")')
    const all = tenMinuteBars(230)
    const continuing = runner()
    const whole = runner()

    const loaded = ok(await loadMany(continuing, 'r1', all.slice(0, 220), { continued: true, script: { source, sha256: sha256(source) } }))
    ok(await loadMany(whole, 'r1', all.slice(0, 220), { script: { source, sha256: sha256(source) } }))
    const request = { id: 2, op: 'append' as const, rule: 'r1', bars: all.slice(220) }
    const appended = await continuing.handle(request)

    expect(loaded).toMatchObject({ continued: false, whole: 'its script reads barstate.islast' })
    expect(appended).toMatchObject({ continued: false, whole: 'its script reads barstate.islast' })
    expect(result(appended)).toEqual(result(await whole.handle(request)))
    expect(continuing.continuing()).toEqual([])
  })

  it('are checked against a whole run over the bars since the last check, which the run then goes on from', async () => {
    const all = tenMinuteBars(260)
    const target = runner()
    ok(await loadMany(target, 'r1', all.slice(0, 200), { continued: true }))
    const nothingYet = ok(await target.handle({ id: 2, op: 'check', rule: 'r1' }))
    for (let index = 200; index < 230; index += 1) ok(await target.handle({ id: index, op: 'append', rule: 'r1', bars: [all[index]] }))

    const checked = ok(await target.handle({ id: 3, op: 'check', rule: 'r1' }))
    const again = ok(await target.handle({ id: 4, op: 'check', rule: 'r1' }))
    const next = ok(await target.handle({ id: 5, op: 'append', rule: 'r1', bars: all.slice(230, 260) }))
    const later = ok(await target.handle({ id: 6, op: 'check', rule: 'r1' }))

    expect(nothingYet).toMatchObject({ op: 'check', compared: 0, same: null, from: null, continued: true })
    expect(checked).toMatchObject({ op: 'check', bars: 230, compared: 30, from: all[200][0], same: true, differences: [], continued: true, whole: null })
    expect(again).toMatchObject({ compared: 0, same: null })
    expect(next).toMatchObject({ continued: true, bars: 260 })
    expect(later).toMatchObject({ compared: 30, from: all[230][0], same: true })
  })

  it('that differ from a whole run are said, and the rule runs whole from then on', async () => {
    const all = tenMinuteBars(240)
    const target = runner()
    const whole = runner()
    ok(await loadMany(target, 'r1', all.slice(0, 200), { continued: true }))
    ok(await loadMany(whole, 'r1', all.slice(0, 220)))
    ok(await target.handle({ id: 2, op: 'append', rule: 'r1', bars: all.slice(200, 220) }))
    // As a PineTS that went on wrongly would have it: an alert no whole run raises.
    heldContext(target, 'r1').alerts.push({ type: 'alertcondition', id: 'alertcondition_0', title: 'Up', message: '', time: all[210][0] } as never)

    const checked = ok(await target.handle({ id: 3, op: 'check', rule: 'r1' }))
    const request = { id: 4, op: 'append' as const, rule: 'r1', bars: all.slice(220, 240) }
    const appended = await target.handle(request)

    expect(checked).toMatchObject({ same: false, compared: 20, continued: false })
    if (checked.op !== 'check') return
    expect(checked.differences).toEqual([`alertcondition "Up" at ${new Date(all[210][0]).toISOString().replace('.000Z', 'Z')} only in the continued run`])
    expect(checked.whole).toBe(`a whole run differed from its continued run: ${checked.differences[0]}`)
    expect(appended).toMatchObject({ continued: false, whole: checked.whole })
    expect(result(appended)).toEqual(result(await whole.handle(request)))
    expect(target.continuing()).toEqual([])
  })

  it('go back to whole runs, the largest first, while held runs keep the heap past its share', async () => {
    const limit = 100_000_000
    let used = 0.3 * limit
    const target = runner(nothingElse, () => ({ used, limit }))
    ok(await loadMany(target, 'small', tenMinuteBars(150), { continued: true }))
    used = 0.6 * limit

    const large = ok(await loadMany(target, 'large', tenMinuteBars(200), { continued: true }))
    const small = ok(await target.handle({ id: 2, op: 'append', rule: 'small', bars: tenMinuteBars(151).slice(150) }))

    expect(large).toMatchObject({
      continued: false, whole: `the runner's heap was 60 % full, past the ${100 * HELD_HEAP_SHARE} % it may be at holding runs`,
    })
    expect(small).toMatchObject({ continued: true })
    expect(target.continuing()).toEqual(['small'])
  })

  it('drop a run a new bar failed in: the engine loads the rule again', async () => {
    const source = `${MANY}if bar_index == 205\n    runtime.error("Bar 205")\n`
    const target = runner()
    ok(await loadMany(target, 'r1', tenMinuteBars(200), { continued: true, script: { source, sha256: sha256(source) } }))

    const failed = await target.handle({ id: 2, op: 'append', rule: 'r1', bars: tenMinuteBars(206).slice(200) })

    expect(failed).toMatchObject({ ok: false, error: { message: expect.stringContaining('Bar 205') } })
    expect(target.continuing()).toEqual([])
  })

  it('are never held for a study, nor said of to a replay that did not ask them', async () => {
    const detailed = ok(await loadMany(runner(), 'r1', tenMinuteBars(60), { continued: true, detail: true }))
    const replayed = ok(await loadMany(runner(), 'r1', tenMinuteBars(60)))

    expect(detailed).not.toHaveProperty('continued')
    expect(replayed).not.toHaveProperty('continued')
  })
})

describe('what a continued run may not be trusted with', () => {
  it('is read in the code alone, its comments and strings left out', () => {
    const script = (code: string) => ({ kind: 'indicator' as const, source: `//@version=6\nindicator("x")\n${code}\n` })

    expect(wholeBecause(script('plot(close) // barstate.islast\nalert("timenow " + str.tostring(close))'))).toBeNull()
    expect(wholeBecause(script("x = 'it\\'s request.security' + \"barstate.islast\"\nplot(barstate.isfirst ? 1 : 0)"))).toBeNull()
    expect(wholeBecause(script('plot(barstate . isconfirmed ? close : na)'))).toBe('its script reads barstate.isconfirmed')
    expect(wholeBecause(script('plot(bar_index == last_bar_index ? 1 : 0)'))).toBe('its script reads last_bar_index')
    expect(wholeBecause(script('plot(session.islastbar ? 1 : 0)'))).toBe('its script reads session.islastbar')
    expect(wholeBecause(script('plot(time - timenow)'))).toBe('its script reads timenow')
    expect(wholeBecause(script('plot(math.random())'))).toBe('its script reads math.random')
    expect(wholeBecause(script('plot(request.security("CAPITAL_DE40", "5", close))'))).toBe('its script reads request.security')
    expect(wholeBecause(script('plot(chart.left_visible_bar_time)'))).toBe('its script reads chart.left_visible_bar_time')
    expect(wholeBecause({ kind: 'strategy' as const, source: '//@version=6\nstrategy("s")\n' })).toBe('it is a strategy(), whose orders a whole run settles')
    expect(codeOf('a = "//x" // y\nb = 1')).toBe('a = "" \nb = 1')
  })

  it('says what differs, the first bars first', () => {
    const at = (minutes: number) => T0 + minutes * MINUTE
    const alert = (time: number, title: string) => ({ type: 'alertcondition', id: 'alertcondition_0', title, message: '', time })

    const differences = differencesOf(
      { alerts: [alert(at(1), 'Up'), alert(at(3), 'Up')], plots: { plot_0: { [at(2)]: 1.5, [at(4)]: null } } },
      { alerts: [alert(at(1), 'Up'), alert(at(5), 'Down')], plots: { plot_0: { [at(2)]: 1.25, [at(4)]: null } } },
    )

    expect(differences).toEqual([
      'plot "plot_0" at 2026-10-15T06:02:00Z: 1.5 continued, 1.25 whole',
      'alertcondition "Up" at 2026-10-15T06:03:00Z only in the continued run',
      'alertcondition "Down" at 2026-10-15T06:05:00Z only in the whole run',
    ])
  })
})

describe('a load in detail, for a study of a script', () => {
  it('gives every bar\'s plot values, and nothing more when it is not asked for', async () => {
    const rows = bars(120)

    const plain = ok(await load(runner(), 'plain', rows))
    const detailed = ok(await load(runner(), 'detailed', rows, { detail: true }))

    expect(Object.keys(plain).sort()).toEqual(['alerts', 'bars', 'id', 'inputs', 'kind', 'milliseconds', 'ok', 'op', 'plots', 'title', 'warnings'])
    if (detailed.op !== 'load' || plain.op !== 'load') return
    expect(detailed.series?.times).toEqual(rows.map((row) => row[0]))
    expect(detailed.series?.plots.Mid).toHaveLength(120)
    // The averages are na over their first bars, then a number at every bar.
    const mid = detailed.series?.plots.Mid ?? []
    const first = mid.findIndex((value) => value !== null)
    expect(first).toBeGreaterThanOrEqual(0)
    expect(first).toBeLessThan(20)
    expect(mid.slice(first).every((value) => typeof value === 'number')).toBe(true)
    expect(detailed.runtimeWarnings).toEqual([])
    // The rest of the answer is the engine's.
    expect(detailed.alerts).toEqual(plain.alerts)
    expect(detailed.plots).toEqual(plain.plots)
  })

  it('keeps each plot at its bar, null where it has none, PineTS\'s drawings left out', () => {
    const candles = bars(3).map(([openTime, open, high, low, close, volume, closeTime]) => ({ openTime, open, high, low, close, volume, closeTime }))
    const times = candles.map((candle) => candle.openTime)

    const found = detailOf(candles, {
      a: { title: 'Fast', data: [{ time: times[0], value: 1 }, { time: times[2], value: Number.NaN }] },
      __labels__: { data: [{ time: times[1], value: 'x' }] },
    }, [])

    expect(found.series.plots).toEqual({ Fast: [1, null, null] })
  })

  it('says each runtime warning once, with how often it came, and few of them', () => {
    const warnings = [
      { message: 'Index out of bounds', bar: 3 }, { message: 'Index out of bounds', bar: 4 },
      ...Array.from({ length: 80 }, (_, index) => ({ message: `other ${index}`, bar: index })),
    ]

    const found = detailOf([], {}, warnings)

    expect(found.runtimeWarnings[0]).toEqual({ message: 'Index out of bounds', bar: 3, count: 2 })
    expect(found.runtimeWarnings).toHaveLength(MAX_RUNTIME_WARNINGS)
  })
})

describe('the plots at some bars', () => {
  it('reads each plot at the bars asked for, PineTS\'s drawings left out', () => {
    const plots = {
      Mid: { title: 'Mid', data: [{ time: 1, value: 10 }, { time: 2, value: Number.NaN }, { time: 3, value: 30 }] },
      __labels__: { data: [{ time: 3, value: 'x' }] },
    }

    expect(plotValuesAt(plots, new Set([2, 3]))).toEqual({ Mid: { 2: null, 3: 30 } })
    expect(plotValuesAt(plots, new Set())).toEqual({})
  })
})
