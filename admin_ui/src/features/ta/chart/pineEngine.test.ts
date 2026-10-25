import type {
  EngineAlert,
  ExecutionHandlers,
  ExecutionRequest,
  ExecutionSession,
  IndicatorModel,
  OHLCV,
  PreparedScript,
  ScriptingEngine,
} from '@luxalgo/vela'
import { describe, expect, it, vi } from 'vitest'

import { fullRunEngine, OVER_AFTER_MS, overBars, RUN_EVERY_MS } from './pineEngine'
import type { GrowingBar, RunClock } from './pineEngine'

/** A clock moved by the test, its timers run when their time comes. */
function fakeClock(start = 0) {
  let now = start
  let next = 1
  const timers = new Map<number, { at: number; callback: () => void }>()
  const clock: RunClock = {
    now: () => now,
    setTimer: (callback, ms) => {
      timers.set(next, { at: now + ms, callback })
      return next++
    },
    clearTimer: (timer) => {
      timers.delete(timer as number)
    },
  }
  const advance = (ms: number) => {
    now += ms
    for (const [id, timer] of [...timers]) {
      if (timer.at > now) continue
      timers.delete(id)
      timer.callback()
    }
  }
  return { clock, advance, pending: () => timers.size }
}

/** An engine that records what its session is asked, and lets the test answer as its runs would. */
function fakeEngine() {
  const notified: (string | undefined)[] = []
  const calls: string[] = []
  let handlers: ExecutionHandlers | null = null
  let given: ExecutionRequest | null = null
  const session: ExecutionSession = {
    getContext: async () => null,
    stop: () => calls.push('stop'),
    update: () => calls.push('update'),
    setVisibleRange: () => calls.push('setVisibleRange'),
    notifyBars: (reason) => notified.push(reason),
  }
  const engine: ScriptingEngine = {
    language: 'pine',
    capabilities: { streaming: true, visibleRange: true, inputs: true, props: true },
    prepare: vi.fn(async () => ({ inputs: [] }) as unknown as PreparedScript),
    execute: vi.fn((request: ExecutionRequest, handed: ExecutionHandlers) => {
      given = request
      handlers = handed
      return session
    }),
  }
  return { engine, notified, calls, run: () => handlers as ExecutionHandlers, given: () => given as ExecutionRequest }
}

const MINUTE = 60_000
const bar = (time: number, close = 1): OHLCV => ({ time, open: 1, high: 2, low: 0, close, volume: 3 })

interface Start {
  bars?: OHLCV[]
  symbol?: string
  timeframe?: string
  growing?: GrowingBar
  now?: number
}

function started(start: Start = {}) {
  const fake = fakeEngine()
  const time = fakeClock(start.now ?? 0)
  const bars = start.bars ?? []
  const request = {
    mode: 'live',
    bars: [...bars],
    getBars: () => bars,
    market: { symbol: start.symbol ?? 'capital:CAPITAL_US100', timeframe: start.timeframe ?? '1' },
  } as unknown as ExecutionRequest
  const told = { models: 0, alerts: [] as EngineAlert[], errors: [] as string[] }
  const session = fullRunEngine(fake.engine, time.clock, start.growing).execute(request, {
    onModel: () => {
      told.models += 1
    },
    onAlert: (alert) => told.alerts.push(alert),
    onError: (error) => told.errors.push(error.message),
  })
  return { ...fake, ...time, session, told, bars }
}

/** A range chart whose bars are each over as soon as the next one begins: the last one grows. */
function rangeChart(count: number) {
  const bars = Array.from({ length: count }, (_, index) => bar(index * 1000))
  const growing: GrowingBar = () => bars[bars.length - 1]?.time ?? null
  return { ...started({ bars, symbol: 'capital:CAPITAL_US100.R60', growing, now: count * 1000 }), growing }
}

const alert = (time: number, title = 'Cross up'): EngineAlert => ({ id: 'a1', title, message: '{"action": "long"}', time, barIndex: 499 })

describe('the bars that are over', () => {
  const market = { symbol: 'capital:CAPITAL_US100', timeframe: '1' }

  it('are all of them but a bar of time whose last ten seconds may still come', () => {
    const bars = [bar(0), bar(MINUTE)]

    // The second minute ended at 2:00; its last ten seconds can come until 2:10.
    expect(overBars(bars, market, () => null, MINUTE + 30_000)).toEqual([bar(0)])
    expect(overBars(bars, market, () => null, 2 * MINUTE + OVER_AFTER_MS - 1)).toEqual([bar(0)])
    expect(overBars(bars, market, () => null, 2 * MINUTE + OVER_AFTER_MS)).toEqual(bars)
    // A bar of 45 seconds ends with its last ten seconds: 50 seconds on for this one.
    expect(overBars([bar(0)], { ...market, timeframe: '45S' }, () => null, 50_000 + OVER_AFTER_MS)).toEqual([bar(0)])
    expect(overBars([], market, () => null, 0)).toEqual([])
  })

  it('leave out a bar of time whose last ten seconds may still come, though the next one has begun', () => {
    const bars = [bar(0), bar(MINUTE), bar(2 * MINUTE)]

    expect(overBars(bars, market, () => null, 2 * MINUTE + 5000)).toEqual([bar(0)])
    expect(overBars(bars, market, () => null, 2 * MINUTE + OVER_AFTER_MS)).toEqual(bars.slice(0, 2))
  })

  it('are all of them but a range bar the server says is in progress', () => {
    const bars = [bar(0), bar(1000)]
    const range = { symbol: 'capital:CAPITAL_US100.R60', timeframe: '1' }

    expect(overBars(bars, range, () => 1000, 10 * MINUTE)).toEqual([bar(0)])
    // Ended: a newer read says no bar of the series is in progress, or another one is.
    expect(overBars(bars, range, () => null, 0)).toEqual(bars)
    expect(overBars(bars, range, () => 2000, 0)).toEqual(bars)
  })
})

