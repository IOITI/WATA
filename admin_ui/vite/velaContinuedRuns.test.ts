// @vitest-environment node
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

import { describe, expect, it } from 'vitest'

import { patchPinets } from './pinetsZoneFormat.ts'
import { CONTINUED_RUN_PATCHES, patchContinuedRuns } from './velaContinuedRuns.ts'
import { extractWorkerSource, velaPinetsBundlePath } from './velaPineWorker.ts'

/** Vela-pinets' worker as the admin serves it: PineTS's formatters kept, its runs continued. */
function servedWorker(): string {
  return patchContinuedRuns(patchPinets(extractWorkerSource(readFileSync(velaPinetsBundlePath(), 'utf8'))))
}

type Message = Record<string, unknown> & { kind: string }

/** The worker in a context of its own, as a page's Web Worker: what it is sent and what it posts, each cloned. */
function startWorker(source: string) {
  const posted: Message[] = []
  let listener: ((event: { data: unknown }) => void) | null = null
  const self: Record<string, unknown> = {
    postMessage: (message: unknown) => posted.push(structuredClone(message) as Message),
    addEventListener: (type: string, handler: (event: { data: unknown }) => void) => {
      if (type === 'message') listener = handler
    },
  }
  const context = vm.createContext({
    self,
    console,
    setTimeout,
    clearTimeout,
    queueMicrotask,
    structuredClone,
    performance,
    TextEncoder,
    TextDecoder,
  })
  vm.runInContext(source, context)
  const send = (message: Message) => listener?.({ data: structuredClone(message) })
  /** What the worker posts until the next `done` of a session (or an error), once it has. */
  const until = async (sessionId: number, from: number): Promise<Message[]> => {
    for (let wait = 0; wait < 2000; wait += 1) {
      const ended = posted.findIndex((message, index) => index >= from && message.sessionId === sessionId && ['done', 'error'].includes(message.kind))
      if (ended !== -1) return posted.slice(from, ended + 1)
      await new Promise((resolve) => setTimeout(resolve, 5))
    }
    throw new Error(`session ${sessionId} never ended`)
  }
  const prepare = async (script: string): Promise<Record<string, unknown>> => {
    send({ kind: 'prepare', reqId: 1, source: script, instanceId: 'ind-1' })
    for (let wait = 0; wait < 2000; wait += 1) {
      const found = posted.find((message) => message.kind === 'prepared')
      if (found) {
        if (found.error) throw new Error(String(found.error))
        return found.prepared as Record<string, unknown>
      }
      await new Promise((resolve) => setTimeout(resolve, 5))
    }
    throw new Error('the script was never prepared')
  }
  return { send, until, prepare, posted, continued: () => (self.__wataContinued as number | undefined) ?? 0 }
}

// A script that keeps state from bar to bar (var, ta.*) and raises alerts at its last bar.
const SCRIPT = `//@version=6
indicator("Continued", overlay=true)
fast = ta.ema(close, 5)
slow = ta.sma(close, 20)
var float peak = na
peak := na(peak) ? high : math.max(peak, high)
var int ups = 0
ups += close > open ? 1 : 0
plot(fast, "Fast")
plot(slow, "Slow")
plot(peak, "Peak")
plot(ups, "Ups")
alertcondition(close > fast, "Above", "above the fast average")
alertcondition(close <= fast, "Below", "below the fast average")
`

const MARKET = { symbol: 'capital:CAPITAL_US100', timeframe: '1' }

/** Minutes of a made-up market from a seed, the same each time. */
function minutes(count: number, seed = 7) {
  let state = seed
  const next = () => {
    state = (state * 1_103_515_245 + 12_345) % 2_147_483_648
    return state / 2_147_483_648
  }
  let price = 25_000
  return Array.from({ length: count }, (_, index) => {
    const open = price
    const close = Math.round((open + (next() - 0.5) * 20) * 10) / 10
    const high = Math.max(open, close) + Math.round(next() * 30) / 10
    const low = Math.min(open, close) - Math.round(next() * 30) / 10
    price = close
    return { time: Date.UTC(2026, 9, 22, 7, 0) + index * 60_000, open, high, low, close, volume: Math.round(next() * 40) }
  })
}

function modelOf(messages: Message[]): unknown {
  const models = messages.filter((message) => message.kind === 'model')
  return models[models.length - 1]?.model
}

