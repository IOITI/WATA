import { describe, expect, it } from 'vitest'

import type { RestartPlan, ServicesState, WataService } from '@/lib/types'

import {
  containerSaid,
  heldBack,
  inPlanOrder,
  pendingSaid,
  planOutcome,
  planRunning,
  planSucceeded,
  restartNeededHint,
  restartUnavailable,
  withPendingChanges,
} from './restarts'

function service(id: string, name: string, overrides: Partial<WataService> = {}): WataService {
  return {
    id,
    name,
    trading_path: false,
    container: `${id}1`,
    state: 'running',
    running: true,
    started_at: '2026-10-24T07:00:00.123456789Z',
    finished_at: null,
    exit_code: 0,
    oom_killed: false,
    restart_count: 0,
    image: 'wata-base:latest',
    restarting: false,
    last_restart: null,
    pending: { paths: [], outside: false },
    refusal: null,
    cost: 'Messages wait in the queue meanwhile.',
    ...overrides,
  }
}

const NAMES = { telegram: 'Telegram', scheduler: 'Scheduler', trader: 'Trader', admin: 'Admin', ta_engine: 'TA engine' }

function plan(steps: RestartPlan['steps'], done = true): RestartPlan {
  return { services: steps.map((step) => step.service), forced: false, by: null, started_at: '', steps, done }
}

function state(overrides: Partial<ServicesState> = {}): ServicesState {
  return {
    available: true,
    error: null,
    starts_known: true,
    actions_enabled: true,
    services: [
      service('telegram', 'Telegram'),
      service('scheduler', 'Scheduler', { pending: { paths: ['a', 'b'], outside: false } }),
      service('ta_engine', 'TA engine', { state: 'absent', running: null, pending: { paths: ['c'], outside: false } }),
      service('trader', 'Trader', { pending: { paths: [], outside: true }, refusal: '1 position open', trading_path: true }),
    ],
    plan: null,
    ...overrides,
  }
}

describe('a plan of restarts', () => {
  it('says every service at work again', () => {
    const done = plan([
      { service: 'telegram', state: 'ready', detail: 'Consuming telegram_channel' },
      { service: 'scheduler', state: 'ready', detail: 'Running since …' },
    ])

    expect(planOutcome(done, NAMES)).toBe('Telegram and Scheduler are at work again.')
    expect(planSucceeded(done)).toBe(true)
    expect(planRunning(done)).toBe(false)
  })

  it('says where it stopped, and what it did not restart', () => {
    const stopped = plan([
      { service: 'telegram', state: 'ready', detail: null },
      { service: 'scheduler', state: 'failed', detail: 'Not running again 180 s after the restart' },
      { service: 'trader', state: 'skipped', detail: 'Not restarted: the plan stopped before it' },
    ])

    expect(planOutcome(stopped, NAMES)).toBe(
      'Telegram is at work again; Scheduler: Not running again 180 s after the restart; not restarted: Trader.',
    )
    expect(planSucceeded(stopped)).toBe(false)
  })

  it('says a service held back on its turn, one not created, and the admin restarting', () => {
    const ended = plan([
      { service: 'ta_engine', state: 'skipped', detail: 'Its container is not created: its compose profile is off' },
      { service: 'trader', state: 'held', detail: 'Held back: 1 position open' },
      { service: 'admin', state: 'skipped', detail: 'Not restarted: the plan stopped before it' },
    ])
    expect(planOutcome(ended, NAMES)).toBe(
      'Trader: Held back: 1 position open; not restarted: Admin; TA engine: not created, its profile off.',
    )

    const admin = plan([
      { service: 'telegram', state: 'ready', detail: null },
      { service: 'admin', state: 'restarting', detail: 'The admin restarts: sign in again in a few seconds' },
    ])
    expect(planOutcome(admin, NAMES)).toBe('Telegram is at work again; the admin restarts: sign in again in a few seconds.')
    expect(planSucceeded(admin)).toBe(true)
  })

  it('is running until done', () => {
    expect(planRunning(plan([{ service: 'telegram', state: 'restarting', detail: null }], false))).toBe(true)
    expect(planRunning(null)).toBe(false)
  })
})

describe('the services', () => {
  it('with changes not applied are those running without them, in the order of the plan', () => {
    expect(withPendingChanges(state()).map((found) => found.id)).toEqual(['scheduler', 'trader'])
  })

  it('say their changes not applied', () => {
    const [telegram, scheduler, , trader] = state().services

    expect(pendingSaid(telegram)).toBeNull()
    expect(pendingSaid(scheduler)).toBe('2 saved changes not applied')
    expect(pendingSaid(trader)).toBe('the file changed on the server since it started')
  })

  it('say what their container does', () => {
    const [telegram, , engine] = state().services

    expect(containerSaid(telegram, 'Europe/Paris')).toMatch(/^Running since /)
    expect(containerSaid(engine, 'Europe/Paris')).toBe('Not created: its compose profile is off')
    expect(containerSaid(service('x', 'X', { state: 'exited', running: false, exit_code: 137 }), 'UTC')).toBe(
      'Exited, exit code 137',
    )
    expect(containerSaid(service('x', 'X', { state: null }), 'UTC')).toBe('Unknown')
    expect(containerSaid(service('x', 'X', { restarting: true }), 'UTC')).toBe('Restarting')
  })

  it('say what holds their restart back, in the order of the plan', () => {
    const services = state().services

    expect(heldBack(services, ['trader', 'telegram'])).toEqual([{ id: 'trader', name: 'Trader', reason: '1 position open' }])
    expect(inPlanOrder(services, ['trader', 'telegram']).map((found) => found.id)).toEqual(['telegram', 'trader'])
  })

  it('cannot be restarted from here without the control service, the actions, or while a plan runs', () => {
    expect(restartUnavailable(state())).toBeNull()
    expect(restartUnavailable(state({ available: false, error: 'No control service runs beside this admin' }))).toBe(
      'No control service runs beside this admin',
    )
    expect(restartUnavailable(state({ actions_enabled: false }))).toMatch(/admin\.actions_enabled/)
    expect(restartUnavailable(state({ plan: plan([{ service: 'telegram', state: 'restarting', detail: null }], false) }))).toMatch(
      /one at a time/,
    )
    expect(restartUnavailable(state(), state().services[2])).toMatch(/not created/)
  })
})

describe("the top bar's Restart needed", () => {
  it('names the services, or says it of them all without the control service', () => {
    expect(restartNeededHint({ restart_services: [{ id: 'trader', name: 'Trader' }] })).toBe(
      'Trader runs without a change saved since it started: restart it from the System page.',
    )
    expect(
      restartNeededHint({
        restart_services: [
          { id: 'scheduler', name: 'Scheduler' },
          { id: 'trader', name: 'Trader' },
          { id: 'admin', name: 'Admin' },
        ],
      }),
    ).toBe('Scheduler, Trader and Admin run without a change saved since they started: restart them from the System page.')
    expect(restartNeededHint({ restart_services: null })).toBe(
      'The configuration changed since the services started: they apply it at their next start.',
    )
  })
})
