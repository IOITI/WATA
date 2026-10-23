// What the TA engine (src/ta_engine) asks of PineTS on the server, through
// runner.ts: a signal rule's script, prepared as the pages prepare it, run on the
// closed bars the engine sends.
//
// A rule's history is a whole run over its bars, from the first one the engine
// sent. Each new bar is then evaluated by a whole run over all of them, or, when
// the engine asks it (`continued`), by continuing the run where it stopped: the
// same PineTS context is given the new bars and executes them as it executed the
// others, bar after bar (a whole run itself executes its last bar in a second
// call on the same context). Either way a bar's alerts are those of a whole run
// over the same bars, which is also what a replay gives (src/ta_engine/replay.py),
// as long as the script reads nothing a whole run gives otherwise at an earlier
// bar: where the bars end (barstate.islast and the others but isfirst,
// last_bar_index, session.islastbar), the clock (timenow, math.random), other
// series (request.*), the chart's visible range, a strategy's orders. Such a
// script runs whole at each new bar (wholeBecause).
//
// The engine has a continued run checked now and then (`check`): a whole run over
// the same bars, compared alert by alert and plot by plot over the bars since the
// last check, whose context the run then goes on from. A difference makes the
// rule run whole until it is loaded again. A held run keeps its memory: past
// HELD_HEAP_SHARE of the heap after a load or a check, the largest goes back to
// whole runs.
//
// PineTS's live mode (updateTail) is not used: it runs its last bar again as one
// not yet closed, after putting back what it can of the state before it. In
// PineTS 0.11.1 it raised none of the alerts of the bars it added to the user's
// script, and leaves the series of request.security a bar or two behind. The
// engine's bars are closed: nothing is run twice.
//
// A study of a script (src/research/study.py, Claude's pine_run) asks a load in
// detail: every bar's plot values and PineTS's runtime warnings come back too.
// Without it the answer is what the engine has always had.

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
  /** Every bar's plot values and the runtime warnings as well (a study); never asked by the engine. */
  detail?: boolean
  /** Each new bar continues the run where it stopped, when the script allows it: the engine's, never a replay's. */
  continued?: boolean
}

export type RunnerRequest =
  | { id: number; op: 'hello' }
  | LoadRequest
  | { id: number; op: 'append'; rule: string; bars: BarRow[] }
  | { id: number; op: 'check'; rule: string }
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

/** A runtime warning of PineTS, once per message: the bar it was first raised at, and how often. */
export interface RuntimeWarning {
  message: string
  bar: number | null
  count: number
}

/** What a load in detail adds: each plot's value at every bar (null where it has none), by its title. */
export interface DetailResult {
  series: { times: number[]; plots: Record<string, (number | string | boolean | null)[]> }
  runtimeWarnings: RuntimeWarning[]
}

/** The distinct runtime warnings a load in detail gives at most. */
export const MAX_RUNTIME_WARNINGS = 50

/** How a rule the engine asked continued runs of is run: what every answer about it says. */
export interface RunMode {
  /** Its new bars continue its run. */
  continued: boolean
  /** Why each new bar runs it whole instead; null while its run continues. */
  whole: string | null
}

/** A rule's continued run against a whole run over the same bars. */
export interface CheckResult extends RunMode {
  /** The bars the rule holds. */
  bars: number
  /** The bars compared: those since the last check, none when there is nothing to compare. */
  compared: number
  /** The open time of the first bar compared, epoch ms. */
  from: number | null
  /** Whether the two agreed; null when nothing was compared. */
  same: boolean | null
  /** What differed, the first bars first, MAX_DIFFERENCES at most. */
  differences: string[]
  /** The whole run's time. */
  milliseconds: number
}

/** The differences a check says at most. */
export const MAX_DIFFERENCES = 5
/**
 * The share of its heap a runner holding runs may be at after a load or a check (its memory counted after
 * a collection): a whole run beside them, a check's, needs about as much again as the largest of them.
 */
export const HELD_HEAP_SHARE = 0.5

/** The heap of the runner's process after a collection, and the most its old space may hold, in bytes. */
export type HeapMeasure = () => { used: number; limit: number } | null

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
    } & RunResult & Partial<DetailResult> & Partial<RunMode>)
  | ({ id: number; ok: true; op: 'append' } & RunResult & Partial<RunMode>)
  | ({ id: number; ok: true; op: 'check' } & CheckResult)
  | { id: number; ok: true; op: 'unload' }
  | { id: number; ok: false; error: RunnerFailure }

