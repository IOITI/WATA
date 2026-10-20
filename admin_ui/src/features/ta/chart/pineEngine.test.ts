import type { EngineAlert, ExecutionHandlers, ExecutionRequest, ExecutionSession, IndicatorModel, PreparedScript, ScriptingEngine } from '@luxalgo/vela'
import { describe, expect, it, vi } from 'vitest'

import { fullRunEngine, RUN_EVERY_MS } from './pineEngine'
import type { RunClock } from './pineEngine'

/** A clock moved by the test, its timers run when their time comes. */
function fakeClock() {
  let now = 0
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
    execute: vi.fn((_request: ExecutionRequest, given: ExecutionHandlers) => {
      handlers = given
      return session
    }),
  }
  return { engine, notified, calls, run: () => handlers as ExecutionHandlers }
}

const REQUEST = { mode: 'static', bars: [] } as unknown as ExecutionRequest

function started() {
  const fake = fakeEngine()
  const time = fakeClock()
  const told = { models: 0, alerts: [] as EngineAlert[], errors: [] as string[] }
  const session = fullRunEngine(fake.engine, time.clock).execute(REQUEST, {
    onModel: () => {
      told.models += 1
    },
    onAlert: (alert) => told.alerts.push(alert),
    onError: (error) => told.errors.push(error.message),
  })
  return { ...fake, ...time, session, told }
}

const alert = (time: number, title = 'Cross up'): EngineAlert => ({ id: 'a1', title, message: '{"action": "long"}', time, barIndex: 499 })

describe('the Charts page\'s Pine engine', () => {
  it('says it cannot stream, so that Vela asks for whole runs, and is otherwise the engine it wraps', async () => {
    const { engine } = fakeEngine()
    const wrapped = fullRunEngine(engine)

    expect(wrapped.language).toBe('pine')
    expect(wrapped.capabilities).toEqual({ streaming: false, visibleRange: true, inputs: true, props: true })
    await wrapped.prepare('//@version=6', 'ind-1')
    expect(engine.prepare).toHaveBeenCalledWith('//@version=6', 'ind-1')
    wrapped.execute(REQUEST, { onModel: () => undefined })
    expect(engine.execute).toHaveBeenCalledWith(REQUEST, expect.any(Object))
  })

  it('runs a script again at the first quote, then a second apart at most, the last quote included', () => {
    const { session, notified, advance, pending } = started()

    session.notifyBars()
    expect(notified).toEqual([undefined])
    // Four more quotes within the second: one run, when it ends.
    for (let quote = 0; quote < 4; quote += 1) {
      advance(200)
      session.notifyBars()
    }
    expect(notified).toHaveLength(1)
    expect(pending()).toBe(1)
    advance(200)
    expect(notified).toHaveLength(2)

    // A quote long after the last run: run at once.
    advance(5 * RUN_EVERY_MS)
    session.notifyBars()
    expect(notified).toHaveLength(3)
  })

  it('passes on at once what Vela says of the history, and forgets the run due when stopped', () => {
    const { session, notified, calls, advance, pending } = started()

    session.notifyBars()
    session.notifyBars('backfill')
    session.notifyBars('complete')
    expect(notified).toEqual([undefined, 'backfill', 'complete'])

    advance(100)
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

  it('passes on an error once while the runs asked for by quotes fail, and again after a success or new inputs', () => {
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