describe('the Charts page\'s Pine engine', () => {
  it('says it cannot stream, so that Vela asks for whole runs, and is otherwise the engine it wraps', async () => {
    const { engine } = fakeEngine()
    const wrapped = fullRunEngine(engine)

    expect(wrapped.language).toBe('pine')
    expect(wrapped.capabilities).toEqual({ streaming: false, visibleRange: true, inputs: true, props: true })
    await wrapped.prepare('//@version=6', 'ind-1')
    expect(engine.prepare).toHaveBeenCalledWith('//@version=6', 'ind-1')
  })

  it('hands the engine the bars that are over, when it starts and at each run', () => {
    const { given, bars, advance } = started({ bars: [bar(0), bar(MINUTE)], now: MINUTE + 30_000 })

    expect(given().bars).toEqual([bar(0)])
    expect(given().getBars?.()).toEqual([bar(0)])
    advance(MINUTE)
    expect(given().getBars?.()).toEqual(bars)
  })

  it('runs a script again when a bar is over, not at each quote of the bar still growing', () => {
    const { session, notified, bars, advance } = started({ bars: [bar(0), bar(MINUTE)], now: MINUTE + 30_000 })

    // Quotes move the minute still growing: nothing to run.
    for (let quote = 0; quote < 10; quote += 1) {
      bars[1] = bar(MINUTE, 1 + quote)
      advance(1000)
      session.notifyBars()
    }
    expect(notified).toEqual([])

    // The next minute began at 2:00; the one before is over at 2:10.
    bars.push(bar(2 * MINUTE))
    advance(2 * MINUTE + 5000 - (MINUTE + 40_000))
    session.notifyBars()
    expect(notified).toEqual([])
    advance(OVER_AFTER_MS)
    session.notifyBars()
    expect(notified).toEqual([undefined])
  })

  it('runs it when a bar of time is over with no quote after it, as when the market closes', () => {
    const { session, notified, advance, pending } = started({ bars: [bar(0), bar(MINUTE)], now: MINUTE + 30_000 })

    session.notifyBars()
    expect(notified).toEqual([])
    expect(pending()).toBe(1)
    advance(MINUTE)
    expect(notified).toEqual([undefined])
    expect(pending()).toBe(0)
  })

  it('runs it at the first range bar over, then a second apart at most, the last bar included', () => {
    const { session, notified, bars, advance, pending } = rangeChart(3)

    bars.push(bar(3000))
    session.notifyBars()
    expect(notified).toEqual([undefined])
    // Four more bars within the second: one run, when it ends.
    for (let more = 4; more < 8; more += 1) {
      advance(200)
      bars.push(bar(more * 1000))
      session.notifyBars()
    }
    expect(notified).toHaveLength(1)
    expect(pending()).toBe(1)
    advance(200)
    expect(notified).toHaveLength(2)

    // A bar long after the last run: run at once.
    advance(5 * RUN_EVERY_MS)
    bars.push(bar(9000))
    session.notifyBars()
    expect(notified).toHaveLength(3)
  })

  it('passes on at once what Vela says of the history, and forgets the run due when stopped', () => {
    const { session, notified, calls, bars, advance, pending } = rangeChart(3)

    bars.push(bar(3000))
    session.notifyBars()
    session.notifyBars('backfill')
    session.notifyBars('complete')
    expect(notified).toEqual([undefined, 'backfill', 'complete'])

    advance(100)
    bars.push(bar(4000))
    session.notifyBars()
    expect(pending()).toBe(1)
    session.stop()
    expect(pending()).toBe(0)
    advance(RUN_EVERY_MS)
    expect(notified).toHaveLength(3)
    expect(calls).toEqual(['stop'])
  })

  it('passes on an alert once, though each run of its bar raises it again', () => {
    const { run, told } = started()

    run().onAlert?.(alert(60_000))
    run().onAlert?.(alert(60_000))
    // Another alert of the same bar, then the same one on the next bar.
    run().onAlert?.(alert(60_000, 'Cross down'))
    run().onAlert?.(alert(120_000))

    expect(told.alerts.map((one) => [one.time, one.title])).toEqual([
      [60_000, 'Cross up'],
      [60_000, 'Cross down'],
      [120_000, 'Cross up'],
    ])
  })

  it('passes on an error once while the runs asked for by new bars fail, and again after a success or new inputs', () => {
    const { session, run, told } = started()

    run().onError?.(new Error('index out of bounds'))
    run().onError?.(new Error('index out of bounds'))
    expect(told.errors).toEqual(['index out of bounds'])

    run().onModel({} as IndicatorModel)
    run().onError?.(new Error('index out of bounds'))
    // New inputs: Vela waits for how their run ends.
    session.update({ in_0: 12 })
    run().onError?.(new Error('index out of bounds'))

    expect(told.models).toBe(1)
    expect(told.errors).toHaveLength(3)
  })
})
