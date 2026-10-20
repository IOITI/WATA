// WATA's bars for the Technical Analysis pages: what Vela draws and what a Pine
// script reads, from the admin's own API (/api/data/assets/...).
//
// The API takes whole days of the trading time zone and serves at most 12,000
// bars a request: a window of time is read in as many requests of whole days as
// it needs, newest first when only a number of bars is asked for.
//
// Range bars depend on where they start: the same prices read from another day
// make other bars. A range series is therefore always read from the day it was
// first read from, so that a later read gives the same bars again with the new
// ones after them.

import { api, ApiError } from '@/lib/api'
import type { AssetDetails, PriceBarRow, PriceBars, PriceRangeBars, PriceRangeRow } from '@/lib/types'

import { refOf, tickerText } from '../engine/tickers'
import type { WataTicker } from '../engine/tickers'
import { barEnd, daysPerRequest, HISTORY_MAX_DAYS, MAX_PERIOD_DAYS, RANGE_MAX_DAYS } from '../engine/timeframes'

/** A bar as Vela and PineTS take it: its open time in epoch milliseconds. */
export interface OhlcvBar {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
  /**
   * When it ended (epoch ms): a bar of time when the next begins, or when its last ten
   * seconds did for a length that cuts no whole ten seconds (45 s); a range bar when
   * its last ten seconds did. Pine's `time_close` reads it.
   */
  closeTime: number
}

export interface BarWindow {
  /** Oldest open time wanted (epoch ms, included). */
  from?: number
  /** Newest open time wanted (epoch ms, included); now when absent. */
  to?: number
  /** At most this many bars, the newest. */
  limit?: number
}

/** What is read of WATA: the admin's API, replaced in tests. */
export interface BarsClient {
  asset: (ref: string) => Promise<AssetDetails>
  assetBars: (ref: string, period: { from: string; to: string }, interval: number) => Promise<PriceBars>
  assetRangeBars: (
    ref: string,
    period: { from: string; to: string },
    options: { side: 'bid' | 'ask'; size: null; under: null; ticks: number },
  ) => Promise<PriceRangeBars>
}

/** Days of range bars read at first; more when more bars are wanted, up to the API's limit. */
const FIRST_RANGE_DAYS = 3
/** Requests made for one window at most: past that, what was found is what there is. */
const MAX_REQUESTS = 40

/** The calendar day of `ms` in `timeZone`, as the API takes it. */
export function dayIn(ms: number, timeZone: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(ms)
}

