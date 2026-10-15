import { formatMoney, formatPercent } from '@/lib/format'
import { actionLabel } from '@/lib/labels'
import type { JournalEntry, Position, PriceBarRow, PriceInstrument, PriceRangeRow } from '@/lib/types'

/**
 * From what the server sends to what the chart draws.
 *
 * The chart library only knows UTC: to show the trading timezone, every time
 * given to it is shifted by that zone's offset (its own documentation's
 * advice). Nothing here touches the library, so all of it can be tested.
 */

/** Bar lengths the server offers, in seconds. */
export const INTERVALS = [10, 30, 60, 300, 900, 3600] as const
/** What the server gives one chart at most. */
export const MAX_BUCKETS = 12_000
/** A stretch without bars up to this long is drawn as empty time; a longer one (a night) is closed up. */
export const MAX_HOLE_SECONDS = 2 * 3600

export type Side = 'bid' | 'ask'

export function intervalLabel(seconds: number): string {
  if (seconds < 60) return `${seconds} s`
  if (seconds < 3600) return `${seconds / 60} min`
  return `${seconds / 3600} h`
}

/** The shortest bar at which a period of `days` days fits in one chart. */
export function defaultInterval(days: number): number {
  return INTERVALS.find((interval) => (days * 86_400) / interval <= MAX_BUCKETS) ?? INTERVALS[INTERVALS.length - 1]
}

/** The bar lengths a period of `days` days can be shown at. */
export function allowedIntervals(days: number): number[] {
  const shortest = defaultInterval(days)
  return INTERVALS.filter((interval) => interval >= shortest)
}

/** Whole days from one ISO day to another, both included. */
export function daysBetween(from: string, to: string): number {
  const span = Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)
  return Number.isFinite(span) ? Math.max(1, Math.round(span / 86_400_000) + 1) : 1
}

/** Today's date in a timezone, as an ISO day. */
export function todayIn(timeZone: string, now: Date = new Date()): string {
  // The Canadian format is the ISO one.
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(now)
}

