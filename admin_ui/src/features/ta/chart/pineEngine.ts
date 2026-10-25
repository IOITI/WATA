// The Pine engine of the Charts page: Vela-pinets' worker engine, its scripts run
// on the bars that are over, as the signal engine runs a rule's.
//
// Left to itself, a live Vela chart runs its scripts in PineTS's live mode (one
// context kept, the last bars added to it), which in PineTS 0.11.1 raises none of
// the alerts of the bars it adds and leaves request.security a bar or two behind
// (engine/runnerCore.ts). Run on the bars that are over, a script draws on the
// chart what the editor's test and the signal engine compute from the same bars;
// the bar still growing shows its prices, and the script's values once it is over.
//
// A script is run again when a bar is over, not at each quote: a second apart at
// most. A bar of time is over once its last ten seconds have had the time to come;
// a range bar once the server no longer says it is in progress (WataBars). Its new
// bars continue its run in the worker (vite/velaContinuedRuns.ts) when nothing it
// reads makes it a whole run's (wholeBecause, as the signal engine decides). Each
// run says again what the run before said of the same bar: an alert of the last bar
// (PineTS raises those only), an error. An alert is passed on once, an error once
// until a run succeeds or is asked for by something else than a quote.

import type { ExecutionHandlers, ExecutionRequest, ExecutionSession, OHLCV, ScriptingEngine } from '@luxalgo/vela'

import { parseTicker } from '../engine/tickers'
import type { WataTicker } from '../engine/tickers'
import { barEnd, timeframeOf } from '../engine/timeframes'
import { codeOf, wholeBecause } from '../engine/wholeRuns'

/** The least time between two runs asked for by the bars of a live chart. */
export const RUN_EVERY_MS = 1000
/** How long after a bar of time ends its last ten seconds may still come from the stream. */
export const OVER_AFTER_MS = 10_000
/** Alerts remembered as passed on, the oldest forgotten first: the last bars' alerts. */
const ALERTS_KEPT = 200

export interface RunClock {
  now: () => number
  setTimer: (callback: () => void, ms: number) => unknown
  clearTimer: (timer: unknown) => void
}

/** The open time (epoch ms) of a range series' bar still in progress; null when none is. */
export type GrowingBar = (ticker: WataTicker) => number | null

const BROWSER_CLOCK: RunClock = {
  now: () => Date.now(),
  setTimer: (callback, ms) => setTimeout(callback, ms),
  clearTimer: (timer) => clearTimeout(timer as ReturnType<typeof setTimeout>),
}

const NONE_GROWING: GrowingBar = () => null

type Market = Pick<ExecutionRequest['market'], 'symbol' | 'timeframe'>

/**
 * The last bars that are not over yet, and when the first of them will be (epoch ms): null
 * for a range bar in progress, which a price ends and not a time. A bar of time is over once
 * its last ten seconds have had the time to come, the next one begun or not.
 */
export function stillGrowing(bars: OHLCV[], market: Market, growing: GrowingBar, now: number): { count: number; overAt: number | null } {
  const last = bars[bars.length - 1]
  if (!last) return { count: 0, overAt: 0 }
  const ticker = parseTicker(market.symbol)
  if (ticker?.rangeTicks) return growing(ticker) === last.time ? { count: 1, overAt: null } : { count: 0, overAt: 0 }
  const length = timeframeOf(market.timeframe)
  if (!length) return { count: 0, overAt: 0 }
  let count = 0
  let overAt = 0
  while (count < bars.length) {
    const at = barEnd(bars[bars.length - 1 - count].time / 1000, length.seconds) * 1000 + OVER_AFTER_MS
    if (at <= now) break
    overAt = at
    count += 1
  }
  return { count, overAt }
}

/** The bars that are over: all of them but those at the end that still grow. */
export function overBars(bars: OHLCV[], market: Market, growing: GrowingBar, now: number): OHLCV[] {
  const { count } = stillGrowing(bars, market, growing, now)
  return count === 0 ? bars : bars.slice(0, bars.length - count)
}

/**
 * Whether a prepared script's new bars may continue its run in the worker: said on its token, which the
 * worker gets with the script (`wataContinue`, read by vite/velaContinuedRuns.ts).
 */
