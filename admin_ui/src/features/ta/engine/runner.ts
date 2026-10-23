/// <reference types="node" />
// The TA engine's Pine runner: one Node process that src/ta_engine/runner.py
// starts as `node --permission --allow-fs-read=<its folder> runner.mjs`, with no
// environment. It reads no file but its own, starts no process or worker, and
// holds no secret. It speaks with the engine in lines of JSON:
//
//   engine → runner   {"id": 1, "op": "load", ...}     a request (runnerCore.ts)
//   runner → engine   {"id": 1, "ok": true, ...}       its answer, in the order asked
//   runner → engine   {"ask": 7, "ticker": ...}        a series a script reads (request.security)
//   engine → runner   {"answer": 7, "bars": [...]}     the engine's answer to it
//
// Built into src/ta_engine/runner/runner.mjs by `npm run build:runner`.

import { createHash } from 'node:crypto'
import { registerHooks } from 'node:module'
import { createInterface } from 'node:readline'
import { getHeapStatistics, setFlagsFromString } from 'node:v8'
import { runInNewContext } from 'node:vm'

import type { SeriesAsk } from './provider'
import { candleOf, Runner } from './runnerCore'
import type { BarRow, HeapMeasure, RunnerRequest } from './runnerCore'

declare const __PINETS_VERSION__: string

// What the held runs keep is measured after a collection, the runner's own (in a context
// of its own: not a global the scripts see), against the old space the engine gave it.
setFlagsFromString('--expose-gc')
const collect = runInNewContext('gc') as () => void
const oldSpace = /^--max-old-space-size=(\d+)$/
const oldSpaceMb = Number(process.execArgv.map((flag) => oldSpace.exec(flag)?.[1]).find(Boolean) ?? Number.NaN)
const heap: HeapMeasure = () => {
  collect()
  const statistics = getHeapStatistics()
  const limit = Number.isFinite(oldSpaceMb) ? oldSpaceMb * 1024 * 1024 : statistics.heap_size_limit
  return { used: statistics.used_heap_size, limit }
}

// The scripts run here are PineTS's compile of Pine: nothing in them has to reach
// the network, Node's modules or another process. Node's permissions keep files,
// processes and workers out of reach, not the network: what reaches it is taken
// away before any script runs. Every import is refused from here on (the
// runner's own are done), and so is signalling another process: the worst a
// script can do is end this one, which the engine starts again.
registerHooks({
  resolve(specifier) {
    throw new Error(`The Pine runner imports nothing: ${specifier}`)
  },
})
for (const name of ['fetch', 'WebSocket', 'EventSource', 'XMLHttpRequest']) {
  Reflect.deleteProperty(globalThis, name)
}
for (const name of [
  'getBuiltinModule', 'binding', '_linkedBinding', 'dlopen', 'kill', 'chdir',
  'setuid', 'setgid', 'seteuid', 'setegid', 'setgroups', 'initgroups',
]) {
  Object.defineProperty(process, name, { value: undefined, configurable: false, writable: false })
}

const write = process.stdout.write.bind(process.stdout)
const send = (message: unknown) => {
  write(`${JSON.stringify(message)}\n`)
}

let lastAsk = 0
const asked = new Map<number, { resolve: (bars: BarRow[]) => void; reject: (error: Error) => void }>()

const ask: SeriesAsk = (symbol, timeframe, window) =>
  new Promise<BarRow[]>((resolve, reject) => {
    lastAsk += 1
    asked.set(lastAsk, { resolve, reject })
    send({
      ask: lastAsk,
      ticker: symbol,
      timeframe,
      from: window.from ?? null,
      to: window.to ?? null,
      limit: window.limit ?? null,
    })
  }).then((rows) => rows.map(candleOf))

const runner = new Runner(
  ask,
  (text) => createHash('sha256').update(text, 'utf8').digest('hex'),
  { pinets: __PINETS_VERSION__, node: process.version },
  heap,
)

const waiting: RunnerRequest[] = []
let busy = false

async function work(): Promise<void> {
  if (busy) return
  busy = true
  try {
    for (let request = waiting.shift(); request; request = waiting.shift()) {
      send(await runner.handle(request))
    }
  } finally {
    busy = false
  }
}

interface Answer {
  answer: number
  bars?: BarRow[]
  error?: string
}

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity })
lines.on('line', (line) => {
  let message: unknown
  try {
    message = JSON.parse(line)
  } catch {
    send({ id: null, ok: false, error: { message: 'A line that is not JSON', file: null, line: null, column: null } })
    return
  }
  if (message !== null && typeof message === 'object' && 'answer' in message) {
    const { answer, bars, error } = message as Answer
    const pending = asked.get(answer)
    asked.delete(answer)
    if (!pending) return
    if (error !== undefined || !Array.isArray(bars)) pending.reject(new Error(error ?? 'No bars'))
    else pending.resolve(bars)
    return
  }
  waiting.push(message as RunnerRequest)
  void work()
})
// The engine closed its end: there is nobody left to answer.
lines.on('close', () => process.exit(0))