/** A run its rule's new bars continue: the PineTS that made it, and its context. */
interface Held {
  pine: PineTS
  context: Context
}

interface Loaded {
  indicator: Indicator
  /** The source and inputs the indicator was compiled from. */
  compiled: string
  prepared: PreparedScript
  ticker: string
  timeframe: string
  candles: Candle[]
  /** The engine asked its new bars to continue its run. */
  continued: boolean
  /** The run they continue; none when each new bar runs the script whole. */
  held: Held | null
  /** Why each new bar runs it whole, the engine having asked otherwise. */
  whole: string | null
  /** The bars a whole run agreed with: a check compares those after. */
  checked: number
}

/**
 * What of PineTS a continued run uses, none of it public (PineTS.class.ts, 0.12.0): the bars it holds and how
 * it adds one (as its live mode adds them), the script it compiled, and the loop of its runs, which a whole
 * run calls twice on one context, its last bar apart.
 */
interface PineInternals {
  data: unknown[]
  _transpiledCode: unknown
  _appendCandle(candle: Candle): void
  _executeIterations(context: Context, code: unknown, from: number, to: number): Promise<void>
}

function internalsOf(pine: PineTS): PineInternals | null {
  const found = pine as unknown as Partial<PineInternals>
  const usable = Array.isArray(found.data) && typeof found._transpiledCode === 'function'
    && typeof found._appendCandle === 'function' && typeof found._executeIterations === 'function'
  return usable ? (found as PineInternals) : null
}

/** Why a whole run is needed when the PineTS of this runner has no loop to continue a run with. */
export const NO_WAY_TO_CONTINUE = 'this PineTS has no way to continue a run'

/** What a script may read that a whole run gives otherwise at an earlier bar, by what its code names. */
const WHOLE_ONLY: RegExp[] = [
  // Where the bars end: barstate.islast, isconfirmed, isrealtime... All but isfirst.
  /\bbarstate\s*\.\s*(?!isfirst\b)[A-Za-z_]\w*/,
  /\blast_bar_(?:index|time)\b/,
  /\bsession\s*\.\s*islastbar(?:_regular)?\b/,
  // The clock, and chance.
  /\btimenow\b/,
  /\bmath\s*\.\s*random\b/,
  // Other series: a run asks them over its own bars.
  /\brequest\s*\.\s*[A-Za-z_]\w*/,
  /\bchart\s*\.\s*(?:left|right)_visible_bar_time\b/,
]

/** A Pine source without its comments, and its strings emptied: the names its code uses. */
export function codeOf(source: string): string {
  let code = ''
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index]
    if (char === '/' && source[index + 1] === '/') {
      const end = source.indexOf('\n', index)
      if (end < 0) break
      index = end - 1
      continue
    }
    if (char === '"' || char === "'") {
      let end = index + 1
      while (end < source.length && source[end] !== char && source[end] !== '\n') end += source[end] === '\\' ? 2 : 1
      code += char + char
      index = end
      continue
    }
    code += char
  }
  return code
}

/** Why each new bar must run a prepared script whole (its libraries inlined in it); null when its run can continue. */
export function wholeBecause(prepared: Pick<PreparedScript, 'source' | 'kind'>): string | null {
  if (prepared.kind === 'strategy') return 'it is a strategy(), whose orders a whole run settles'
  const code = codeOf(prepared.source)
  for (const pattern of WHOLE_ONLY) {
    const found = pattern.exec(code)
    if (found) return `its script reads ${found[0].replace(/\s+/g, '')}`
  }
  return null
}

function moment(time: number): string {
  return new Date(time).toISOString().replace('.000Z', 'Z')
}

function shown(value: unknown): string {
  return typeof value === 'string' ? JSON.stringify(value) : String(value)
}

/**
 * What differs between a continued run's answer and a whole run's over the same bars (alerts by their bar,
 * call, title and message; plots, keyed as PineTS keys them, at the bars asked): a few, the first bars first.
 */