export function shiftDay(day: string, days: number): string {
  const date = new Date(`${day}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

// ── timezone ──

const formatters = new Map<string, Intl.DateTimeFormat>()
const offsets = new Map<string, number>()
/** An offset only changes on a quarter of an hour: one computation serves the fifteen minutes. */
const OFFSET_STEP = 900

function formatterFor(timeZone: string): Intl.DateTimeFormat {
  let formatter = formatters.get(timeZone)
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hourCycle: 'h23',
      year: 'numeric',
      month: 'numeric',
      day: 'numeric',
      hour: 'numeric',
      minute: 'numeric',
      second: 'numeric',
    })
    formatters.set(timeZone, formatter)
  }
  return formatter
}

/** Seconds to add to a UTC instant to read it as the wall-clock time of `timeZone`. */
export function zoneOffsetSeconds(t: number, timeZone: string): number {
  const step = Math.floor(t / OFFSET_STEP)
  const key = `${timeZone}|${step}`
  const known = offsets.get(key)
  if (known !== undefined) return known

  const instant = step * OFFSET_STEP
  const parts: Record<string, number> = {}
  for (const part of formatterFor(timeZone).formatToParts(new Date(instant * 1000))) {
    if (part.type !== 'literal') parts[part.type] = Number(part.value)
  }
  const wallClock = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second) / 1000
  const offset = wallClock - instant
  offsets.set(key, offset)
  return offset
}

/** A UTC instant as the chart must be given it to show the time of `timeZone`. */
export function chartTime(t: number, timeZone: string): number {
  return t + zoneOffsetSeconds(t, timeZone)
}

// ── series ──

export interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
}
export interface LinePoint {
  time: number
  value: number
}
/** A time with nothing at it: the chart leaves it empty. */
export interface EmptyPoint {
  time: number
}

/**
 * A line drawn over the prices, on their scale: something measured on them.
 * "trend" is told by its colour as well (rising or falling at each point),
 * "reference" is a level the prices are compared with, "band" a limit around it.
 */
export type OverlayTone = 'trend' | 'reference' | 'band'
export interface OverlayPoint extends LinePoint {
  /** For a trend: whether the line was rising at this point. */
  rising?: boolean
}
export interface ChartOverlay {
  id: string
  tone: OverlayTone
  points: readonly (OverlayPoint | EmptyPoint)[]
}

function build<T extends { time: number }>(
  bars: readonly PriceBarRow[],
  interval: number,
  timeZone: string,
  point: (row: PriceBarRow, time: number) => T | null,
): (T | EmptyPoint)[] {
  const points: (T | EmptyPoint)[] = []
  let lastTime = Number.NEGATIVE_INFINITY
  let previous: number | null = null

  // The library refuses a time that does not come after the one before. On the
  // night the clocks go back, an hour of wall-clock times repeats: those are left out.
  const push = (item: T | EmptyPoint) => {
    if (item.time > lastTime) {
      points.push(item)
      lastTime = item.time
    }
  }

  for (const row of bars) {
    const t = row[0]
    const item = point(row, chartTime(t, timeZone))
    if (item === null) continue
    if (previous !== null) {
      const missing = t - previous - interval
      if (missing > 0 && missing <= MAX_HOLE_SECONDS) {
        for (let hole = previous + interval; hole < t; hole += interval) push({ time: chartTime(hole, timeZone) })
      }
    }
    push(item)
    previous = t
  }
  return points
}

/** Candles of one side. A bar without that side (an instrument with no ask) is left out. */
export function toCandles(
  bars: readonly PriceBarRow[],
  side: Side,
  interval: number,
  timeZone: string,
): (Candle | EmptyPoint)[] {
  const first = side === 'bid' ? 1 : 5
  return build(bars, interval, timeZone, (row, time) => {
    const [open, high, low, close] = [row[first], row[first + 1], row[first + 2], row[first + 3]]
    if (open === null || high === null || low === null || close === null) return null
    return { time, open, high, low, close }
  })
}

/** The close of the bid, as a line: the index under a turbo. */
export function toLine(bars: readonly PriceBarRow[], interval: number, timeZone: string): (LinePoint | EmptyPoint)[] {
  return build(bars, interval, timeZone, (row, time) => ({ time, value: row[4] }))
}

/** The chart times that carry a value: where a marker can go. */
export function drawnTimes(points: readonly (Candle | LinePoint | EmptyPoint)[]): Set<number> {
  const times = new Set<number>()
  for (const point of points) {
    if ('open' in point || 'value' in point) times.add(point.time)
  }
  return times
}

const MIN_DECIMALS = 2
const MAX_DECIMALS = 5

/** Decimals the prices are quoted with (a turbo under one euro has three), read from the first bars. */
export function priceDecimals(bars: readonly PriceBarRow[]): number {
  let decimals = MIN_DECIMALS
  for (const row of bars.slice(0, 200)) {
    for (const value of [row[4], row[8]]) {
      if (value === null) continue
      const text = String(value)
      const point = text.indexOf('.')
      if (point !== -1 && !text.includes('e')) decimals = Math.max(decimals, text.length - point - 1)
    }
  }
  return Math.min(decimals, MAX_DECIMALS)
}

/** The same for a plain list of values (the index beside range bars). */
export function valueDecimals(values: Iterable<number | null>): number {
  let decimals = MIN_DECIMALS
  for (const value of values) {
    if (value === null) continue
    const text = String(value)
    const point = text.indexOf('.')
    if (point !== -1 && !text.includes('e')) decimals = Math.max(decimals, text.length - point - 1)
    if (decimals >= MAX_DECIMALS) return MAX_DECIMALS
  }
  return decimals
}

const chartTimeFormat = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'UTC',
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

/** A chart time as it reads on the chart's own axis: the wall clock of the trading timezone. */
export function formatChartTime(time: number): string {
  return chartTimeFormat.format(new Date(time * 1000))
}

/** The same, as a spreadsheet takes it: "2026-10-12 10:00:10". */
export function chartTimeIso(time: number): string {
  return new Date(time * 1000).toISOString().slice(0, 19).replace('T', ' ')
}

/** A "#rrggbb" colour made see-through. The canvas the chart draws on knows no CSS variable. */
export function withAlpha(hex: string, alpha: number): string {
  const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex)
  if (!match) return hex
  const [red, green, blue] = match.slice(1).map((part) => Number.parseInt(part, 16))
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`
}

/** The bar shown at each chart time, for the readout that follows the pointer. */
export function barsByChartTime(bars: readonly PriceBarRow[], timeZone: string): Map<number, PriceBarRow> {
  const byTime = new Map<number, PriceBarRow>()
  for (const row of bars) {
    const time = chartTime(row[0], timeZone)
    if (!byTime.has(time)) byTime.set(time, row)
  }
  return byTime
}

// ── markers ──

/** What a marker says; the chart gives each its colour. */
export type MarkerTone = 'entry' | 'profit' | 'loss' | 'neutral' | 'warning'

interface MarkerBase {
  id: string
  time: number
  shape: 'circle' | 'arrowUp' | 'arrowDown'
  tone: MarkerTone
  text: string
}

/** A mark on a bar: beside it, or at a price of its scale. */
export type ChartMarker =
  | (MarkerBase & { position: 'aboveBar' | 'belowBar' })
  | (MarkerBase & { position: 'atPriceTop' | 'atPriceBottom'; price: number })

/**
 * Where a moment (epoch seconds, UTC) falls on the chart: the time of the bar
 * it belongs to, or null when no bar is drawn for it. A marker goes on a bar
 * or not at all.
 */
export type Locator = (instant: number) => number | null

/** On bars of a fixed length: the bar of the bucket the moment falls in, when one is drawn. */
export function timeLocator(interval: number, timeZone: string, shown: ReadonlySet<number>): Locator {
  return (instant) => {
    const time = chartTime(Math.floor(instant / interval) * interval, timeZone)
    return shown.has(time) ? time : null
  }
}

function markerTime(iso: string | null | undefined, locate: Locator): number | null {
  if (!iso) return null
  const instant = Date.parse(iso) / 1000
  return Number.isFinite(instant) ? locate(instant) : null
}

function byTime(markers: ChartMarker[]): ChartMarker[] {
  return markers.sort((a, b) => a.time - b.time)
}

/**
 * Where WATA bought and sold. On the turbo's own chart the marks sit at the
 * prices paid and received; on the index chart, whose prices are another
 * thing altogether, they only say when.
 */
export function tradeMarkers(trades: readonly Position[], atPrice: boolean, locate: Locator): ChartMarker[] {
  const markers: ChartMarker[] = []
  for (const trade of trades) {
    const direction = actionLabel(trade.action)
    const entry = markerTime(trade.execution_time_open, locate)
    if (entry !== null) {
      markers.push(
        atPrice && trade.position_open_price !== null
          ? {
              id: `${trade.position_id}-in`,
              time: entry,
              position: 'atPriceBottom',
              price: trade.position_open_price,
              shape: 'arrowUp',
              tone: 'entry',
              text: `Buy ${formatMoney(trade.position_open_price, trade.instrument_currency)}`,
            }
          : {
              id: `${trade.position_id}-in`,
              time: entry,
              position: 'belowBar',
              shape: 'arrowUp',
              tone: 'entry',
              text: direction,
            },
      )
    }
    const exit = markerTime(trade.execution_time_close, locate)
    if (exit !== null) {
      const percent = trade.position_total_performance_percent
      const result = percent === null ? 'Sold' : formatPercent(percent, true)
      const tone: MarkerTone = percent === null || percent === 0 ? 'neutral' : percent > 0 ? 'profit' : 'loss'
      markers.push(
        atPrice && trade.position_close_price !== null
          ? {
              id: `${trade.position_id}-out`,
              time: exit,
              position: 'atPriceTop',
              price: trade.position_close_price,
              shape: 'arrowDown',
              tone,
              text: result,
            }
          : {
              id: `${trade.position_id}-out`,
              time: exit,
              position: 'aboveBar',
              shape: 'arrowDown',
              tone,
              text: `${direction} ${result}`,
            },
      )
    }
  }
  return byTime(markers)
}

const SIGNAL_OUTCOME: Record<string, string> = {
  executed: '',
  closed: '',
  refused: ' refused',
  failed: ' failed',
  ignored: ' ignored',
}

/** The signals the trader handled, labelled with what it did when that was not simply to act on them. */
export function signalMarkers(signals: readonly JournalEntry[], locate: Locator): ChartMarker[] {
  const markers: ChartMarker[] = []
  for (const signal of signals) {
    const time = markerTime(signal.decided_at ?? signal.received_timestamp, locate)
    if (time === null) continue
    const acted = signal.outcome === 'executed' || signal.outcome === 'closed'
    markers.push({
      id: `signal-${signal.signal_uuid}`,
      time,
      position: 'aboveBar',
      shape: 'circle',
      tone: acted ? 'neutral' : 'warning',
      text: `${actionLabel(signal.action)}${SIGNAL_OUTCOME[signal.outcome] ?? ` ${signal.outcome}`}`,
    })
  }
  return byTime(markers)
}

// ── range bars ──

/** Where each figure of a range bar is in the row the server sends. */
export const RANGE = { t: 0, open: 1, high: 2, low: 3, close: 4, began: 5, ended: 6, complete: 7, afterPause: 8, index: 9 } as const

/** A range bar and the time the chart is given for it. */
export interface PlacedRangeBar {
  time: number
  row: PriceRangeRow
}

/**
 * Range bars at their chart times, oldest first. Like the bars of a fixed
 * length, one whose time does not come after the one before (the night the
 * clocks go back) is left out: the chart refuses it.
 */
export function placeRangeBars(rows: readonly PriceRangeRow[], timeZone: string): PlacedRangeBar[] {
  const placed: PlacedRangeBar[] = []
  let lastTime = Number.NEGATIVE_INFINITY
  for (const row of rows) {
    const time = chartTime(row[0], timeZone)
    if (time <= lastTime) continue
    placed.push({ time, row })
    lastTime = time
  }
  return placed
}

function withPauses<T extends { time: number }>(
  placed: readonly PlacedRangeBar[],
  point: (bar: PlacedRangeBar) => T | null,
): (T | EmptyPoint)[] {
  const points: (T | EmptyPoint)[] = []
  let lastTime = Number.NEGATIVE_INFINITY
  for (const bar of placed) {
    // One empty slot where the data paused: the bars on either side do not follow one another.
    if (bar.row[RANGE.afterPause] && bar.time - 1 > lastTime) points.push({ time: bar.time - 1 })
    points.push(point(bar) ?? { time: bar.time })
    lastTime = bar.time
  }
  return points
}

export function rangeCandles(placed: readonly PlacedRangeBar[]): (Candle | EmptyPoint)[] {
  return withPauses(placed, ({ time, row }) => ({ time, open: row[1], high: row[2], low: row[3], close: row[4] }))
}

/** The index as it was at the end of each bar: one point per bar, so both panels have the same times. */
export function rangeIndexLine(placed: readonly PlacedRangeBar[]): (LinePoint | EmptyPoint)[] {
  return withPauses(placed, ({ time, row }) => {
    const level = row[RANGE.index]
    return level === null ? null : { time, value: level }
  })
}

/**
 * On range bars: the bar that was growing at that moment. A moment after the
 * end of a bar that nothing follows at once (the last one, or the one before a
 * pause) belongs to no bar.
 */
export function rangeLocator(placed: readonly PlacedRangeBar[]): Locator {
  return (instant) => {
    // The last bar that had begun by then (several can begin in the same ten seconds: the last of them).
    let low = 0
    let high = placed.length
    while (low < high) {
      const middle = (low + high) >> 1
      if (placed[middle].row[RANGE.began] <= instant) low = middle + 1
      else high = middle
    }
    const bar = placed[low - 1]
    if (!bar) return null
    const next = placed[low]
    const followed = next !== undefined && !next.row[RANGE.afterPause]
    return followed || instant < bar.row[RANGE.ended] ? bar.time : null
  }
}

/** What a range is in ticks, for a price move, and the other way round. */
export function ticksOf(size: number, tick: number): number {
  return Math.round(size / tick)
}

export function sizeOf(ticks: number, tick: number, decimals: number): number {
  return Number((ticks * tick).toFixed(decimals))
}

/**
 * The next range up or down along 1, 2, 5, 10, 20, 50…, in ticks: what the −
 * and + beside the range step through. Never under one tick.
 */
export function stepRange(ticks: number, direction: 1 | -1): number {
  const from = Math.max(Math.round(ticks), 1)
  const magnitude = 10 ** Math.floor(Math.log10(from))
  const rungs = [magnitude / 2, magnitude, 2 * magnitude, 5 * magnitude, 10 * magnitude].filter(
    (rung) => rung >= 1 && Number.isInteger(rung),
  )
  if (direction === 1) return rungs.find((rung) => rung > from) ?? 10 * magnitude
  return [...rungs].reverse().find((rung) => rung < from) ?? 1
}

/** The clock of a chart time, without its day: "10:00:20". */
export function formatChartClock(time: number): string {
  return new Date(time * 1000).toISOString().slice(11, 19)
}

// ── instruments ──

/** How an instrument is named in the picker and above the chart. */
export function instrumentLabel(instrument: PriceInstrument): string {
  if (instrument.role === 'underlying') return instrument.description ?? instrument.symbol ?? `Index ${instrument.uic}`
  const direction = actionLabel(instrument.direction)
  const level = instrument.barrier === null ? '' : ` ${new Intl.NumberFormat('en-GB').format(instrument.barrier)}`
  return `${direction}${level}${instrument.issuer ? ` · ${instrument.issuer}` : ''}`
}

export interface InstrumentGroups {
  index: PriceInstrument[]
  long: PriceInstrument[]
  short: PriceInstrument[]
}

/**
 * The index, then the turbos by direction, each nearest knock-out level first:
 * the highest for the longs, the lowest for the shorts, as the trader ranks them.
 */
export function groupInstruments(instruments: readonly PriceInstrument[]): InstrumentGroups {
  const turbos = (direction: string, order: 1 | -1) =>
    instruments
      .filter((instrument) => instrument.role === 'turbo' && instrument.direction === direction)
      .sort((a, b) => order * ((a.barrier ?? 0) - (b.barrier ?? 0)))
  return {
    index: instruments.filter((instrument) => instrument.role === 'underlying'),
    long: turbos('long', -1),
    short: turbos('short', 1),
  }
}
