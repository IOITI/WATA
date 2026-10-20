// The Pine worker of the Technical Analysis pages: compiles a prepared script for
// the editor, and runs it on bars the page hands over (the editor's test).
//
// It is built as its own file (vite.config.ts, `worker`), which the admin serves
// with the only policy that lets PineTS evaluate the code it generates, and that
// policy lets it open no connection: what a script reads of other symbols is asked
// of the page (workerProtocol.ts).

import { answer } from './workerCore'
import type { Candle, FromWorker, ToWorker } from './workerProtocol'

interface WorkerScope {
  postMessage(message: FromWorker): void
  addEventListener(type: 'message', listener: (event: MessageEvent<ToWorker>) => void): void
}

const scope = self as unknown as WorkerScope
const waiting = new Map<number, { resolve: (bars: Candle[]) => void; reject: (error: Error) => void }>()
let nextFetch = 1

function ask(id: number, symbol: string, timeframe: string, window: { from?: number; to?: number; limit?: number }) {
  const fetchId = nextFetch
  nextFetch += 1
  return new Promise<Candle[]>((resolve, reject) => {
    waiting.set(fetchId, { resolve, reject })
    scope.postMessage({ op: 'fetch', fetchId, id, symbol, timeframe, ...window })
  })
}

scope.addEventListener('message', (event) => {
  const message = event.data
  if (message.op === 'series') {
    const pending = waiting.get(message.fetchId)
    waiting.delete(message.fetchId)
    if (!pending) return
    if (message.error !== undefined) pending.reject(new Error(message.error))
    else pending.resolve(message.bars ?? [])
    return
  }
  answer(message, ask).then((response) => scope.postMessage(response))
})
