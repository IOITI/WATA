import { describe, expect, it } from 'vitest'

import { ApiError } from '@/lib/api'
import type { AssetDetails, AssetStatus, PriceBarRow, PriceBars, PriceRangeBars, PriceRangeRow } from '@/lib/types'

import { parseTicker } from '../engine/tickers'
import type { WataTicker } from '../engine/tickers'
import { addDays, dayIn, nextWeekday, timeBar, WataBars, windowed } from './wataBars'
import type { BarsClient } from './wataBars'

const PARIS = 'Europe/Paris'
// Thursday 15 Oct 2026, 12:00 in Paris.
const NOW = Date.UTC(2026, 9, 15, 10, 0)

function ticker(text: string): WataTicker {
  const parsed = parseTicker(text)
  if (!parsed) throw new Error(text)
  return parsed
}

/**
 * A made-up API: one bar an hour, every day from `first` to `last`, recording each request. Asked for the
 * newest range bars, it says it built them from `rangeFrom` when given (the first day they fit from).
 */
function fakeClient(
  first: string,
  last: string,
  options: { capPerRequestDays?: number; status?: AssetStatus; rangeFrom?: string; rangeCut?: boolean } = {},
) {
  const requests: string[] = []
  const hourly = (from: string, to: string): PriceBarRow[] => {
    const rows: PriceBarRow[] = []
    for (let day = from; day <= to; day = addDays(day, 1)) {
      if (day < first || day > last) continue
      const midnight = Date.parse(`${day}T00:00:00Z`) / 1000 - 2 * 3600
      for (let hour = 0; hour < 24; hour += 1) {
        const t = midnight + hour * 3600
        const price = 25000 + hour
        rows.push([t, price, price + 2, price - 2, price + 1, price + 1, price + 3, price - 1, price + 2, hour])
      }
    }
    return rows
  }
  const client: BarsClient = {
    asset: async (ref) =>
      ({ first_day: first, last_day: last, timezone: PARIS, asset: { ref, status: options.status } }) as unknown as AssetDetails,
    assetBars: async (ref, period, interval) => {
      requests.push(`bars ${ref} ${period.from}..${period.to} ${interval}`)
      const days = (Date.parse(period.to) - Date.parse(period.from)) / 86_400_000 + 1
      if (options.capPerRequestDays && days > options.capPerRequestDays) {
        throw new ApiError(422, 'More than 12000 bars: choose a longer interval or a shorter period')
      }
      return { bars: hourly(period.from, period.to), interval } as unknown as PriceBars
    },
    assetRangeBars: async (ref, period, rangeOptions) => {
      const asked = [
        rangeOptions.after !== undefined ? `after ${rangeOptions.after}` : '',
        rangeOptions.newest ? 'newest' : '',
      ].filter(Boolean)
      requests.push(`range ${ref} ${period.from}..${period.to} ${rangeOptions.side} ${rangeOptions.ticks}${asked.map((word) => ` ${word}`).join('')}`)
      const from = rangeOptions.newest && options.rangeFrom ? options.rangeFrom : period.from
      const rows: PriceRangeRow[] = hourly(from, period.to)
        .filter((row) => rangeOptions.after === undefined || row[0] >= rangeOptions.after)
        .map((row) => [row[0], row[1], row[2], row[3], row[4], row[0], row[0] + 3600, true, false, null, 7])
      return { from, cut: rangeOptions.newest ? options.rangeCut === true : false, bars: rows } as unknown as PriceRangeBars
    },
  }
  return { client, requests }
}

describe('days', () => {
  it('names the day of the trading time zone and counts calendar days', () => {
    // 23:30 UTC on the 14th is already the 15th in Paris.
    expect(dayIn(Date.UTC(2026, 9, 14, 23, 30), PARIS)).toBe('2026-10-15')
    expect(addDays('2026-10-01', -1)).toBe('2026-09-30')
    expect(addDays('2026-10-24', 2)).toBe('2026-10-26')
    // The weekday before Monday 26 is Friday 23, the one after Friday 23 is Monday 26.
    expect(nextWeekday('2026-10-26', -1)).toBe('2026-10-23')
    expect(nextWeekday('2026-10-23', 1)).toBe('2026-10-26')
    expect(nextWeekday('2026-10-21', 1)).toBe('2026-10-22')
  })
})

