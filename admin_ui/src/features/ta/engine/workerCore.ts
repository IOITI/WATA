// What the Pine worker does, apart from being a worker: compile a source, run it
// on the bars handed over, and ask for the other series a script reads. Kept out
// of pine.worker.ts so that it can be tested without one.

import { Indicator, PineTS } from 'pinets'

import { compileFailure, runSummary } from './pineRun'
import { SeriesProvider } from './provider'
import type { Candle, WorkerRequest, WorkerResponse } from './workerProtocol'

/** Asks the page for the bars of another symbol or timeframe. */
export type SeriesAsker = (
  id: number,
  symbol: string,
  timeframe: string,
  window: { from?: number; to?: number; limit?: number },
) => Promise<Candle[]>

export async function answer(request: WorkerRequest, ask: SeriesAsker): Promise<WorkerResponse> {
  try {
    if (request.op === 'compile') {
      const indicator = new Indicator(request.source)
      indicator.prepare()
      return {
        id: request.id,
        ok: true,
        op: 'compile',
        declaration: indicator.getDeclarationType(),
        inputs: indicator.getInputsMeta(),
      }
    }
    const started = performance.now()
    const provider = new SeriesProvider(request, (symbol, timeframe, window) => ask(request.id, symbol, timeframe, window))
    const pine = new PineTS(provider, request.symbol, request.timeframe, request.bars.length)
    pine.setAlertMode('all')
    const context = await pine.run(new Indicator(request.source, request.inputs ?? {}))
    return { id: request.id, ok: true, op: 'run', ...runSummary(context, performance.now() - started) }
  } catch (error) {
    return { id: request.id, ok: false, error: compileFailure(error) }
  }
}
