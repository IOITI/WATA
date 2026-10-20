import type { ChartMarker, Locator, MarkerTone } from '@/features/prices/chartData'
import type { BacktestFate, BacktestSignal, BacktestTrade } from '@/lib/types'

/**
 * A day of a run on the chart of its asset: where each signal was raised and
 * what became of it, where each trade was bought and sold. Pure: the page
 * gives the locator of the bars it drew.
 */

const FATE_TONE: Record<BacktestFate, MarkerTone> = {
  executed: 'entry',
  refused: 'neutral',
  failed: 'loss',
  unpriced: 'warning',
  closed: 'neutral',
  ignored: 'neutral',
  not_a_signal: 'neutral',
}

const ACTION_TEXT: Record<string, string> = {
  long: 'Long',
  short: 'Short',
  'close-position': 'Close',
  'close-long': 'Close long',
  'close-short': 'Close short',
}

function instant(iso: string | null | undefined): number | null {
  if (!iso) return null
  const value = Date.parse(iso) / 1000
  return Number.isFinite(value) ? value : null
}

/** The moment a signal belongs to on the chart: the bar it was raised on, or when it reached the trader. */
export function signalMoment(signal: BacktestSignal): number | null {
  return instant(signal.bar_time) ?? instant(signal.arrived_at) ?? instant(signal.bar_closed_at)
}

/**
 * Every signal of the day at the bar it belongs to: entries under the bars
 * when long and above when short, closes above. The scheduler's close of the
 * day is left out unless it closed something: it is the same every day.
 */
export function signalMarkers(signals: readonly BacktestSignal[], locate: Locator): ChartMarker[] {
  const markers: ChartMarker[] = []
  for (const signal of signals) {
    if (signal.source === 'schedule' && signal.fate !== 'closed') continue
    const moment = signalMoment(signal)
    const time = moment === null ? null : locate(moment)
    if (time === null) continue
    const action = signal.action ?? ''
    const entry = action === 'long' || action === 'short'
    markers.push({
      id: `signal-${signal.id}`,
      time,
      shape: action === 'long' ? 'arrowUp' : action === 'short' ? 'arrowDown' : 'circle',
      tone: FATE_TONE[signal.fate],
      text: `${ACTION_TEXT[action] ?? (signal.source === 'schedule' ? 'Day close' : 'Alert')}${entry && signal.fate !== 'executed' ? ' ✕' : ''}`,
      position: action === 'long' ? 'belowBar' : 'aboveBar',
    })
  }
  return markers.sort((first, second) => first.time - second.time)
}

/** Each trade of the day: its purchase, and its sale coloured by what it made. */
export function tradeMarkers(trades: readonly BacktestTrade[], locate: Locator, atPrice = false): ChartMarker[] {
  const markers: ChartMarker[] = []
  for (const trade of trades) {
    const opened = instant(trade.entry.filled_at)
    const closed = instant(trade.exit.filled_at)
    const openTime = opened === null ? null : locate(opened)
    const closeTime = closed === null ? null : locate(closed)
    if (openTime !== null) {
      markers.push(
        atPrice
          ? { id: `buy-${trade.id}`, time: openTime, shape: 'arrowUp', tone: 'entry', text: `Bought ${trade.entry.price}`, position: 'atPriceBottom', price: trade.entry.price }
          : {
              id: `buy-${trade.id}`, time: openTime, shape: trade.direction === 'long' ? 'arrowUp' : 'arrowDown', tone: 'entry',
              text: `#${trade.id} ${trade.direction}`, position: trade.direction === 'long' ? 'belowBar' : 'aboveBar',
            },
      )
    }
    if (closeTime !== null) {
      const tone: MarkerTone = trade.performance_percent > 0 ? 'profit' : trade.performance_percent < 0 ? 'loss' : 'neutral'
      const result = `${trade.performance_percent > 0 ? '+' : ''}${trade.performance_percent.toFixed(2)} %`
      markers.push(
        atPrice
          ? { id: `sell-${trade.id}`, time: closeTime, shape: 'arrowDown', tone, text: `Sold ${trade.exit.bid} (${result})`, position: 'atPriceTop', price: trade.exit.bid }
          : { id: `sell-${trade.id}`, time: closeTime, shape: 'circle', tone, text: result, position: 'aboveBar' },
      )
    }
  }
  return markers.sort((first, second) => first.time - second.time)
}

/**
 * The stretch of the chart a trade is brought into view with: from a little
 * before its purchase to a little after its sale, in chart times.
 */
export function tradeFocus(trade: BacktestTrade, locate: Locator, marginSeconds = 900): { from: number; to: number } | null {
  const opened = instant(trade.entry.filled_at)
  const closed = instant(trade.exit.filled_at)
  if (opened === null || closed === null) return null
  // The bars drawn around the trade: the margin may fall where no bar is, so it is looked for nearer.
  const near = (moment: number, step: number) => {
    for (let offset = 0; offset <= marginSeconds; offset += 60) {
      const found = locate(moment + step * (marginSeconds - offset))
      if (found !== null) return found
    }
    return locate(moment)
  }
  const from = near(opened, -1)
  const to = near(closed, 1)
  return from !== null && to !== null ? { from: Math.min(from, to), to: Math.max(from, to) } : null
}
