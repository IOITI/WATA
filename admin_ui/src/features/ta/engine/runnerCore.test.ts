/// <reference types="node" />
import { createHash } from 'node:crypto'

import { describe, expect, it } from 'vitest'

import type { SeriesAsk } from './provider'
import { detailOf, MAX_RUNTIME_WARNINGS, plotValuesAt, Runner } from './runnerCore'
import type { BarRow, PinnedLibrary, RunnerResponse } from './runnerCore'

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

function runner(ask: SeriesAsk = nothingElse) {
  return new Runner(ask, sha256, { pinets: '0.11.1', node: 'v24' })
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
