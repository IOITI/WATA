import { describe, expect, it } from 'vitest'

import { ApiError } from '@/lib/api'
import type { AssetDetails, PriceBarRow, PriceBars, PriceRangeBars, PriceRangeRow } from '@/lib/types'

import { parseTicker } from '../engine/tickers'
import type { WataTicker } from '../engine/tickers'
import { addDays, dayIn, timeBar, WataBars, windowed } from './wataBars'
import type { BarsClient } from './wataBars'

const PARIS = 'Europe/Paris'
// Thursday 15 Oct 2026, 12:00 in Paris.
const NOW = Date.UTC(2026, 9, 15, 10, 0)

function ticker(text: string): WataTicker {
  const parsed = parseTicker(text)
  if (!parsed) throw new Error(text)
  return parsed
}

/** A made-up API: one bar an hour, every day from `first` to `last`, recording each request. */
function fakeClient(first: string, last: string, options: { capPerRequestDays?: number } = {}) {
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
    asset: async (ref) => ({ first_day: first, last_day: last, timezone: PARIS, asset: { ref } }) as unknown as AssetDetails,
    assetBars: async (ref, period, interval) => {
      requests.push(`bars ${ref} ${period.from}..${period.to} ${interval}`)
      const days = (Date.parse(period.to) - Date.parse(period.from)) / 86_400_000 + 1
      if (options.capPerRequestDays && days > options.capPerRequestDays) {
        throw new ApiError(422, 'More than 12000 bars: choose a longer interval or a shorter period')
      }
      return { bars: hourly(period.from, period.to), interval } as unknown as PriceBars
    },
    assetRangeBars: async (ref, period, rangeOptions) => {
      requests.push(`range ${ref} ${period.from}..${period.to} ${rangeOptions.side} ${rangeOptions.ticks}`)
      const rows: PriceRangeRow[] = hourly(period.from, period.to).map((row) => [
        row[0], row[1], row[2], row[3], row[4], row[0], row[0] + 3600, true, false, null, 7,
      ])
      return { bars: rows } as unknown as PriceRangeBars
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

  it('stops at the asset\'s last day, except on the live Capital.com feed', async () => {
    const dukascopy = fakeClient('2026-09-01', '2026-09-22')
    await new WataBars(dukascopy.client, () => NOW).bars(ticker('DUKASCOPY_US100'), 3600, { limit: 5 })
    expect(dukascopy.requests[0]).toContain('..2026-09-22')

    const capital = fakeClient('2026-09-01', '2026-10-12')
    await new WataBars(capital.client, () => NOW).bars(ticker('CAPITAL_US100'), 3600, { limit: 5 })
    expect(capital.requests[0]).toContain('..2026-10-15')
  })
})

describe('range bars', () => {
  it('are read again from the day they were first read from, so that they stay the same bars', async () => {
    const { client, requests } = fakeClient('2026-09-01', '2026-10-15')
    const bars = new WataBars(client, () => NOW)
    const range = ticker('CAPITAL_US100.R60')

    const first = await bars.bars(range, 60, { limit: 10 })
    const tail = await bars.bars(range, 60, { from: first[first.length - 1].time })

    expect(requests).toEqual([
      'range capital/US100 2026-10-13..2026-10-15 bid 60',
      'range capital/US100 2026-10-13..2026-10-15 bid 60',
    ])
    expect(first).toHaveLength(10)
    expect(first[0].volume).toBe(7)
    // A range bar ends when its last ten seconds did, not a fixed time after it began.
    expect(first[0].closeTime - first[0].time).toBe(3_600_000)
    expect(tail[0]).toEqual(first[first.length - 1])
  })

  it('read more days, up to the API\'s month, when more bars are wanted', async () => {
    const { client, requests } = fakeClient('2026-09-01', '2026-10-15')
    const bars = new WataBars(client, () => NOW)

    const found = await bars.bars(ticker('CAPITAL_US100.ASK.R60'), 60, { limit: 200 })

    expect(found).toHaveLength(200)
    expect(requests).toEqual([
      'range capital/US100 2026-10-13..2026-10-15 ask 60',
      'range capital/US100 2026-10-10..2026-10-15 ask 60',
      'range capital/US100 2026-10-04..2026-10-15 ask 60',
    ])
  })
})

describe('windowed', () => {
  it('sorts, keeps one bar per time (the later read), and cuts to the window and the number asked', () => {
    const bar = (time: number, close: number) => ({ time, open: 1, high: 1, low: 1, close, volume: 0, closeTime: time + 1 })

    expect(windowed([bar(3, 1), bar(1, 1), bar(2, 1), bar(2, 9)], { from: 2, limit: 1 }, 10)).toEqual([bar(3, 1)])
    expect(windowed([bar(3, 1), bar(1, 1), bar(2, 1), bar(2, 9)], {}, 2)).toEqual([bar(1, 1), bar(2, 9)])
  })
})
