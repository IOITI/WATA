// The Pine engine of the Charts page: Vela-pinets' worker engine, its scripts run
// in full again whenever the bars of a live chart change.
//
// Left to itself, a live Vela chart runs its scripts in PineTS's live mode (one
// context kept, the last bars added to it), which in PineTS 0.11.1 raises none of
// the alerts of the bars it adds and leaves request.security a bar or two behind
// (engine/runnerCore.ts). Run in full, a script draws on the chart what the
// editor's test and the signal engine compute from the same bars.
//
// A chart moves at each quote: its scripts are run again a second apart at most,
// the last quote always included. Each run says again what the run before said of
// the same bar: an alert of the last bar (PineTS raises those only), an error. An
// alert is passed on once, an error once until a run succeeds or is asked for by
// something else than a quote.

import type { ExecutionHandlers, ExecutionRequest, ExecutionSession, ScriptingEngine } from '@luxalgo/vela'

/** The least time between two runs asked for by the quotes of a live chart. */
export const RUN_EVERY_MS = 1000
/** Alerts remembered as passed on, the oldest forgotten first: the last bars' alerts. */
const ALERTS_KEPT = 200

export interface RunClock {
  now: () => number
  setTimer: (callback: () => void, ms: number) => unknown
  clearTimer: (timer: unknown) => void
}

const BROWSER_CLOCK: RunClock = {
  now: () => Date.now(),
  setTimer: (callback, ms) => setTimeout(callback, ms),
  clearTimer: (timer) => clearTimeout(timer as ReturnType<typeof setTimeout>),
}

/** `engine`, asked by Vela for whole runs only, a second apart at most on a live chart. */
export function fullRunEngine(engine: ScriptingEngine, clock: RunClock = BROWSER_CLOCK): ScriptingEngine {
  return {
    language: engine.language,
    // An engine that cannot stream is run in full by Vela, on a live chart too.
    capabilities: { ...engine.capabilities, streaming: false },
    prepare: (source, instanceId) => engine.prepare(source, instanceId),
    execute: (request, handlers) => pacedSession(engine, request, handlers, clock),
  }
}

function pacedSession(
  engine: ScriptingEngine,
  request: ExecutionRequest,
  handlers: ExecutionHandlers,
  clock: RunClock,
): ExecutionSession {
  const alerted = new Set<string>()
  let failing = false
  const session = engine.execute(request, {
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

  let lastRun = Number.NEGATIVE_INFINITY
  let due: unknown = null
  const run = () => {
    due = null
    lastRun = clock.now()
    session.notifyBars()
  }
  // Vela waits for how a run it asked for ended (new inputs, the history loaded): it is told.
  const asked = () => {
    failing = false
  }

  return {
    getContext: session.getContext?.bind(session),
    stop: () => {
      if (due !== null) clock.clearTimer(due)
      due = null
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
        session.notifyBars(reason)
        return
      }
      if (due !== null) return
      const wait = lastRun + RUN_EVERY_MS - clock.now()
      if (wait <= 0) run()
      else due = clock.setTimer(run, wait)
    },
  }
}
