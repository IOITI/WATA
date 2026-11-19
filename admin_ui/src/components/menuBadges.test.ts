import { describe, expect, it } from 'vitest'

import type { AgentState, Health, Overview, Position, ServiceHealth, Session } from '@/lib/types'

import { menuBadges } from './menuBadges'

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

function service(id: string, status: ServiceHealth['status']): ServiceHealth {
  return { id, name: id, status, detail: '' }
}

function health(status: Health['status'], services: ServiceHealth[]): Health {
  return { version: '0.94.0', checked_at: '', status, services, queues: [], watchlist: null, config_modified_at: '' }
}

function overview(positions: Partial<Position>[], paused: string | null = null): Overview {
  return { entries_paused_since: paused, open_positions: positions as Position[] } as Overview
}

describe("the sidebar's badges", () => {
  it('say nothing while all is well', () => {
    expect(menuBadges({ session: SESSION, health: health('ok', [service('saxo', 'ok')]), overview: overview([]) })).toEqual({})
  })

  it('count the positions open, say the sales Saxo refuses and the pause of new entries', () => {
    const badges = menuBadges({
      session: SESSION,
      overview: overview([{ position_id: '1' }, { position_id: '2', sell_refusals: { count: 3 } as Position['sell_refusals'] }], '2026-11-19T08:00:00Z'),
    })
    expect(badges.positions).toEqual({ badge: 2 })
    expect(badges.orders).toEqual({ badge: '1 refused', badgeTone: 'negative' })
    expect(badges.dashboard).toEqual({ badge: 'Paused', badgeTone: 'warning' })
  })

  it('put a service that is not well on the page about it, and on the System', () => {
    const badges = menuBadges({
      session: SESSION,
      health: health('down', [
        service('capital_feed', 'warning'),
        service('ta_engine', 'down'),
        service('disk', 'down'),
        service('edge_watch', 'warning'),
        service('watchlist_manager', 'warning'),
      ]),
    })
    expect(badges.providers).toEqual({ badge: 'Warning', badgeTone: 'warning' })
    expect(badges['ta-monitor']).toEqual({ badge: 'Down', badgeTone: 'negative' })
    expect(badges.disk).toEqual({ badge: 'Critical', badgeTone: 'negative' })
    expect(badges.performance).toEqual({ badge: 'Watch', badgeTone: 'warning' })
    expect(badges.watchlist).toEqual({ badge: 'Warning', badgeTone: 'warning' })
    expect(badges.system).toEqual({ badge: 'Down', badgeTone: 'negative' })
  })

  it("say Saxo's login first on the System, then a release, then a restart", () => {
    const saxo = health('down', [service('saxo', 'down')])
    expect(menuBadges({ session: SESSION, health: saxo }).system).toEqual({ badge: 'Authorise', badgeTone: 'negative' })

    const waiting = { ...SESSION, release: { newest: '0.95.0', containers: {}, waiting: ['admin1'], applying: false } }
    expect(menuBadges({ session: waiting, health: health('ok', []) }).system).toEqual({ badge: 'Release', badgeTone: 'warning' })
    const applying = { ...SESSION, release: { newest: '0.95.0', containers: {}, waiting: ['admin1'], applying: true } }
    expect(menuBadges({ session: applying }).system).toEqual({ badge: 'Deploying', badgeTone: 'warning' })

    const restart = { ...SESSION, restart_required: true, restart_services: [{ id: 'trader', name: 'Trader' }] }
    expect(menuBadges({ session: restart }).system).toEqual({ badge: 'Restart', badgeTone: 'warning' })
    expect(menuBadges({ session: restart }).config).toBeUndefined()
    // The control service cannot say which: the Configuration does.
    const unplaced = { ...SESSION, restart_required: true, restart_services: null }
    expect(menuBadges({ session: unplaced }).config).toEqual({ badge: 'Restart', badgeTone: 'warning' })
  })

  it("say the AI agent's questions first, then its pause, then what it is doing", () => {
    const agent = { enabled: true, model: 'm', under_way: ['a', 'b'], asking: [] } as AgentState
    expect(menuBadges({ session: SESSION, agent }).agent).toEqual({ badge: 2 })
    expect(menuBadges({ session: SESSION, agent: { ...agent, pause: { since: '', by: null } } }).agent).toEqual({ badge: 'Paused', badgeTone: 'warning' })
    expect(menuBadges({ session: SESSION, agent: { ...agent, asking: ['c'] } }).agent).toEqual({ badge: '1 question', badgeTone: 'warning' })
    expect(menuBadges({ session: SESSION, agent: { ...agent, enabled: false } }).agent).toBeUndefined()
  })
})
