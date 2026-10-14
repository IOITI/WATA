import type { CsvColumn } from '@/lib/csv'
import type { PriceBarRow, PriceBars, PriceInstrument, PriceRangeBars, PriceRangeRow } from '@/lib/types'

import {
  barsByChartTime,
  chartTime,
  chartTimeIso,
  drawnTimes,
  instrumentLabel,
  intervalLabel,
  placeRangeBars,
  priceDecimals,
  RANGE,
  rangeCandles,
  rangeIndexLine,
  rangeLocator,
  timeLocator,
  toCandles,
  toLine,
  valueDecimals,
} from './chartData'
import type { Candle, EmptyPoint, LinePoint, Locator, Side } from './chartData'

/**
 * What the page draws, whichever kind of bars it was asked for: bars of a
 * fixed length, or range bars. The chart, the markers and the legend only
 * know the common part; the readout and the table read the rest.
 */

/** Bars of a fixed length as they are asked for: the prices of an instrument and, under a turbo, those of its index. */
export interface TimeBars {
  prices: PriceBars
  index: PriceBars | null
}

interface ChartBase {
  /** Names the view drawn. When it changes, the whole period is brought back into view. */
  key: string
  instrument: PriceInstrument
  timeZone: string
  side: Side
  candles: (Candle | EmptyPoint)[]
  /** Something is drawn (an instrument without an ask has bars and nothing to draw of it). */
  hasCandles: boolean
  /** Bars were received at all. */
  hasBars: boolean
  decimals: number
  /** Under a turbo, its index: a line on the same times. */
  indexLine?: (LinePoint | EmptyPoint)[]
  indexName?: string
  indexDecimals: number
  /** The time axis shows seconds. */
  showSeconds: boolean
  /** Where a moment falls on the prices, and on the index line. */
  locate: Locator
  indexLocate?: Locator
  /** The chart time of the last bar: what is read when nothing is pointed at. */
  lastTime: number | null
  /** The bars in a few words, for the chart's description: "1 min bars", "range bars of 0.60". */
  barsLabel: string
}

export interface TimeChart extends ChartBase {
  kind: 'time'
  interval: number
  bars: PriceBarRow[]
  barAt: Map<number, PriceBarRow>
  indexBarAt: Map<number, PriceBarRow>
}

export interface RangeChart extends ChartBase {
  kind: 'range'
  /** One step of the prices, and the range in steps (R) and as a price. */
  tick: number
  ticks: number
  size: number
  rows: PriceRangeRow[]
  rowAt: Map<number, PriceRangeRow>
}

export type ChartModel = TimeChart | RangeChart

export function timeChart({ prices, index }: TimeBars, side: Side): TimeChart {
  const { bars, interval, timezone: timeZone } = prices
  const candles = toCandles(bars, side, interval, timeZone)
  const drawn = drawnTimes(candles)
  const indexBars = index?.bars ?? []
  const indexLine = indexBars.length > 0 ? toLine(indexBars, interval, timeZone) : undefined
  const last = bars[bars.length - 1]

  return {
    kind: 'time',
    key: `${prices.instrument.ref}|${prices.from}|${prices.to}|${interval}|${side}`,
    instrument: prices.instrument,
    timeZone,
    side,
    candles,
    hasCandles: drawn.size > 0,
    hasBars: bars.length > 0,
    decimals: priceDecimals(bars),
    indexLine,
    indexName: indexLine && index ? instrumentLabel(index.instrument) : undefined,
    indexDecimals: priceDecimals(indexBars),
    showSeconds: interval < 60,
    locate: timeLocator(interval, timeZone, drawn),
    indexLocate: indexLine ? timeLocator(interval, timeZone, drawnTimes(indexLine)) : undefined,
    lastTime: last ? chartTime(last[0], timeZone) : null,
    barsLabel: `${intervalLabel(interval)} bars`,
    interval,
    bars,
    barAt: barsByChartTime(bars, timeZone),
    indexBarAt: barsByChartTime(indexBars, timeZone),
  }
}