/** `day` moved by `days` calendar days. */
export function addDays(day: string, days: number): string {
  const date = new Date(`${day}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

function later(a: string, b: string): string {
  return a > b ? a : b
}

function earlier(a: string, b: string): string {
  return a < b ? a : b
}

/** A bar of time of one side, `seconds` long; null for the ask of a bar that has none. */
export function timeBar(row: PriceBarRow, side: 'bid' | 'ask', seconds: number): OhlcvBar | null {
  const [t, bidOpen, bidHigh, bidLow, bidClose, askOpen, askHigh, askLow, askClose, updates] = row
  const times = { time: t * 1000, closeTime: barEnd(t, seconds) * 1000, volume: updates }
  if (side === 'bid') return { ...times, open: bidOpen, high: bidHigh, low: bidLow, close: bidClose }
  if (askOpen === null || askHigh === null || askLow === null || askClose === null) return null
  return { ...times, open: askOpen, high: askHigh, low: askLow, close: askClose }
}

export function rangeBar(row: PriceRangeRow): OhlcvBar {
  return {
    time: row[0] * 1000,
    open: row[1],
    high: row[2],
    low: row[3],
    close: row[4],
    volume: row[10] ?? 0,
    closeTime: row[6] * 1000,
  }
}

/** Sorted by open time, one bar per open time (the later read wins), within the window. */
export function windowed(bars: OhlcvBar[], window: BarWindow, now: number): OhlcvBar[] {
  const byTime = new Map<number, OhlcvBar>()
  for (const bar of bars) byTime.set(bar.time, bar)
  const to = window.to ?? now
  const kept = [...byTime.values()]
    .filter((bar) => bar.time <= to && (window.from === undefined || bar.time >= window.from))
    .sort((a, b) => a.time - b.time)
  return window.limit !== undefined && kept.length > window.limit ? kept.slice(kept.length - window.limit) : kept
}

function tooMany(error: unknown): boolean {
  return error instanceof ApiError && error.status === 422 && /more than|too many/i.test(error.message)
}

interface Span {
  first: string
  last: string
  timeZone: string
  /** Followed by Capital.com's feed or still recorded from Saxo: its last day is today, whatever it was when read. */
  live: boolean
}

export class WataBars {
  private readonly spans = new Map<string, Promise<Span>>()
  private readonly rangeStarts = new Map<string, string>()
  private readonly client: BarsClient
  private readonly now: () => number

  constructor(client: BarsClient = api, now: () => number = () => Date.now()) {
    this.client = client
    this.now = now
  }

  /** The asset's first and last days with prices, read once. */
  span(ticker: WataTicker): Promise<Span> {
    const ref = refOf(ticker)
    let span = this.spans.get(ref)
    if (!span) {
      span = this.client.asset(ref).then((details) => ({
        first: details.first_day,
        last: details.last_day,
        timeZone: details.timezone,
        live: ticker.provider === 'capital' || (ticker.provider === 'saxo' && details.asset.status === 'recording'),
      }))
      span.catch(() => this.spans.delete(ref))
      this.spans.set(ref, span)
    }
    return span
  }

  /** Forget what was learnt of the assets: their days may have grown. */
  refresh(): void {
    this.spans.clear()
  }

  /** The bars of `ticker` in `window`: of `seconds` each, or range bars when the ticker says so. */
  async bars(ticker: WataTicker, seconds: number, window: BarWindow): Promise<OhlcvBar[]> {
    const span = await this.span(ticker)
    const now = this.now()
    // A chart opened before the day's first bar, or left open overnight, reads today's bars as they come.
    const lastKnown = span.live ? dayIn(now, span.timeZone) : span.last
    const lastDay = earlier(dayIn(window.to ?? now, span.timeZone), lastKnown)
    const firstDay = window.from === undefined ? span.first : later(dayIn(window.from, span.timeZone), span.first)
    if (lastDay < firstDay) return []
    return ticker.rangeTicks
      ? this.rangeBars(ticker, span, firstDay, lastDay, window, now)
      : this.timeBars(ticker, seconds, firstDay, lastDay, window, now)
  }

  private async timeBars(
    ticker: WataTicker,
    seconds: number,
    firstDay: string,
    lastDay: string,
    window: BarWindow,
    now: number,
  ): Promise<OhlcvBar[]> {
    const ref = refOf(ticker)
    const to = window.to ?? now
    let days = daysPerRequest(seconds, ticker.provider === 'dukascopy' ? HISTORY_MAX_DAYS : MAX_PERIOD_DAYS)
    // A few bars (the last ones, asked for again and again by a live chart): the days they can be in.
    if (window.from === undefined && window.limit !== undefined) {
      days = Math.min(days, Math.max(1, Math.ceil((window.limit * seconds) / 86_400) + 1))
    }
    let end = lastDay
    const found: OhlcvBar[] = []
    let inWindow = 0
    for (let request = 0; request < MAX_REQUESTS && end >= firstDay; request += 1) {
      const start = later(addDays(end, -(days - 1)), firstDay)
      let answer: PriceBars
      try {
        answer = await this.client.assetBars(ref, { from: start, to: end }, seconds)
      } catch (error) {
        // A day busier than the cap allows for: ask for fewer days at a time.
        if (tooMany(error) && days > 1) {
          days = Math.max(1, Math.floor(days / 2))
          continue
        }
        throw error
      }
      for (const row of answer.bars) {
        const bar = timeBar(row, ticker.side, seconds)
        if (!bar) continue
        found.push(bar)
        if (bar.time <= to) inWindow += 1
      }
      const enough = window.from === undefined && window.limit !== undefined && inWindow >= window.limit
      if (enough) break
      end = addDays(start, -1)
    }
    return windowed(found, window, now)
  }

  private async rangeBars(
    ticker: WataTicker,
    span: Span,
    firstDay: string,
    lastDay: string,
    window: BarWindow,
    now: number,
  ): Promise<OhlcvBar[]> {
    const key = tickerText(ticker)
    const anchored = this.rangeStarts.get(key)
    // Read from the day the series was first read from, unless older bars are wanted.
    let start = anchored && anchored <= firstDay ? anchored : null
    if (start === null) {
      start = window.from !== undefined ? firstDay : later(addDays(lastDay, -(FIRST_RANGE_DAYS - 1)), span.first)
    }
    if (addDays(start, RANGE_MAX_DAYS - 1) < lastDay) start = addDays(lastDay, -(RANGE_MAX_DAYS - 1))
    let found: OhlcvBar[] = []
    for (let request = 0; request < MAX_REQUESTS; request += 1) {
      let answer: PriceRangeBars
      try {
        answer = await this.client.assetRangeBars(
          refOf(ticker),
          { from: start, to: lastDay },
          { side: ticker.side, size: null, under: null, ticks: ticker.rangeTicks ?? 1 },
        )
      } catch (error) {
        if (tooMany(error) && start < lastDay) {
          start = addDays(start, Math.ceil((dayCount(start, lastDay) - 1) / 2))
          continue
        }
        throw error
      }
      found = answer.bars.map(rangeBar)
      const wantMore =
        window.from === undefined &&
        window.limit !== undefined &&
        found.length < window.limit &&
        start > span.first &&
        dayCount(start, lastDay) < RANGE_MAX_DAYS
      if (!wantMore) break
      start = later(addDays(lastDay, -(Math.min(RANGE_MAX_DAYS, dayCount(start, lastDay) * 2) - 1)), span.first)
    }
    this.rangeStarts.set(key, start)
    return windowed(found, window, now)
  }
}

/** Days from `first` to `last`, both counted. */
function dayCount(first: string, last: string): number {
  return Math.round((Date.parse(`${last}T00:00:00Z`) - Date.parse(`${first}T00:00:00Z`)) / 86_400_000) + 1
}
