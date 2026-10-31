// The run's own layer on a backtest day's Vela chart: a scripting engine of WATA's (Vela's engines are
// pluggable by language) whose "script" is the layer runDayModel.ts made, as JSON, and whose model is
// those labels and trade markers. No worker, nothing computed: the run said where its signals and
// trades were. A row of Vela's legend of its own, so that it can be hidden apart from the script.

import type { ExecutionHandlers, ExecutionRequest, ExecutionSession, IndicatorModel, ScriptingEngine } from '@luxalgo/vela'

import type { RunLayer } from './runDayModel'

export const RUN_LANGUAGE = 'wata-run'

interface Token {
  layer: RunLayer
  instanceId: string
}

export function runModel(layer: RunLayer, instanceId: string): IndicatorModel {
  return {
    id: instanceId,
    title: layer.title,
    shorttitle: layer.short ?? 'The run',
    overlay: true,
    paneHint: 'price',
    series: [],
    fills: [],
    backgrounds: layer.backgrounds,
    priceLines: [],
    labels: layer.labels,
    trades: layer.trades,
    inputs: [],
    inputValues: {},
  }
}

function session(request: ExecutionRequest, handlers: ExecutionHandlers): ExecutionSession {
  const { layer, instanceId } = request.prepared.token as Token
  let stopped = false
  const hand = () => {
    if (stopped) return
    handlers.onModel(runModel(layer, instanceId))
    handlers.onDone?.()
  }
  // As an engine answers: once the chart has its session.
  queueMicrotask(hand)
  return {
    stop: () => {
      stopped = true
    },
    // Nothing to set: the layer is said again, as Vela waits for how a run it asked for ended.
    update: () => queueMicrotask(hand),
    setVisibleRange: () => undefined,
    notifyBars: (reason) => {
      if (reason !== undefined) queueMicrotask(hand)
    },
  }
}

export function runMarksEngine(): ScriptingEngine {
  return {
    language: RUN_LANGUAGE,
    capabilities: { streaming: false, visibleRange: false, inputs: false },
    prepare: async (source, instanceId) => {
      const layer = JSON.parse(source) as RunLayer
      return {
        language: RUN_LANGUAGE,
        inputs: [],
        meta: { title: layer.title, shorttitle: 'The run', overlay: true },
        reactsToViewport: false,
        token: { layer, instanceId } satisfies Token,
      }
    },
    execute: session,
  }
}
