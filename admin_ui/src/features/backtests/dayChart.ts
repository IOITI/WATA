import { placeRangeBars, rangeCandles, rangeLocator } from '@/features/prices/chartData'
import type { Candle, ChartMarker, ChartOverlay, EmptyPoint, OverlayPoint } from '@/features/prices/chartData'
import { formatPercent } from '@/lib/format'
import type { BacktestDayChart, PriceRangeRow } from '@/lib/types'

/**
 * One day of a backtest as the price chart takes it: the range bars the
 * strategy saw, what it measured on each as lines over them, and what became
 * of its decisions as marks. Nothing here draws.
 *
 * A mark says little on purpose: several often sit on neighbouring bars, and
 * the reasons are listed under the chart with their times.
 */

/** A range bar with what the strategy measured on it. */
export interface DayBar {
  row: PriceRangeRow
  kernel: number | null
  vwap: number | null
  upper: number | null
  lower: number | null
  entriesAllowed: boolean
}

export interface DayView {
  /** Names the view drawn: when it changes, the whole day is brought back into view. */
  key: string
  candles: (Candle | EmptyPoint)[]
  overlays: ChartOverlay[]
  markers: ChartMarker[]
  /** The bar drawn at a chart time. */
  barAt: Map<number, DayBar>
  /** The chart time of the last bar: what is read when nothing is pointed at. */
  lastTime: number | null
  decimals: number
  /** The chart time of the bar that was growing at a moment (epoch seconds); null when none was. */
  locate: (moment: number) => number | null
}

/** A trade of the day as the stretch of its chart to bring into view: itself, and as much again around it. */
export function tradeFocus(view: DayView, trade: { entry_time: string; exit_time: string }): { from: number; to: number } | null {
  const entered = view.locate(epoch(trade.entry_time))
  const left = view.locate(epoch(trade.exit_time)) ?? entered
  if (entered === null || left === null) return null
  // A trade of a few seconds is still shown with the minutes around it.
  const around = Math.max(TRADE_SURROUNDINGS_SECONDS, (left - entered) / 2)
  return { from: entered - around, to: left + around }
}

const TRADE_SURROUNDINGS_SECONDS = 600

function epoch(iso: string): number {
  return Date.parse(iso) / 1000
}

export function dayView(chart: BacktestDayChart): DayView {
  const placed = placeRangeBars(chart.bars, chart.timezone)
  // A bar the chart refused (the night the clocks go back) takes its figures with it.
  const indexOf = new Map(chart.bars.map((row, index) => [row, index]))

  const line = (values: readonly (number | null)[], trend = false): (OverlayPoint | EmptyPoint)[] => {
    let previous: number | null = null
    return placed.map(({ time, row }) => {
      const value = values[indexOf.get(row) ?? -1] ?? null
      if (value === null) return { time }
      const point: OverlayPoint = trend && previous !== null && value !== previous ? { time, value, rising: value > previous } : { time, value }
      previous = value
      return point
    })
  }

  const barAt = new Map<number, DayBar>()
  for (const { time, row } of placed) {
    const index = indexOf.get(row) ?? -1
    barAt.set(time, {
      row,
      kernel: chart.kernel[index] ?? null,
      vwap: chart.vwap[index] ?? null,
      upper: chart.upper[index] ?? null,
      lower: chart.lower[index] ?? null,
      entriesAllowed: chart.entries_allowed[index] ?? false,
    })
  }

  const locate = rangeLocator(placed)
  const markers: ChartMarker[] = []
  const closedAt = new Set<number>()
  chart.trades.forEach((trade, number) => {
    const long = trade.direction === 'long'
    const entered = locate(epoch(trade.entry_time))
    if (entered !== null) {
      markers.push({
        id: `entry-${number}`,
        time: entered,
        shape: long ? 'arrowUp' : 'arrowDown',
        position: long ? 'belowBar' : 'aboveBar',
        tone: 'entry',
        text: `${trade.direction} · ${trade.entry_reason}`,
      })
    }
    closedAt.add(epoch(trade.exit_time))
    const left = locate(epoch(trade.exit_time))
    if (left !== null) {
      markers.push({
        id: `exit-${number}`,
        time: left,
        shape: 'circle',
        position: long ? 'aboveBar' : 'belowBar',
        tone: trade.performance_percent > 0 ? 'profit' : 'loss',
        text: formatPercent(trade.performance_percent, true),
      })
    }
  })
  chart.refusals.forEach((refusal, number) => {
    const time = locate(epoch(refusal.time))
    if (time === null) return
    markers.push({
      id: `refusal-${number}`,
      time,
      shape: 'circle',
      position: refusal.direction === 'long' ? 'belowBar' : 'aboveBar',
      tone: 'warning',
      text: 'refused',
    })
  })
  // An entry the strategy decided is a trade or a refusal above. Going flat is
  // only worth a mark of its own when nothing was open to sell.
  chart.decisions.forEach((decision, number) => {
    if (decision.target !== 'flat' || closedAt.has(decision.at)) return
    const time = locate(decision.at)
    if (time === null) return
    markers.push({
      id: `flat-${number}`,
      time,
      shape: 'circle',
      position: 'aboveBar',
      tone: 'neutral',
      text: 'flat',
    })
  })
  markers.sort((a, b) => a.time - b.time)

  return {
    key: `${chart.id}|${chart.day}`,
    candles: rangeCandles(placed),
    overlays: [
      { id: 'upper', tone: 'band', points: line(chart.upper) },
      { id: 'lower', tone: 'band', points: line(chart.lower) },
      { id: 'vwap', tone: 'reference', points: line(chart.vwap) },
      { id: 'kernel', tone: 'trend', points: line(chart.kernel, true) },
    ],
    markers,
    barAt,
    lastTime: placed.length > 0 ? placed[placed.length - 1].time : null,
    decimals: chart.decimals,
    locate,
  }
}
