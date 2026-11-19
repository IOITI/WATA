import { describe, expect, it } from 'vitest'

import type { Health, ServiceHealth, Session } from '@/lib/types'

import { SAXO_AUTHORISE_HINT, stateItems } from './stateItems'

const SESSION = {
  authenticated: true,
  version: '0.94.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: true,
  restart_required: false,
  restart_services: [],
  release: { newest: '0.94.0', containers: {}, waiting: [], applying: false },
} as Session

function service(id: string, name: string, status: ServiceHealth['status'], detail = ''): ServiceHealth {
  return { id, name, status, detail }
}

function health(services: ServiceHealth[]): Health {
  return { version: '0.94.0', checked_at: '', status: 'ok', services, queues: [], watchlist: null, config_modified_at: '' }
}

const quiet = (items: ReturnType<typeof stateItems>) => items.map(({ id, tone, label, href }) => ({ id, tone, label, href }))

describe('the states of the top bar', () => {
  it('are quiet while all is well: Saxo authorised, the services up, the page told live', () => {
    expect(quiet(stateItems({ session: SESSION, health: health([service('saxo', 'Saxo', 'ok'), service('trader', 'Trader', 'ok')]), live: true }))).toEqual([
      { id: 'saxo', tone: 'ok', label: 'Saxo: authorised', href: '/system' },
      { id: 'services', tone: 'ok', label: 'All services up', href: '/system' },
      { id: 'live', tone: 'ok', label: 'Live updates', href: undefined },
    ])
  })

  it("say Saxo's login to fix apart from the services, and nothing of it while it is unknown", () => {
    const items = stateItems({ session: SESSION, health: health([service('saxo', 'Saxo', 'down'), service('trader', 'Trader', 'ok')]), live: true })
    expect(items[0]).toMatchObject({ id: 'saxo', tone: 'danger', label: 'Saxo: authorise', detail: SAXO_AUTHORISE_HINT, href: '/system' })
    expect(items[1]).toMatchObject({ id: 'services', tone: 'ok' })
    expect(stateItems({ session: SESSION, health: health([service('saxo', 'Saxo', 'unknown')]), live: true }).some((item) => item.id === 'saxo')).toBe(false)
  })

  it('name the services down, else those to watch', () => {
    const one = stateItems({ session: SESSION, health: health([service('disk', 'Disk space', 'warning', '12 % free')]), live: true })
    expect(one.find((item) => item.id === 'services')).toMatchObject({ tone: 'warning', label: 'Disk space: warning', detail: '12 % free' })
    const several = stateItems({
      session: SESSION,
      health: health([service('ta_engine', 'TA engine', 'down'), service('capital_feed', 'Capital.com feed', 'down'), service('disk', 'Disk space', 'warning')]),
      live: true,
    })
    expect(several.find((item) => item.id === 'services')).toMatchObject({ tone: 'danger', label: '2 services down', detail: 'TA engine and Capital.com feed.' })
  })

  it('say a release waiting or applying, and a restart where it is dealt with', () => {
    const waiting = { ...SESSION, release: { newest: '0.95.0', containers: {}, waiting: ['admin1'], applying: false } }
    expect(stateItems({ session: waiting, live: true }).find((item) => item.id === 'release')).toMatchObject({
      tone: 'warning', label: 'Release waiting', href: '/system#release',
    })
    const restart = { ...SESSION, restart_required: true, restart_services: null }
    expect(stateItems({ session: restart, live: true }).find((item) => item.id === 'restart')).toMatchObject({ tone: 'warning', href: '/config' })
    const placed = { ...SESSION, restart_required: true, restart_services: [{ id: 'trader', name: 'Trader' }] }
    expect(stateItems({ session: placed, live: true }).find((item) => item.id === 'restart')).toMatchObject({ href: '/system' })
  })

  it('say a page that polls in grey, asking nothing', () => {
    expect(stateItems({ session: SESSION, live: false }).at(-1)).toMatchObject({ id: 'live', tone: 'neutral', label: 'Polling' })
  })
})
