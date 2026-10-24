import { describe, expect, it } from 'vitest'

import type { ServiceHealth } from '@/lib/types'

import { MAP_LINKS, MAP_NODES, serviceMap } from './serviceMap'

// The ids src/admin/health.py gives its entries (the calendars and the backups are files, not services: not on the map).
const HEALTH_IDS = [
  'saxo', 'postgres', 'web_server', 'watchlist_manager', 'price_recorder', 'capital_feed', 'ta_engine',
  'macro_calendar', 'market_calendar', 'backups', 'rabbitmq', 'position_monitor', 'telegram', 'trader', 'scheduler',
]

describe('the map of the services', () => {
  it('links only what it draws, each link named', () => {
    const ids = new Set(MAP_NODES.map((node) => node.id))
    for (const link of MAP_LINKS) {
      expect(ids.has(link.from), link.from).toBe(true)
      expect(ids.has(link.to), link.to).toBe(true)
      expect(link.label).toBeTruthy()
    }
  })

  it('reads each service’s health under the id the health summary gives it, and draws every service', () => {
    const named = MAP_NODES.flatMap((node) => (node.health ? [node.health] : []))
    for (const id of named) expect(HEALTH_IDS, id).toContain(id)
    expect(HEALTH_IDS.filter((id) => !named.includes(id))).toEqual(['macro_calendar', 'market_calendar', 'backups'])
  })

  it('gives each node its health, in words too, and leaves the outside ones without', () => {
    const services: ServiceHealth[] = [
      { id: 'trader', name: 'Trader', status: 'down', detail: 'Stopped 2026-10-23T05:00:00Z (from its log)' },
      { id: 'saxo', name: 'Saxo connection', status: 'ok', detail: 'Authorised' },
    ]
    const nodes = new Map(serviceMap(services).map((node) => [node.id, node]))

    expect(nodes.get('trader')).toMatchObject({ status: 'down', statusLabel: 'Down', detail: 'Stopped 2026-10-23T05:00:00Z (from its log)' })
    expect(nodes.get('saxo')).toMatchObject({ status: 'ok', statusLabel: 'Up' })
    expect(nodes.get('scheduler')).toMatchObject({ status: 'unknown', statusLabel: 'Unknown', detail: 'Not in the health summary' })
    expect(nodes.get('tradingview')?.status).toBeUndefined()
  })
})