describe('bars of time', () => {
  it('reads the newest days first until enough bars are found', async () => {
    const { client, requests } = fakeClient('2026-09-01', '2026-10-15')
    const bars = new WataBars(client, () => NOW)

    const found = await bars.bars(ticker('CAPITAL_US100'), 3600, { limit: 30 })

    expect(found).toHaveLength(30)
    expect(found[found.length - 1].time).toBeLessThanOrEqual(NOW)
    // Thirty hours of bars are in the last three days at most: that is all that is read.
    expect(requests).toEqual(['bars capital/US100 2026-10-13..2026-10-15 3600'])
    expect(found.every((bar, index) => index === 0 || bar.time > found[index - 1].time)).toBe(true)
  })

  it('reads only the days a few bars can be in, as a live chart asks for its last ones', async () => {
    const { client, requests } = fakeClient('2026-09-01', '2026-10-15')
    const bars = new WataBars(client, () => NOW)

    const found = await bars.bars(ticker('SAXO_12'), 60, { limit: 2 })

    expect(found).toHaveLength(2)
    expect(requests).toEqual(['bars saxo/12 2026-10-14..2026-10-15 60'])
  })

  it('reads a window from its first day, in requests of whole days under the cap', async () => {
    const { client, requests } = fakeClient('2026-09-01', '2026-10-15')
    const bars = new WataBars(client, () => NOW)

    const found = await bars.bars(ticker('CAPITAL_US100'), 60, {
      from: Date.UTC(2026, 8, 20, 0, 0),
      to: Date.UTC(2026, 9, 10, 0, 0),
    })

    expect(requests).toEqual([
      'bars capital/US100 2026-10-03..2026-10-10 60',
      'bars capital/US100 2026-09-25..2026-10-02 60',
      'bars capital/US100 2026-09-20..2026-09-24 60',
    ])
    expect(found[0].time).toBeGreaterThanOrEqual(Date.UTC(2026, 8, 20, 0, 0))
    expect(found[found.length - 1].time).toBeLessThanOrEqual(Date.UTC(2026, 9, 10, 0, 0))
  })

  it('asks for fewer days at a time when the server says the bars are too many', async () => {
    const { client, requests } = fakeClient('2026-10-01', '2026-10-15', { capPerRequestDays: 2 })
    const bars = new WataBars(client, () => NOW)

    const found = await bars.bars(ticker('DUKASCOPY_US100'), 60, { limit: 3000 })

    // Four days refused; then two at a time, as long as more bars are wanted and the asset has days.
    expect(requests.slice(0, 4)).toEqual([
      'bars dukascopy/us100 2026-10-12..2026-10-15 60',
      'bars dukascopy/us100 2026-10-14..2026-10-15 60',
      'bars dukascopy/us100 2026-10-12..2026-10-13 60',
      'bars dukascopy/us100 2026-10-10..2026-10-11 60',
    ])
    expect(requests.slice(-1)[0]).toBe('bars dukascopy/us100 2026-10-01..2026-10-01 60')
    // Every bar the asset has up to now: fourteen whole days and the first half of the fifteenth.
    expect(found).toHaveLength(14 * 24 + 13)
  })

  it('serves the ask side, and leaves out a bar without one', () => {
    const row: PriceBarRow = [100, 1, 2, 0.5, 1.5, 1.1, 2.1, 0.6, 1.6, 9]

    expect(timeBar(row, 'ask', 60)).toEqual({ time: 100_000, closeTime: 160_000, open: 1.1, high: 2.1, low: 0.6, close: 1.6, volume: 9 })
    expect(timeBar([100, 1, 2, 0.5, 1.5, null, null, null, null, 9], 'ask', 60)).toBeNull()
  })

  it('ends a bar of seconds with its last ten seconds: those of a 45-second bar end 50 or 40 seconds on', () => {
    const at = (t: number): PriceBarRow => [t, 1, 2, 0.5, 1.5, 1.1, 2.1, 0.6, 1.6, 9]

    expect(timeBar(at(90), 'bid', 30)).toMatchObject({ time: 90_000, closeTime: 120_000 })
    // The ten seconds from 130 to 140 begin in the bar of 90: they are its last.
    expect(timeBar(at(90), 'bid', 45)).toMatchObject({ time: 90_000, closeTime: 140_000 })
    expect(timeBar(at(135), 'bid', 45)).toMatchObject({ time: 135_000, closeTime: 180_000 })
  })

  it('stops at the asset\'s last day, except on the live Capital.com feed and on a Saxo asset still recorded', async () => {
    const dukascopy = fakeClient('2026-09-01', '2026-09-22')
    await new WataBars(dukascopy.client, () => NOW).bars(ticker('DUKASCOPY_US100'), 3600, { limit: 5 })
    expect(dukascopy.requests[0]).toContain('..2026-09-22')

    const capital = fakeClient('2026-09-01', '2026-10-12')
    await new WataBars(capital.client, () => NOW).bars(ticker('CAPITAL_US100'), 3600, { limit: 5 })
    expect(capital.requests[0]).toContain('..2026-10-15')

    // Read before the day's first bar (a chart opened early): today's bars are asked for as they are recorded.
    const recorded = fakeClient('2026-09-01', '2026-10-14', { status: 'recording' })
    await new WataBars(recorded.client, () => NOW).bars(ticker('SAXO_12'), 60, { limit: 2 })
    expect(recorded.requests[0]).toContain('..2026-10-15')

    const ended = fakeClient('2026-09-01', '2026-10-09', { status: 'ended' })
    await new WataBars(ended.client, () => NOW).bars(ticker('SAXO_9'), 60, { limit: 2 })
    expect(ended.requests[0]).toContain('..2026-10-09')
  })
})

