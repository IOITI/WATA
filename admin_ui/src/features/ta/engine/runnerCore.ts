// What the TA engine (src/ta_engine) asks of PineTS on the server, through
// runner.ts: a signal rule's script, prepared as the pages prepare it, run on the
// closed bars the engine sends.
//
// Each new bar is evaluated by a whole run over the rule's bars, from the first
// one the engine sent: a bar's alerts are those of a run over the same bars,
// which is also what a replay gives (src/ta_engine/replay.py). PineTS's live mode
// (updateTail) is not used: in PineTS 0.11.1 it raised none of the alerts of the
// bars it added to the user's script, and leaves the series of request.security
// a bar or two behind. The engine bounds the bars a rule holds instead.

import { Indicator, PineTS } from 'pinets'
import type { Context, IPineInput } from 'pinets'

import { alertParts, compileFailure } from './pineRun'
import { originOf, PrepareError, prepareScript } from './prepare'
import type { PreparedScript } from './prepare'
import { SeriesProvider } from './provider'
import type { SeriesAsk } from './provider'
import type { Candle } from './workerProtocol'

/** A closed bar as the engine sends it: [open ms, open, high, low, close, volume, close ms]. */
export type BarRow = [number, number, number, number, number, number, number]

/** The bars a rule may hold: the engine starts it again from fewer each day. */
export const MAX_RULE_BARS = 100_000

export interface PinnedSource {
  source: string
  sha256: string
}

export interface PinnedLibrary extends PinnedSource {
  id: string
  name: string
  version: number
}

export interface LoadRequest {
  id: number
  op: 'load'
  rule: string
  script: PinnedSource
  libraries: PinnedLibrary[]
  /** The inputs the rule sets, by the input's id in the script (in_0, ...). */
  inputs: Record<string, unknown>
  /** The chart's ticker and timeframe, as request.security and syminfo see them. */
  ticker: string
  timeframe: string
  bars: BarRow[]
}

export type RunnerRequest =
  | { id: number; op: 'hello' }
  | LoadRequest
  | { id: number; op: 'append'; rule: string; bars: BarRow[] }
  | { id: number; op: 'unload'; rule: string }

export interface RunnerAlert {
  /** alertcondition, or alert. */
  type: string
  /** The call that raised it: alertcondition_0, alert_2... */
  id: string
  title: string | null
  message: string
  /** The open time of the bar that raised it, epoch ms. */
  time: number
}

/** The value of each plot, by its title, at some bars (by their open time, epoch ms). */
export type PlotValues = Record<string, Record<string, number | string | boolean | null>>

export interface RunnerFailure {
  message: string
  /** Where it is, as far as is known: the script, or a library by its name. */
  file: string | null
  line: number | null
  column: number | null
}

export interface RunResult {
  bars: number
  milliseconds: number
  alerts: RunnerAlert[]
  plots: PlotValues
}

export type RunnerResponse =
  | { id: number; ok: true; op: 'hello'; pinets: string; node: string }
  | ({
      id: number
      ok: true
      op: 'load'
      kind: string
      title: string | null
      inputs: IPineInput[]
      warnings: string[]
    } & RunResult)
  | ({ id: number; ok: true; op: 'append' } & RunResult)
  | { id: number; ok: true; op: 'unload' }
  | { id: number; ok: false; error: RunnerFailure }

interface Loaded {
  indicator: Indicator
  prepared: PreparedScript
  ticker: string
  timeframe: string
  candles: Candle[]
}

export function candleOf(row: BarRow): Candle {
  const [openTime, open, high, low, close, volume, closeTime] = row
  return { openTime, open, high, low, close, volume, closeTime }
}

function plotValue(value: unknown): number | string | boolean | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'boolean' || typeof value === 'string') return value
  return null
}

interface PlotLike {
  title?: string
  data?: { time?: number; value?: unknown }[]
}

/** Each plot's value at ``times``. PineTS's own drawings (__labels__, ...) are left out. */
export function plotValuesAt(plots: Record<string, PlotLike> | undefined, times: Set<number>): PlotValues {
  const values: PlotValues = {}
  if (times.size === 0) return values
  const earliest = Math.min(...times)
  for (const [key, plot] of Object.entries(plots ?? {})) {
    if (key.startsWith('__')) continue
    const at: Record<string, number | string | boolean | null> = {}
    const points = plot.data ?? []
    // From the end: the new bars are what is asked for most.
    for (let index = points.length - 1; index >= 0; index -= 1) {
      const time = points[index].time
      if (typeof time !== 'number') continue
      if (time < earliest) break
      if (times.has(time) && !(String(time) in at)) at[String(time)] = plotValue(points[index].value)
    }
    values[plot.title ?? key] = at
  }
  return values
}

class RunnerError extends Error {}

export class Runner {
  private readonly rules = new Map<string, Loaded>()
  private readonly ask: SeriesAsk
  private readonly sha256: (text: string) => string
  private readonly versions: { pinets: string; node: string }

  constructor(ask: SeriesAsk, sha256: (text: string) => string, versions: { pinets: string; node: string }) {
    this.ask = ask
    this.sha256 = sha256
    this.versions = versions
  }

  /** The rules loaded, by id: for tests. */
  loaded(): string[] {
    return [...this.rules.keys()]
  }