export function differencesOf(
  continued: { alerts: RunnerAlert[]; plots: PlotValues },
  whole: { alerts: RunnerAlert[]; plots: PlotValues },
  most = MAX_DIFFERENCES,
): string[] {
  const found: [number, string][] = []
  const key = (alert: RunnerAlert) => JSON.stringify([alert.time, alert.id, alert.title, alert.message])
  const named = (alert: RunnerAlert) => `${alert.type} ${JSON.stringify(alert.title ?? alert.id)} at ${moment(alert.time)}`
  const ours = new Set(continued.alerts.map(key))
  const theirs = new Set(whole.alerts.map(key))
  for (const alert of whole.alerts) if (!ours.has(key(alert))) found.push([alert.time, `${named(alert)} only in the whole run`])
  for (const alert of continued.alerts) if (!theirs.has(key(alert))) found.push([alert.time, `${named(alert)} only in the continued run`])
  for (const plot of new Set([...Object.keys(continued.plots), ...Object.keys(whole.plots)])) {
    const mine = continued.plots[plot] ?? {}
    const other = whole.plots[plot] ?? {}
    for (const time of new Set([...Object.keys(mine), ...Object.keys(other)])) {
      const [a, b] = [mine[time], other[time]]
      if (a !== b) found.push([Number(time), `plot ${JSON.stringify(plot)} at ${moment(Number(time))}: ${shown(a)} continued, ${shown(b)} whole`])
    }
  }
  return found.sort((first, second) => first[0] - second[0]).slice(0, most).map(([, said]) => said)
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

interface WarningLike {
  message: string
  bar?: number
}

/** Each plot at every candle, and the runtime warnings once each: what a load in detail adds. */
export function detailOf(
  candles: Candle[],
  plots: Record<string, PlotLike> | undefined,
  warnings: WarningLike[] | undefined,
): DetailResult {
  const times = candles.map((candle) => candle.openTime)
  const at = new Map(times.map((time, index) => [time, index]))
  const series: DetailResult['series']['plots'] = {}
  for (const [key, plot] of Object.entries(plots ?? {})) {
    if (key.startsWith('__')) continue
    const values: (number | string | boolean | null)[] = new Array(times.length).fill(null)
    for (const point of plot.data ?? []) {
      const index = typeof point.time === 'number' ? at.get(point.time) : undefined
      if (index !== undefined) values[index] = plotValue(point.value)
    }
    series[plot.title ?? key] = values
  }
  const seen = new Map<string, RuntimeWarning>()
  for (const warning of warnings ?? []) {
    const known = seen.get(warning.message)
    if (known) known.count += 1
    else if (seen.size < MAX_RUNTIME_WARNINGS) seen.set(warning.message, { message: warning.message, bar: warning.bar ?? null, count: 1 })
  }
  return { series: { times, plots: series }, runtimeWarnings: [...seen.values()] }
}

/**
 * Each plot's value at ``times``, by its title (``keyed``: by PineTS's key, which two plots of one title do
 * not share). PineTS's own drawings (__labels__, ...) are left out.
 */
export function plotValuesAt(plots: Record<string, PlotLike> | undefined, times: Set<number>, keyed = false): PlotValues {
  const values: PlotValues = {}
  if (times.size === 0) return values
  let earliest = Number.POSITIVE_INFINITY
  for (const time of times) earliest = Math.min(earliest, time)
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
    values[keyed ? key : (plot.title ?? key)] = at
  }
  return values
}

class RunnerError extends Error {}

export class Runner {
  private readonly rules = new Map<string, Loaded>()
  private readonly ask: SeriesAsk
  private readonly sha256: (text: string) => string
  private readonly versions: { pinets: string; node: string }
  private readonly heap: HeapMeasure

  constructor(
    ask: SeriesAsk,
    sha256: (text: string) => string,
    versions: { pinets: string; node: string },
    heap: HeapMeasure = () => null,
  ) {
    this.ask = ask
    this.sha256 = sha256
    this.versions = versions
    this.heap = heap
  }

  /** The rules loaded, by id: for tests. */
  loaded(): string[] {
    return [...this.rules.keys()]
  }

  /** The rules whose new bars continue their run, by id: for tests. */
  continuing(): string[] {
    return [...this.rules].filter(([, loaded]) => loaded.held !== null).map(([rule]) => rule)
  }

