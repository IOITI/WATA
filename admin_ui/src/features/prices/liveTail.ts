import type { LiveQuoteMessage, PriceBarRow } from '@/lib/types'

import { chartTime } from './chartData'
import type { Candle, LinePoint, Side } from './chartData'

/**
 * The bar a live chart shows moving: the last bar of the period as it was
 * read, with what the stream brought since folded into it.
 *
 * The stream sends each quote with the ten seconds it is part of, then each
 * ten seconds once closed: exactly the bars the feed records. They are folded
 * into bars of the length shown by the rule the server aggregates with
 * (src/admin/data.py, aggregate): first open, highest high, lowest low, last
 * close, each side of the ask from the bars that have one, quote changes
 * added up. A bar the chart drew live is the bar it reads afterwards.
 *
 * What the bars read already hold is never counted twice: they say the last
 * ten seconds they hold (`through`), and nothing up to it is taken from the
 * stream. Pure: tested without a chart or a stream.
 */

export const LIVE_BAR_SECONDS = 10

/** What the stream changes on a chart: its last candles, and their volume, in chart time. */
export interface LiveUpdate {
  candles: Candle[]
  volume: LinePoint[]
}

/**
 * The live part of a chart, handed to it as a channel rather than as data: a
 * quote moves the last candle without the page being drawn again.
 */
export interface LiveChart {
  /** What is moving now; null before the first quote or bar. */
  current(): LiveUpdate | null
  /** Called at each quote and closed bar; returns what stops it. */
  subscribe(listener: (update: LiveUpdate) => void): () => void
}

export interface LiveTail {
  /** Length of the bars shown, in seconds. */
  interval: number
  /** The start of the last ten seconds counted: the stream adds only what comes after. */
  through: number | null
  /** The newest bar of what is recorded: the bars read, then the closed ten seconds received. */
  current: PriceBarRow | null
  /** The ten seconds in progress, as the last quote left them. */
  forming: PriceBarRow | null
}

export function bucketOf(t: number, interval: number): number {
  return t - (((t % interval) + interval) % interval)
}

function higher(a: number | null, b: number | null): number | null {
  if (a === null) return b
  return b === null ? a : Math.max(a, b)
}

function lower(a: number | null, b: number | null): number | null {
  if (a === null) return b
  return b === null ? a : Math.min(a, b)
}

/** `bar` added at the end of `into`, which keeps its start. */
export function fold(into: PriceBarRow, bar: PriceBarRow): PriceBarRow {
  return [
    into[0],
    into[1],
    Math.max(into[2], bar[2]),
    Math.min(into[3], bar[3]),
    bar[4],
    into[5] ?? bar[5],
    higher(into[6], bar[6]),
    lower(into[7], bar[7]),
    bar[8] ?? into[8],
    into[9] + bar[9],
  ]
}

function asBucket(bar: PriceBarRow, interval: number): PriceBarRow {
  const row = [...bar] as PriceBarRow
  row[0] = bucketOf(bar[0], interval)
  return row
}

/** The tail of bars read: their last one, and the last ten seconds they hold. */
export function startTail(bars: readonly PriceBarRow[], through: number | null | undefined, interval: number): LiveTail {
  const last = bars.length > 0 ? bars[bars.length - 1] : null
  // Without the server's word for it, every ten seconds of the last bar are taken as counted.
  const counted = through ?? (last ? last[0] + interval - LIVE_BAR_SECONDS : null)
  return { interval, through: counted, current: last ? [...last] as PriceBarRow : null, forming: null }
}

/** Ten closed seconds received: folded into the bar they belong to, or the first of a new one. */
export function withBar(tail: LiveTail, bar: PriceBarRow): LiveTail {
  if (tail.through !== null && bar[0] <= tail.through) return tail
  const bucket = bucketOf(bar[0], tail.interval)
  const current = tail.current && tail.current[0] === bucket ? fold(tail.current, bar) : asBucket(bar, tail.interval)
  // The quotes of those ten seconds are now in the bar closed with them.
  const forming = tail.forming && tail.forming[0] > bar[0] ? tail.forming : null
  return { ...tail, through: bar[0], current, forming }
}

/** A quote received, with the ten seconds it is part of. */
export function withQuote(tail: LiveTail, bar: PriceBarRow | null): LiveTail {
  if (bar === null || (tail.through !== null && bar[0] <= tail.through)) return tail
  return { ...tail, forming: bar }
}

/**
 * The bars to draw now, oldest first: the newest one with the ten seconds in
 * progress, or it and the one they begin.
 */
export function tailRows(tail: LiveTail): PriceBarRow[] {
  const { current, forming, interval } = tail
  if (!forming) return current ? [current] : []
  const bucket = bucketOf(forming[0], interval)
  if (current && current[0] === bucket) return [fold(current, forming)]
  const begun = asBucket(forming, interval)
  return current ? [current, begun] : [begun]
}

/** A bar as a candle of one side, in chart time; null for the ask of a bar without one. */
export function rowCandle(row: PriceBarRow, side: Side, timeZone: string): Candle | null {
  const time = chartTime(row[0], timeZone)
  if (side === 'bid') return { time, open: row[1], high: row[2], low: row[3], close: row[4] }
  if (row[5] === null || row[6] === null || row[7] === null || row[8] === null) return null
  return { time, open: row[5], high: row[6], low: row[7], close: row[8] }
}

/** The quote changes of a bar, in chart time: what stands for volume. */
export function rowVolume(row: PriceBarRow, timeZone: string): LinePoint {
  return { time: chartTime(row[0], timeZone), value: row[9] }
}

/**
 * How long ago Capital.com quoted it, as the server's clock counts:
 * `offsetMs` is the server's clock less the browser's, from the stream's
 * first message. Null for a quote without Capital.com's time.
 */
export function quoteAge(message: Pick<LiveQuoteMessage, 't'>, offsetMs: number, nowMs: number): number | null {
  if (message.t === null) return null
  return Math.max(0, nowMs + offsetMs - message.t)
}
