/**
 * WATA's services drawn as a map (System page): what calls what, each service with the health the admin reads of
 * it (`src/admin/health.py`). The links follow AGENTS.md's service map: a service added, or a link changed, is
 * changed here too. A link goes from the caller to what it calls or writes to.
 */

import { HEALTH_STATUS_LABEL } from 'ioiti-ui'
import type { TraceConnection, TraceNode } from 'ioiti-ui'

import type { ServiceHealth } from '@/lib/types'

export interface MapNode {
  id: string
  label: string
  /** The id of its entry in the health summary; none for what is outside WATA (and for the admin itself). */
  health?: string
  x: number
  y: number
}

export const MAP_WIDTH = 760
export const MAP_HEIGHT = 330

const COLUMNS = [70, 225, 380, 535, 690]
const ROWS = [55, 160, 265]

/** Left to right as a signal goes: where it comes from, the services, what they reach. */
export const MAP_NODES: MapNode[] = [
  { id: 'tradingview', label: 'TradingView', x: COLUMNS[0], y: ROWS[0] },
  { id: 'admin', label: 'Admin', x: COLUMNS[0], y: ROWS[1] },
  { id: 'capital', label: 'Capital.com', x: COLUMNS[0], y: ROWS[2] },
  { id: 'web_server', label: 'Web server', health: 'web_server', x: COLUMNS[1], y: ROWS[0] },
  { id: 'postgres', label: 'PostgreSQL', health: 'postgres', x: COLUMNS[1], y: ROWS[1] },
  { id: 'capital_feed', label: 'Capital.com feed', health: 'capital_feed', x: COLUMNS[1], y: ROWS[2] },
  { id: 'trader', label: 'Trader', health: 'trader', x: COLUMNS[2], y: ROWS[0] },
  { id: 'scheduler', label: 'Scheduler', health: 'scheduler', x: COLUMNS[2], y: ROWS[1] },
  { id: 'ta_engine', label: 'TA engine', health: 'ta_engine', x: COLUMNS[2], y: ROWS[2] },
  { id: 'position_monitor', label: 'Position monitor', health: 'position_monitor', x: COLUMNS[3], y: ROWS[0] },
  { id: 'rabbitmq', label: 'RabbitMQ', health: 'rabbitmq', x: COLUMNS[3], y: ROWS[1] },
  { id: 'price_recorder', label: 'Price recorder', health: 'price_recorder', x: COLUMNS[3], y: ROWS[2] },
  { id: 'saxo', label: 'Saxo', health: 'saxo', x: COLUMNS[4], y: ROWS[0] },
  { id: 'watchlist_manager', label: 'Watchlist', health: 'watchlist_manager', x: COLUMNS[4], y: ROWS[1] },
  { id: 'telegram', label: 'Telegram', health: 'telegram', x: COLUMNS[4], y: ROWS[2] },
]

export const MAP_LINKS: TraceConnection[] = [
  { from: 'tradingview', to: 'web_server', label: 'webhook' },
  { from: 'trader', to: 'web_server', label: '/latest-signals' },
  { from: 'scheduler', to: 'web_server', label: 'close, /internal/signal' },
  { from: 'ta_engine', to: 'web_server', label: 'signals, /internal/signal' },
  { from: 'trader', to: 'saxo', label: 'orders' },
  { from: 'trader', to: 'watchlist_manager', label: 'turbos' },
  { from: 'trader', to: 'postgres', label: 'trades, journal' },
  { from: 'trader', to: 'rabbitmq', label: 'closes handed over' },
  { from: 'position_monitor', to: 'saxo', label: 'stream, sells' },
  { from: 'position_monitor', to: 'rabbitmq', label: 'trading-ops' },
  { from: 'position_monitor', to: 'postgres', label: 'positions' },
  { from: 'scheduler', to: 'rabbitmq', label: 'daily_stats' },
  { from: 'watchlist_manager', to: 'saxo', label: 'quotes' },
  { from: 'price_recorder', to: 'saxo', label: 'stream' },
  { from: 'price_recorder', to: 'postgres', label: 'bars' },
  { from: 'capital_feed', to: 'capital', label: 'stream' },
  { from: 'capital_feed', to: 'postgres', label: 'bars' },
  { from: 'capital_feed', to: 'rabbitmq', label: 'wata.prices' },
  { from: 'ta_engine', to: 'postgres', label: 'bars, journal' },
  { from: 'telegram', to: 'rabbitmq', label: 'telegram_channel' },
  { from: 'admin', to: 'postgres', label: 'read only' },
  { from: 'admin', to: 'web_server', label: 'signals' },
  { from: 'admin', to: 'rabbitmq', label: 'ops, prices' },
  { from: 'admin', to: 'watchlist_manager', label: 'watchlist' },
]

/** The map's nodes with their health: a service the summary does not report is unknown. */
export function serviceMap(services: ServiceHealth[]): TraceNode[] {
  const byId = new Map(services.map((service) => [service.id, service]))
  return MAP_NODES.map((node) => {
    if (!node.health) return { id: node.id, label: node.label, x: node.x, y: node.y }
    const service = byId.get(node.health)
    const status = service?.status ?? 'unknown'
    return {
      id: node.id,
      label: node.label,
      x: node.x,
      y: node.y,
      status,
      statusLabel: HEALTH_STATUS_LABEL[status],
      detail: service ? service.detail : 'Not in the health summary',
    }
  })
}