export function rangeChart(data: PriceRangeBars): RangeChart {
  const { bars: rows, timezone: timeZone } = data
  const placed = placeRangeBars(rows, timeZone)
  const locate = rangeLocator(placed)
  // The index comes with the bars, one level per bar: a line only when there is one to draw.
  const hasIndex = data.under !== null && rows.some((row) => row[RANGE.index] !== null)
  const indexLine = hasIndex ? rangeIndexLine(placed) : undefined
  const indexTimes = indexLine ? drawnTimes(indexLine) : undefined
  const last = placed[placed.length - 1]

  return {
    kind: 'range',
    key: `${data.instrument.ref}|${data.from}|${data.to}|range|${data.size}|${data.side}`,
    instrument: data.instrument,
    timeZone,
    side: data.side,
    candles: rangeCandles(placed),
    hasCandles: placed.length > 0,
    hasBars: rows.length > 0,
    decimals: data.decimals,
    indexLine,
    indexName: indexLine && data.under ? instrumentLabel(data.under) : undefined,
    indexDecimals: valueDecimals(rows.map((row) => row[RANGE.index])),
    // Several range bars can be born in the same ten seconds.
    showSeconds: true,
    locate,
    indexLocate: indexTimes
      ? (instant) => {
          const time = locate(instant)
          return time !== null && indexTimes.has(time) ? time : null
        }
      : undefined,
    lastTime: last ? last.time : null,
    barsLabel: `range bars of ${data.size.toFixed(data.decimals)}`,
    tick: data.tick,
    ticks: data.ticks,
    size: data.size,
    rows,
    rowAt: new Map(placed.map((bar) => [bar.time, bar.row])),
  }
}

// ── as a file ──

export function timeBarsCsv(timeZone: string): CsvColumn<PriceBarRow>[] {
  return [
    { header: 'time_utc', value: (row) => new Date(row[0] * 1000).toISOString() },
    { header: 'time_local', value: (row) => chartTimeIso(chartTime(row[0], timeZone)) },
    { header: 'bid_open', value: (row) => row[1] },
    { header: 'bid_high', value: (row) => row[2] },
    { header: 'bid_low', value: (row) => row[3] },
    { header: 'bid_close', value: (row) => row[4] },
    { header: 'ask_open', value: (row) => row[5] },
    { header: 'ask_high', value: (row) => row[6] },
    { header: 'ask_low', value: (row) => row[7] },
    { header: 'ask_close', value: (row) => row[8] },
    { header: 'updates', value: (row) => row[9] },
  ]
}

/** The times in the file are when a bar really began and ended, not the place the chart gave it. */
export function rangeBarsCsv(timeZone: string): CsvColumn<PriceRangeRow>[] {
  return [
    { header: 'began_utc', value: (row) => new Date(row[RANGE.began] * 1000).toISOString() },
    { header: 'ended_utc', value: (row) => new Date(row[RANGE.ended] * 1000).toISOString() },
    { header: 'began_local', value: (row) => chartTimeIso(chartTime(row[RANGE.began], timeZone)) },
    { header: 'ended_local', value: (row) => chartTimeIso(chartTime(row[RANGE.ended], timeZone)) },
    { header: 'open', value: (row) => row[RANGE.open] },
    { header: 'high', value: (row) => row[RANGE.high] },
    { header: 'low', value: (row) => row[RANGE.low] },
    { header: 'close', value: (row) => row[RANGE.close] },
    { header: 'seconds', value: (row) => row[RANGE.ended] - row[RANGE.began] },
    { header: 'complete', value: (row) => (row[RANGE.complete] ? 'yes' : 'no') },
    { header: 'after_pause', value: (row) => (row[RANGE.afterPause] ? 'yes' : 'no') },
    { header: 'index', value: (row) => row[RANGE.index] },
  ]
}

/** What became of a range bar: it reached its range, is still growing, or was cut short by a pause in the data. */
export function rangeBarState(row: PriceRangeRow, isLast: boolean): 'complete' | 'growing' | 'cut' {
  if (row[RANGE.complete]) return 'complete'
  return isLast ? 'growing' : 'cut'
}