describe('range bars', () => {
  it('are read again from the day they were first read from, so that they stay the same bars', async () => {
    const { client, requests } = fakeClient('2026-09-01', '2026-10-15')
    const bars = new WataBars(client, () => NOW)
    const range = ticker('CAPITAL_US100.R60')

    const first = await bars.bars(range, 60, { limit: 10 })
    const last = first[first.length - 1].time
    const tail = await bars.bars(range, 60, { from: last })

    // A live chart's read asks only for the bars from its last one on.
    expect(requests).toEqual([
      'range capital/US100 2026-10-13..2026-10-15 bid 60 newest',
      `range capital/US100 2026-10-13..2026-10-15 bid 60 after ${last / 1000}`,
    ])
    expect(first).toHaveLength(10)
    expect(first[0].volume).toBe(7)
    // A range bar ends when its last ten seconds did, not a fixed time after it began.
    expect(first[0].closeTime - first[0].time).toBe(3_600_000)
    expect(tail[0]).toEqual(first[first.length - 1])
    expect(tail.every((bar) => bar.time >= last && bar.time <= NOW)).toBe(true)
  })

  it('read more days, up to the API\'s month, when more bars are wanted', async () => {
    const { client, requests } = fakeClient('2026-09-01', '2026-10-15')
    const bars = new WataBars(client, () => NOW)

    const found = await bars.bars(ticker('CAPITAL_US100.ASK.R60'), 60, { limit: 200 })

    expect(found).toHaveLength(200)
    expect(requests).toEqual([
      'range capital/US100 2026-10-13..2026-10-15 ask 60 newest',
      'range capital/US100 2026-10-10..2026-10-15 ask 60 newest',
      'range capital/US100 2026-10-04..2026-10-15 ask 60 newest',
    ])
  })

  it('are read in one request from the first day they fit from, and read again from that day', async () => {
    // The server says the bars of three days are too many: it built them from the 14th.
    const { client, requests } = fakeClient('2026-09-01', '2026-10-15', { rangeFrom: '2026-10-14' })
    const bars = new WataBars(client, () => NOW)
    const range = ticker('CAPITAL_US100.R60')

    // More are wanted than it gave: no earlier day would give more.
    const first = await bars.bars(range, 60, { limit: 200 })
    const last = first[first.length - 1].time
    await bars.bars(range, 60, { from: last })

    expect(requests).toEqual([
      'range capital/US100 2026-10-13..2026-10-15 bid 60 newest',
      `range capital/US100 2026-10-14..2026-10-15 bid 60 after ${last / 1000}`,
    ])
  })

  it('cut to the newest by the server are not read from more days', async () => {
    const { client, requests } = fakeClient('2026-09-01', '2026-10-15', { rangeCut: true })

    await new WataBars(client, () => NOW).bars(ticker('CAPITAL_US100.R60'), 60, { limit: 200 })

    expect(requests).toEqual(['range capital/US100 2026-10-13..2026-10-15 bid 60 newest'])
  })

  it('older than the day the series was read from are read anew from their own day', async () => {
    const { client, requests } = fakeClient('2026-09-01', '2026-10-15')
    const bars = new WataBars(client, () => NOW)
    const range = ticker('CAPITAL_US100.R60')

    await bars.bars(range, 60, { limit: 10 })
    await bars.bars(range, 60, { from: Date.UTC(2026, 9, 11, 12, 0) })

    expect(requests[1]).toBe('range capital/US100 2026-10-11..2026-10-15 bid 60 newest')
  })

  it('read anew when the server builds too many from the day they were read from', async () => {
    const { client, requests } = fakeClient('2026-09-01', '2026-10-15')
    const bars = new WataBars(client, () => NOW)
    const range = ticker('CAPITAL_US100.R60')
    const first = await bars.bars(range, 60, { limit: 10 })
    const read = client.assetRangeBars
    client.assetRangeBars = async (ref, period, rangeOptions) => {
      if (rangeOptions.after !== undefined) {
        requests.push('refused')
        throw new ApiError(422, 'More than 120000 range bars in this period: choose a larger range or a shorter period')
      }
      return read(ref, period, rangeOptions)
    }

    const tail = await bars.bars(range, 60, { from: first[first.length - 1].time })

    expect(requests.slice(1)).toEqual(['refused', 'range capital/US100 2026-10-15..2026-10-15 bid 60 newest'])
    expect(tail[0]).toEqual(first[first.length - 1])
  })
})

describe('windowed', () => {
  it('sorts, keeps one bar per time (the later read), and cuts to the window and the number asked', () => {
    const bar = (time: number, close: number) => ({ time, open: 1, high: 1, low: 1, close, volume: 0, closeTime: time + 1 })

    expect(windowed([bar(3, 1), bar(1, 1), bar(2, 1), bar(2, 9)], { from: 2, limit: 1 }, 10)).toEqual([bar(3, 1)])
    expect(windowed([bar(3, 1), bar(1, 1), bar(2, 1), bar(2, 9)], {}, 2)).toEqual([bar(1, 1), bar(2, 9)])
  })
})
