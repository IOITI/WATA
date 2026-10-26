// A real position on the chart of the turbo it bought, as the price recorder kept it (the backtests draw their
// trades the same way, features/backtests/tradeChart.ts): the signal that opened it, where it was bought and
// sold, and over the bid while it was held, the price paid, the best and worst it reached, and the level its
// close reason names. Pure: the page gives the locator of the bars it drew.

import { chartTime } from '@/features/prices/chartData'
import type { ChartMarker, ChartOverlay, Locator, MarkerTone } from '@/features/prices/chartData'
import type { Position, PositionChartRefs, PriceBarRow } from '@/lib/types'

function instant(iso: string | null | undefined): number | null {
  if (!iso) return null
  const value = Date.parse(iso) / 1000
  return Number.isFinite(value) ? value : null
}

/** The level a close reason names, as a price of the turbo: a stop-loss, a take-profit, a trailing stop's peak. */
export function reasonLevel(reason: string | null, openPrice: number | null): { label: string; price: number } | null {
  if (!reason || !openPrice) return null
  const stop = /Stoploss \((-?\d+(?:\.\d+)?)%\)/.exec(reason)
  if (stop) return { label: `Stop-loss ${stop[1]} %`, price: openPrice * (1 + Number(stop[1]) / 100) }
  const take = /Takeprofit \((-?\d+(?:\.\d+)?)%\)/.exec(reason)
  if (take) return { label: `Take-profit +${take[1]} %`, price: openPrice * (1 + Number(take[1]) / 100) }
  const peak = /Trailing Stop: peak (-?\d+(?:\.\d+)?)%/.exec(reason)
  if (peak) return { label: `Trailing stop's peak +${peak[1]} %`, price: openPrice * (1 + Number(peak[1]) / 100) }
  return null
}

/** The signal, the purchase and the sale, at their moments. */
export function positionMarkers(position: Position, signal: PositionChartRefs['signal'], locate: Locator): ChartMarker[] {
  const markers: ChartMarker[] = []
  const signalled = instant(signal?.signal_timestamp)
  const signalTime = signalled === null ? null : locate(signalled)
  if (signalTime !== null && signal) {
    const long = signal.action === 'long'
    markers.push({
      id: 'signal', time: signalTime, shape: long ? 'arrowUp' : 'arrowDown', tone: 'neutral',
      text: `Signal ${signal.action}`, position: long ? 'belowBar' : 'aboveBar',
    })
  }
  const opened = instant(position.execution_time_open)
  const openTime = opened === null ? null : locate(opened)
  if (openTime !== null && position.position_open_price !== null) {
    markers.push({
      id: 'bought', time: openTime, shape: 'arrowUp', tone: 'entry', text: `Bought ${position.position_open_price}`,
      position: 'atPriceBottom', price: position.position_open_price,
    })
  }
  const closed = instant(position.execution_time_close)
  const closeTime = closed === null ? null : locate(closed)
  const result = position.position_total_performance_percent
  if (closeTime !== null && position.position_close_price !== null && result !== null) {
    const tone: MarkerTone = result > 0 ? 'profit' : result < 0 ? 'loss' : 'neutral'
    markers.push({
      id: 'sold', time: closeTime, shape: 'arrowDown', tone,
      text: `Sold ${position.position_close_price} (${result > 0 ? '+' : ''}${result.toFixed(2)} %)`,
      position: 'atPriceTop', price: position.position_close_price,
    })
  }
  return markers
}

/** The lines over the bid while the position was held: the price paid, its best and worst, its close's level. */
export function positionOverlays(position: Position, bars: readonly PriceBarRow[], timeZone: string): ChartOverlay[] {
  const opened = instant(position.execution_time_open)
  const closed = instant(position.execution_time_close)
  const open = position.position_open_price
  if (opened === null || closed === null || !open) return []
  const held = bars.filter((bar) => bar[0] + 10 > opened - 10 && bar[0] <= closed)
  if (held.length === 0) return []
  const times = held.map((bar) => chartTime(bar[0], timeZone))
  const line = (id: string, price: number, tone: ChartOverlay['tone']): ChartOverlay => ({
    id, tone, points: times.map((time) => ({ time, value: price })),
  })
  const overlays = [line('bought', open, 'reference')]
  const best = position.position_max_performance_percent
  const worst = position.position_min_performance_percent
  if (best !== null && best > 0) overlays.push(line('best', open * (1 + best / 100), 'band'))
  if (worst !== null && worst !== undefined && worst < 0) overlays.push(line('worst', open * (1 + worst / 100), 'band'))
  const level = reasonLevel(position.position_close_reason, open)
  if (level) overlays.push(line('reason', level.price, 'trend'))
  return overlays
}

/** The stretch of the day the position is brought into view with: from a little before the signal to after the sale. */
export function positionFocus(position: Position, signal: PositionChartRefs['signal'], locate: Locator, marginSeconds = 300): { from: number; to: number } | null {
  const opened = instant(position.execution_time_open)
  const closed = instant(position.execution_time_close)
  if (opened === null || closed === null) return null
  const start = Math.min(opened, instant(signal?.signal_timestamp) ?? opened)
  const near = (moment: number, step: number) => {
    for (let offset = 0; offset <= marginSeconds; offset += 60) {
      const found = locate(moment + step * (marginSeconds - offset))
      if (found !== null) return found
    }
    return locate(moment)
  }
  const from = near(start, -1)
  const to = near(closed, 1)
  return from !== null && to !== null ? { from: Math.min(from, to), to: Math.max(from, to) } : null
}
