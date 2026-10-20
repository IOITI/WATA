import { chartTime } from '@/features/prices/chartData'
import type { ChartOverlay, OverlayPoint } from '@/features/prices/chartData'
import type { BacktestTrade, PriceBarRow } from '@/lib/types'

/**
 * One trade on the chart of the turbo it bought: the levels that could have
 * sold it, drawn over the bid while it was held. Pure.
 */

export interface ExitSettings {
  stoploss_percent: number
  max_profit_percent: number
  trailing_enabled: boolean
  trailing_activation_percent: number
  trailing_drawdown_percent: number
  day_profit_target: number
}

export function exitSettings(settings: Record<string, unknown>): ExitSettings {
  const number = (name: string, fallback: number) => (typeof settings[name] === 'number' ? (settings[name] as number) : fallback)
  return {
    stoploss_percent: number('stoploss_percent', -15),
    max_profit_percent: number('max_profit_percent', 60),
    trailing_enabled: settings.trailing_enabled === true,
    trailing_activation_percent: number('trailing_activation_percent', 5),
    trailing_drawdown_percent: number('trailing_drawdown_percent', 8),
    day_profit_target: number('day_profit_target', 1.7),
  }
}

export interface TradeLevel {
  id: 'stop' | 'take_profit' | 'day_target'
  label: string
  price: number
  percent: number
}

/** The fixed levels of a trade: its stop-loss, its take-profit, and where the day's target was met. */
export function tradeLevels(trade: BacktestTrade, settings: ExitSettings): TradeLevel[] {
  const open = trade.entry.price
  const needed = ((1 + settings.day_profit_target / 100) / (1 + trade.day_percent_before / 100) - 1) * 100
  return [
    { id: 'stop', label: `Stop-loss ${settings.stoploss_percent} %`, price: open * (1 + settings.stoploss_percent / 100), percent: settings.stoploss_percent },
    {
      id: 'take_profit', label: `Take-profit +${settings.max_profit_percent} %`,
      price: open * (1 + settings.max_profit_percent / 100), percent: settings.max_profit_percent,
    },
    { id: 'day_target', label: `Day's target (+${needed.toFixed(2)} % for this trade)`, price: open * (1 + needed / 100), percent: needed },
  ]
}

function performance(open: number, bid: number): number {
  return Math.round((bid * 100 / open - 100) * 100) / 100
}

/**
 * The trailing stop as the monitor moved it: armed once the best result
 * reached its activation, then the best less its drawdown, at each ten
 * seconds the trade was held. Null where it was not armed.
 */
export function trailingLevel(
  trade: BacktestTrade,
  bars: readonly PriceBarRow[],
  settings: ExitSettings,
  timeZone: string,
): OverlayPoint[] {
  if (!settings.trailing_enabled) return []
  const open = trade.entry.price
  const opened = Date.parse(trade.entry.filled_at) / 1000
  const closed = Date.parse(trade.exit.filled_at) / 1000
  const points: OverlayPoint[] = []
  let best = 0
  for (const bar of bars) {
    const start = bar[0]
    if (start + 10 <= opened - 10 || start > closed) continue
    best = Math.max(best, performance(open, bar[2]))
    if (best >= settings.trailing_activation_percent) {
      points.push({ time: chartTime(start, timeZone), value: open * (1 + (best - settings.trailing_drawdown_percent) / 100) })
    }
  }
  return points
}

/**
 * The lines drawn over the turbo's bid for a trade: what it was bought at,
 * its stop, the day's target, its take-profit when it is near enough to be
 * seen without flattening the prices, and the trailing stop as it moved.
 */
export function tradeOverlays(
  trade: BacktestTrade,
  bars: readonly PriceBarRow[],
  settings: ExitSettings,
  timeZone: string,
): ChartOverlay[] {
  const opened = Date.parse(trade.entry.filled_at) / 1000
  const closed = Date.parse(trade.exit.filled_at) / 1000
  const held = bars.filter((bar) => bar[0] + 10 > opened - 10 && bar[0] <= closed)
  if (held.length === 0) return []
  const times = held.map((bar) => chartTime(bar[0], timeZone))
  const line = (id: string, price: number, tone: ChartOverlay['tone']): ChartOverlay => ({
    id, tone, points: times.map((time) => ({ time, value: price })),
  })
  const lows = Math.min(...held.map((bar) => bar[3]))
  const highs = Math.max(...held.map((bar) => bar[2]))
  const span = Math.max(highs - lows, trade.entry.price * 0.02)
  const near = (price: number) => price >= lows - 2 * span && price <= highs + 2 * span
  const overlays = [line('bought', trade.entry.price, 'reference')]
  for (const level of tradeLevels(trade, settings)) {
    if (level.id === 'take_profit' && !near(level.price)) continue
    overlays.push(line(level.id, level.price, 'band'))
  }
  const trailing = trailingLevel(trade, held, settings, timeZone)
  if (trailing.length > 0) overlays.push({ id: 'trailing', tone: 'trend', points: trailing })
  return overlays
}

/** The result of a trade at each ten seconds it was held, for the chart of its path. */
export function tradePath(trade: BacktestTrade, bars: readonly PriceBarRow[]): { t: number; value: number }[] {
  const opened = Date.parse(trade.entry.filled_at) / 1000
  const closed = Date.parse(trade.exit.filled_at) / 1000
  return bars
    .filter((bar) => bar[0] + 10 > opened - 10 && bar[0] <= closed)
    .map((bar) => ({ t: bar[0], value: performance(trade.entry.price, bar[4]) }))
}