function alertsOf(messages: Message[]): unknown[] {
  return messages.filter((message) => message.kind === 'alert').map((message) => message.alert)
}

/** Each plot of a model, and how many bars it has a point at. */
function plotted(model: unknown): [string, number][] {
  return (model as { series: { title: string; points: unknown[] }[] }).series.map((series) => [series.title, series.points.length])
}

/** A whole run of the script over `bars`, in a session of its own. */
async function wholeRun(worker: ReturnType<typeof startWorker>, prepared: Record<string, unknown>, sessionId: number, bars: unknown[]) {
  const from = worker.posted.length
  worker.send({ kind: 'execute', sessionId, mode: 'static', prepared, market: MARKET, bars, inputs: {}, props: {} })
  return worker.until(sessionId, from)
}

describe('Vela-pinets\' worker, its runs continued', () => {
  it('is patched in the installed package: the build stops if a piece of code is not there once', () => {
    const source = servedWorker()

    expect(source).toContain('function __wataRun(t)')
    expect(source).toContain('ctx:f,pine:d}}')
    expect(() => patchContinuedRuns('nothing of the worker')).toThrow('@luxalgo/vela-pinets changed')
    expect(() => patchContinuedRuns(`${CONTINUED_RUN_PATCHES[0].find}${CONTINUED_RUN_PATCHES[0].find}`)).toThrow('found 2 times')
  })

  it('gives a script\'s new bars to its run, and its model and alerts are those of a whole run over the same bars', async () => {
    const bars = minutes(300)
    const worker = startWorker(servedWorker())
    const prepared = await worker.prepare(SCRIPT)
    const token = { ...(prepared.token as Record<string, unknown>), wataContinue: true }
    const continuing = { ...prepared, token }

    await wholeRun(worker, continuing, 1, bars.slice(0, 250))
    let from = worker.posted.length
    worker.send({ kind: 'notifyBars', sessionId: 1, bars: bars.slice(0, 251) })
    const one = await worker.until(1, from)
    from = worker.posted.length
    worker.send({ kind: 'notifyBars', sessionId: 1, bars: bars.slice(0, 258) })
    const seven = await worker.until(1, from)

    expect(worker.continued()).toBe(2)
    expect(plotted(modelOf(seven))).toEqual([['Fast', 258], ['Slow', 258], ['Peak', 258], ['Ups', 258]])
    const wholeAt251 = await wholeRun(worker, prepared, 2, bars.slice(0, 251))
    const wholeAt258 = await wholeRun(worker, prepared, 3, bars.slice(0, 258))
    expect(modelOf(one)).toEqual(modelOf(wholeAt251))
    expect(modelOf(seven)).toEqual(modelOf(wholeAt258))
    // The alerts of the last bar, as a whole run raises them.
    expect(alertsOf(one)).toEqual(alertsOf(wholeAt251))
    expect(alertsOf(seven)).toEqual(alertsOf(wholeAt258))
    expect(alertsOf(seven)).toHaveLength(1)
    // Only the continued runs: the whole ones of the other sessions continue nothing.
    expect(worker.continued()).toBe(2)
  })

  it('runs a script whole when it is not marked as one whose run can continue, or when earlier bars changed', async () => {
    const bars = minutes(300)
    const worker = startWorker(servedWorker())
    const prepared = await worker.prepare(SCRIPT)
    const token = prepared.token as Record<string, unknown>

    await wholeRun(worker, prepared, 1, bars.slice(0, 250))
    let from = worker.posted.length
    worker.send({ kind: 'notifyBars', sessionId: 1, bars: bars.slice(0, 251) })
    await worker.until(1, from)
    expect(worker.continued()).toBe(0)

    // History loaded again: the first bars are no longer those of the run.
    await wholeRun(worker, { ...prepared, token: { ...token, wataContinue: true } }, 2, bars.slice(10, 260))
    from = worker.posted.length
    worker.send({ kind: 'notifyBars', sessionId: 2, bars: bars.slice(0, 261) })
    const anew = await worker.until(2, from)
    expect(worker.continued()).toBe(0)
    expect(modelOf(anew)).toEqual(modelOf(await wholeRun(worker, prepared, 3, bars.slice(0, 261))))
  })
})