  async handle(request: RunnerRequest): Promise<RunnerResponse> {
    let prepared: PreparedScript | null = null
    try {
      switch (request.op) {
        case 'hello':
          return { id: request.id, ok: true, op: 'hello', ...this.versions }
        case 'load': {
          const previous = this.rules.get(request.rule)
          this.rules.delete(request.rule)
          prepared = await this.prepare(request)
          return await this.load(request, prepared, previous)
        }
        case 'append':
          prepared = this.rules.get(request.rule)?.prepared ?? null
          return { id: request.id, ok: true, op: 'append', ...(await this.append(request.rule, request.bars)) }
        case 'check':
          prepared = this.rules.get(request.rule)?.prepared ?? null
          return { id: request.id, ok: true, op: 'check', ...(await this.check(request.rule)) }
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

  private async load(request: LoadRequest, prepared: PreparedScript, previous?: Loaded): Promise<RunnerResponse> {
    const inputs = request.inputs ?? {}
    const compiled = JSON.stringify([prepared.source, inputs])
    // Compiled once: each run of the rule uses the same, and so does the rule loaded again with the same
    // script and inputs (each day of a replay, the engine's daily restart). Each compile leaves native
    // memory behind that the process never gets back, about 60 MB for the Kernel VWAP script: a backtest
    // that compiled it every engine day was killed for memory after 10 to 20 days, each day slower.
    let indicator = previous?.compiled === compiled ? previous.indicator : null
    if (indicator === null) {
      indicator = new Indicator(prepared.source, inputs)
      indicator.prepare()
    }
    // A study never adds a bar: what it loads is never held.
    const continued = request.continued === true && request.detail !== true
    const loaded: Loaded = {
      indicator,
      compiled,
      prepared,
      ticker: request.ticker,
      timeframe: request.timeframe,
      candles: this.checkedCandles(request.bars, null),
      continued,
      held: null,
      whole: continued ? wholeBecause(prepared) : null,
      checked: 0,
    }
    const result = await this.run(loaded, Number.NEGATIVE_INFINITY, new Set(), request.detail === true, continued && loaded.whole === null)
    loaded.checked = loaded.candles.length
    this.rules.set(request.rule, loaded)
    if (loaded.held) this.keepWithinHeap()
    return {
      id: request.id,
      ok: true,
      op: 'load',
      kind: prepared.kind,
      title: prepared.title,
      inputs: indicator.getInputsMeta(),
      warnings: prepared.warnings,
      ...result,
      ...(continued ? this.mode(loaded) : {}),
    }
  }

  private mode(loaded: Loaded): RunMode {
    return loaded.held ? { continued: true, whole: null } : { continued: false, whole: loaded.whole }
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

  private async append(rule: string, rows: BarRow[]): Promise<RunResult & Partial<RunMode>> {
    const loaded = this.rules.get(rule)
    if (!loaded) throw new RunnerError(`No rule ${rule} is loaded`)
    const candles = this.checkedCandles(rows, loaded.candles.at(-1) ?? null)
    if (candles.length === 0) throw new RunnerError('No bar to add')
    if (loaded.candles.length + candles.length > MAX_RULE_BARS) {
      throw new RunnerError(`A rule holds ${MAX_RULE_BARS} bars at most: load it again from fewer`)
    }
    const times = new Set(candles.map((candle) => candle.openTime))
    if (loaded.held) {
      try {
        return { ...(await this.continueRun(loaded, loaded.held, candles, times)), ...this.mode(loaded) }
      } catch (error) {
        // Some of its bars may have run: the engine loads the rule again from its history.
        loaded.held = null
        throw error
      }
    }
    loaded.candles.push(...candles)
    const result = await this.run(loaded, candles[0].openTime, times)
    return loaded.continued ? { ...result, ...this.mode(loaded) } : result
  }

  /** A whole run of the rule over the bars it holds: a new PineTS, and the context it ran the script in. */
  private async wholeRun(loaded: Loaded): Promise<Held> {
    const chart = { symbol: loaded.ticker, timeframe: loaded.timeframe, bars: loaded.candles }
    const pine = new PineTS(new SeriesProvider(chart, this.ask), loaded.ticker, loaded.timeframe, loaded.candles.length)
    pine.setAlertMode('all')
    return { pine, context: await pine.run(loaded.indicator) }
  }

  /**
   * A whole run; the alerts of the bars from ``since``, and the plots at their bars and at ``plotTimes``;
   * in ``detail``, every bar's plot values and the runtime warnings too. ``hold``: its new bars continue it.
   */
  private async run(
    loaded: Loaded, since: number, plotTimes: Set<number>, detail = false, hold = false,
  ): Promise<RunResult & Partial<DetailResult>> {
    const started = performance.now()
    const { pine, context } = await this.wholeRun(loaded)
    if (hold) {
      if (internalsOf(pine)) loaded.held = { pine, context }
      else loaded.whole = NO_WAY_TO_CONTINUE
    }
    const result = this.result(loaded, context, since, plotTimes, started)
    if (!detail) return result
    return { ...result, ...detailOf(loaded.candles, context.plots as Record<string, PlotLike>, context.warnings as WarningLike[]) }
  }

  /** The new bars run on the held run's context, after the bars it ran, as a whole run runs its last one. */
  private async continueRun(loaded: Loaded, held: Held, candles: Candle[], times: Set<number>): Promise<RunResult> {
    const started = performance.now()
    const pine = internalsOf(held.pine) as PineInternals
    const context = held.context as Context & { marketData?: unknown }
    const from = loaded.candles.length
    for (const candle of candles) {
      // The provider hands PineTS the very bars the rule holds (provider.ts): one push, PineTS's own.
      if (pine.data !== loaded.candles) loaded.candles.push(candle)
      const market = context.marketData
      if (Array.isArray(market) && market !== pine.data && market !== loaded.candles) market.push(candle)
      pine._appendCandle(candle)
    }
    context.length = loaded.candles.length
    await pine._executeIterations(context, pine._transpiledCode, from, loaded.candles.length)
    return this.result(loaded, context, candles[0].openTime, times, started)
  }

  /**
   * The held run against a whole run over the same bars, at the bars since the last check: the same, the
   * whole run's context is the one the next bars continue; different, the rule runs whole from now on.
   */
  private async check(rule: string): Promise<CheckResult> {
    const loaded = this.rules.get(rule)
    if (!loaded) throw new RunnerError(`No rule ${rule} is loaded`)
    const held = loaded.held
    if (!held || loaded.checked >= loaded.candles.length) {
      return { bars: loaded.candles.length, compared: 0, from: null, same: null, differences: [], milliseconds: 0, ...this.mode(loaded) }
    }
    const started = performance.now()
    let fresh: Held
    try {
      fresh = await this.wholeRun(loaded)
    } catch (error) {
      // Its continued run went on where a whole run fails: it runs whole, which says so at its next bar.
      loaded.held = null
      loaded.whole = 'a whole run of it failed where its continued run went on'
      throw error
    }
    const milliseconds = Math.round(performance.now() - started)
    const since = loaded.candles[loaded.checked].openTime
    const times = new Set(loaded.candles.slice(loaded.checked).map((candle) => candle.openTime))
    const answer = (context: Context) => ({
      alerts: this.alertsFrom(context, since),
      plots: plotValuesAt(context.plots as Record<string, PlotLike>, times, true),
    })
    const differences = differencesOf(answer(held.context), answer(fresh.context))
    if (differences.length === 0) {
      loaded.held = fresh
      loaded.checked = loaded.candles.length
    } else {
      loaded.held = null
      loaded.whole = `a whole run differed from its continued run: ${differences[0]}`
    }
    this.keepWithinHeap()
    return {
      bars: loaded.candles.length, compared: times.size, from: since, same: differences.length === 0, differences, milliseconds,
      ...this.mode(loaded),
    }
  }

  /** Past HELD_HEAP_SHARE of the heap, the held run of the most bars goes back to whole runs: one at a time. */
  private keepWithinHeap(): void {
    const heap = this.heap()
    if (heap === null || heap.used <= HELD_HEAP_SHARE * heap.limit) return
    let largest: Loaded | null = null
    for (const loaded of this.rules.values()) {
      if (loaded.held && (largest === null || loaded.candles.length > largest.candles.length)) largest = loaded
    }
    if (largest === null) return
    largest.held = null
    largest.whole = `the runner's heap was ${Math.round((100 * heap.used) / heap.limit)} % full, `
      + `past the ${Math.round(100 * HELD_HEAP_SHARE)} % it may be at holding runs`
  }

  /** The alerts of the bars from ``since``, once each, in the order of their bars. */
  private alertsFrom(context: Context, since: number): RunnerAlert[] {
    const seen = new Set<string>()
    const found: RunnerAlert[] = []
    const raised = context.alerts ?? []
    // From the end: PineTS adds a bar's alerts as it runs it, so those from ``since`` are the last ones.
    for (let index = raised.length - 1; index >= 0; index -= 1) {
      const alert = raised[index]
      if (alert.time < since) break
      const entry = { type: alert.type, id: alert.id, ...alertParts(alert), time: alert.time }
      // Once each: a call PineTS evaluates twice on a bar must not make two signals.
      const key = JSON.stringify([entry.time, entry.id, entry.title, entry.message])
      if (seen.has(key)) continue
      seen.add(key)
      found.push(entry)
    }
    return found.reverse().sort((first, second) => first.time - second.time)
  }

  private result(loaded: Loaded, context: Context, since: number, plotTimes: Set<number>, started: number): RunResult {
    const alerts = this.alertsFrom(context, since)
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