  async handle(request: RunnerRequest): Promise<RunnerResponse> {
    let prepared: PreparedScript | null = null
    try {
      switch (request.op) {
        case 'hello':
          return { id: request.id, ok: true, op: 'hello', ...this.versions }
        case 'load': {
          this.rules.delete(request.rule)
          prepared = await this.prepare(request)
          return await this.load(request, prepared)
        }
        case 'append':
          prepared = this.rules.get(request.rule)?.prepared ?? null
          return { id: request.id, ok: true, op: 'append', ...(await this.append(request.rule, request.bars)) }
        case 'unload':
          this.rules.delete(request.rule)
          return { id: request.id, ok: true, op: 'unload' }
        default:
          throw new RunnerError(`Unknown request ${JSON.stringify((request as { op?: unknown }).op)}`)
      }
    } catch (error) {
      return { id: request.id, ok: false, error: this.failure(error, prepared) }
    }
  }

  private checkPin(pinned: PinnedSource, what: string): void {
    if (this.sha256(pinned.source) !== pinned.sha256) {
      throw new RunnerError(`The source of ${what} is not the version the rule pinned`)
    }
  }

  private async prepare(request: LoadRequest): Promise<PreparedScript> {
    this.checkPin(request.script, 'the script')
    for (const library of request.libraries) this.checkPin(library, `the library "${library.name}"`)
    const byName = new Map(request.libraries.map((library) => [library.name, library]))
    // Only the versions the rule pinned: a library it did not pin is missing, never fetched.
    const prepared = await prepareScript(request.script.source, async (name) => {
      const library = byName.get(name)
      return library ? { id: library.id, name: library.name, version: library.version, source: library.source } : null
    })
    if (prepared.kind === 'library') throw new RunnerError('A library does not run on its own')
    return prepared
  }

  private async load(request: LoadRequest, prepared: PreparedScript): Promise<RunnerResponse> {
    const indicator = new Indicator(prepared.source, request.inputs ?? {})
    // Compiled once: each run of the rule uses the same.
    indicator.prepare()
    const loaded: Loaded = {
      indicator,
      prepared,
      ticker: request.ticker,
      timeframe: request.timeframe,
      candles: this.checkedCandles(request.bars, null),
    }
    const result = await this.run(loaded, Number.NEGATIVE_INFINITY, new Set())
    this.rules.set(request.rule, loaded)
    return {
      id: request.id,
      ok: true,
      op: 'load',
      kind: prepared.kind,
      title: prepared.title,
      inputs: indicator.getInputsMeta(),
      warnings: prepared.warnings,
      ...result,
    }
  }

  private checkedCandles(rows: BarRow[], after: Candle | null): Candle[] {
    const candles = rows.map(candleOf)
    let previous = after
    for (const candle of candles) {
      if (![candle.openTime, candle.open, candle.high, candle.low, candle.close, candle.volume, candle.closeTime].every(Number.isFinite)) {
        throw new RunnerError(`A bar is not made of numbers (${candle.openTime})`)
      }
      if (previous !== null && candle.openTime <= previous.openTime) {
        throw new RunnerError(`Bars out of order: ${candle.openTime} after ${previous.openTime}`)
      }
      previous = candle
    }
    return candles
  }

  private async append(rule: string, rows: BarRow[]): Promise<RunResult> {
    const loaded = this.rules.get(rule)
    if (!loaded) throw new RunnerError(`No rule ${rule} is loaded`)
    const candles = this.checkedCandles(rows, loaded.candles.at(-1) ?? null)
    if (candles.length === 0) throw new RunnerError('No bar to add')
    if (loaded.candles.length + candles.length > MAX_RULE_BARS) {
      throw new RunnerError(`A rule holds ${MAX_RULE_BARS} bars at most: load it again from fewer`)
    }
    loaded.candles.push(...candles)
    return this.run(loaded, candles[0].openTime, new Set(candles.map((candle) => candle.openTime)))
  }

  /** A whole run; the alerts of the bars from ``since``, and the plots at their bars and at ``plotTimes``. */
  private async run(loaded: Loaded, since: number, plotTimes: Set<number>): Promise<RunResult> {
    const started = performance.now()
    const chart = { symbol: loaded.ticker, timeframe: loaded.timeframe, bars: loaded.candles }
    const pine = new PineTS(new SeriesProvider(chart, this.ask), loaded.ticker, loaded.timeframe, loaded.candles.length)
    pine.setAlertMode('all')
    const context = await pine.run(loaded.indicator)
    return this.result(loaded, context, since, plotTimes, started)
  }

  private result(loaded: Loaded, context: Context, since: number, plotTimes: Set<number>, started: number): RunResult {
    const seen = new Set<string>()
    const alerts: RunnerAlert[] = []
    for (const alert of context.alerts ?? []) {
      if (alert.time < since) continue
      const entry = { type: alert.type, id: alert.id, ...alertParts(alert), time: alert.time }
      // Once each: a call PineTS evaluates twice on a bar must not make two signals.
      const key = JSON.stringify([entry.time, entry.id, entry.title, entry.message])
      if (seen.has(key)) continue
      seen.add(key)
      alerts.push(entry)
    }
    alerts.sort((first, second) => first.time - second.time)
    const times = new Set([...plotTimes, ...alerts.map((alert) => alert.time)])
    return {
      bars: loaded.candles.length,
      milliseconds: Math.round(performance.now() - started),
      alerts,
      plots: plotValuesAt(context.plots as Record<string, PlotLike>, times),
    }
  }

  private failure(error: unknown, prepared: PreparedScript | null): RunnerFailure {
    if (error instanceof PrepareError) {
      return { message: error.message, file: error.file, line: error.line, column: error.column }
    }
    if (error instanceof RunnerError) return { message: error.message, file: null, line: null, column: null }
    const compiled = compileFailure(error)
    const origin = prepared && compiled.line !== null ? originOf(prepared, compiled.line) : null
    return {
      message: compiled.message,
      file: origin?.file ?? null,
      line: origin?.line ?? null,
      column: origin ? compiled.column : null,
    }
  }
}