export function markContinuable(prepared: ExecutionRequest['prepared']): void {
  const token = prepared?.token as { source?: unknown; wataContinue?: boolean } | null | undefined
  if (!token || typeof token.source !== 'string') return
  const kind = /^\s*strategy\s*\(/m.test(codeOf(token.source)) ? 'strategy' : 'indicator'
  token.wataContinue = wholeBecause({ source: token.source, kind }) === null
}

/** What a run is given, said short: the number of bars and the last of them. */
function signature(bars: OHLCV[]): string {
  const last = bars[bars.length - 1]
  return last ? `${bars.length}|${last.time}|${last.open}|${last.high}|${last.low}|${last.close}|${last.volume ?? ''}` : '0'
}

/** `engine`, asked by Vela for whole runs only, on the bars that are over, a second apart at most on a live chart. */
export function fullRunEngine(engine: ScriptingEngine, clock: RunClock = BROWSER_CLOCK, growing: GrowingBar = NONE_GROWING): ScriptingEngine {
  return {
    language: engine.language,
    // An engine that cannot stream is run in full by Vela, on a live chart too.
    capabilities: { ...engine.capabilities, streaming: false },
    prepare: (source, instanceId) => engine.prepare(source, instanceId),
    execute: (request, handlers) => pacedSession(engine, request, handlers, clock, growing),
  }
}

function pacedSession(
  engine: ScriptingEngine,
  request: ExecutionRequest,
  handlers: ExecutionHandlers,
  clock: RunClock,
  growing: GrowingBar,
): ExecutionSession {
  const allBars = request.getBars ?? (() => request.bars)
  const over = () => overBars(allBars(), request.market, growing, clock.now())
  markContinuable(request.prepared)
  const alerted = new Set<string>()
  let failing = false
  const session = engine.execute({ ...request, bars: over(), getBars: over }, {
    onModel: (model) => {
      failing = false
      handlers.onModel(model)
    },
    onAlert: (alert) => {
      const key = JSON.stringify([alert.time, alert.id, alert.title ?? null, alert.message])
      if (alerted.has(key)) return
      alerted.add(key)
      if (alerted.size > ALERTS_KEPT) alerted.delete(alerted.values().next().value as string)
      handlers.onAlert?.(alert)
    },
    onWarning: (warning) => handlers.onWarning?.(warning),
    onError: (error) => {
      if (failing) return
      failing = true
      handlers.onError?.(error)
    },
    onDone: () => handlers.onDone?.(),
  })

  // What the last run was given.
  let given = signature(over())
  let lastRun = Number.NEGATIVE_INFINITY
  let due: unknown = null
  // A bar of time ends with no quote after it (a closed market): looked at again when it is over.
  let looked: unknown = null
  const run = () => {
    due = null
    lastRun = clock.now()
    given = signature(over())
    session.notifyBars()
  }
  // Vela waits for how a run it asked for ended (new inputs, the history loaded): it is told.
  const asked = () => {
    failing = false
  }
  const lookAgain = () => {
    if (looked !== null) return
    const { count, overAt: at } = stillGrowing(allBars(), request.market, growing, clock.now())
    if (count === 0 || at === null) return
    looked = clock.setTimer(() => {
      looked = null
      tick()
    }, at - clock.now())
  }
  const tick = () => {
    if (due !== null) return
    if (signature(over()) === given) {
      lookAgain()
      return
    }
    const wait = lastRun + RUN_EVERY_MS - clock.now()
    if (wait <= 0) run()
    else due = clock.setTimer(run, wait)
  }

  return {
    getContext: session.getContext?.bind(session),
    stop: () => {
      if (due !== null) clock.clearTimer(due)
      if (looked !== null) clock.clearTimer(looked)
      due = looked = null
      session.stop()
    },
    update: (inputs, props) => {
      asked()
      session.update(inputs, props)
    },
    setVisibleRange: (range) => {
      asked()
      session.setVisibleRange(range)
    },
    notifyBars: (reason) => {
      // The history still loading, or loaded: as Vela says it.
      if (reason !== undefined) {
        asked()
        given = signature(over())
        session.notifyBars(reason)
        return
      }
      tick()
    },
  }
}
