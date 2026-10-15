import { describe, expect, it } from 'vitest'

import type { AssetDayStatistics, AssetSpreadSlot } from '@/lib/types'

import { dearestSlot, periodFigures, statisticsDecimals } from './statistics'

function day(changes: Partial<AssetDayStatistics>): AssetDayStatistics {
  return {
    day: '2026-10-12', first: '2026-10-12T06:00:00Z', last: '2026-10-12T19:59:50Z', bars: 5040, updates: 9000,
    open: 8.4, high: 8.9, low: 8.1, close: 8.6, change: 0.2, change_percent: 2.38, range: 0.8, range_percent: 9.52,
    spread_average: 0.05, spread_widest: 0.09, without_ask: 0, trading_seconds: 50400, covered: 1, volume: null,
    ...changes,
  }
}

describe('a period in a few figures', () => {
  it('goes from the first open to the last close, and averages the days', () => {
    const figures = periodFigures([
      day({ open: 8.0, close: 8.3, range: 0.6, range_percent: 7.5 }),
      day({ day: '2026-10-13', open: 8.3, close: 8.8, range: 1.0, range_percent: 12.5 }),
    ])

    expect(figures.days).toBe(2)
    expect(figures.change).toBeCloseTo(0.8)
    expect(figures.changePercent).toBeCloseTo(10)
    expect(figures.averageRange).toBeCloseTo(0.8)
    expect(figures.averageRangePercent).toBeCloseTo(10)
    expect([figures.bars, figures.updates]).toEqual([10080, 18000])
  })

  it('weighs the spread of each day by its bars that had an ask', () => {
    const figures = periodFigures([
      day({ bars: 3000, without_ask: 0, spread_average: 0.04, spread_widest: 0.06 }),
      // A short day, with half of its bars without an ask, counts for a quarter as much.
      day({ day: '2026-10-13', bars: 2000, without_ask: 1000, spread_average: 0.08, spread_widest: 0.2 }),
    ])

    expect(figures.spreadAverage).toBeCloseTo((0.04 * 3000 + 0.08 * 1000) / 4000)
    expect(figures.spreadWidest).toBeCloseTo(0.2)
  })

  it('has no spread for an asset quoted with one price', () => {
    const figures = periodFigures([day({ spread_average: null, spread_widest: null })])

    expect([figures.spreadAverage, figures.spreadWidest]).toEqual([null, null])
  })

  it('covers the trading time of the whole period, a long day counting for more', () => {
    const figures = periodFigures([
      day({ covered: 1, trading_seconds: 50400 }),
      day({ day: '2026-10-13', covered: 0.5, trading_seconds: 25200 }),
      // A Sunday has no trading time: it changes nothing.
      day({ day: '2026-10-11', covered: null, trading_seconds: 0 }),
    ])

    expect(figures.covered).toBeCloseTo((50400 + 12600) / 75600)
  })

  it('adds up the volume of the days it was recorded on, and has none when it never was', () => {
    expect(periodFigures([day({ volume: 1200 }), day({ volume: null }), day({ volume: 300 })]).volume).toBe(1500)
    expect(periodFigures([day({})]).volume).toBeNull()
  })

  it('is empty for no day at all', () => {
    expect(periodFigures([])).toMatchObject({ days: 0, change: null, averageRange: null, spreadAverage: null, covered: null })
  })
})

describe('the spread through the day', () => {
  const slot = (start: string, average: number | null): AssetSpreadSlot => ({
    slot: start, bars: 180, spread_average: average, spread_widest: average, without_ask: 0,
  })

  it('is dearest in the slot whose average is widest', () => {
    expect(dearestSlot([slot('08:00', 0.08), slot('15:30', 0.12), slot('16:00', 0.05)])?.slot).toBe('15:30')
    expect(dearestSlot([slot('08:00', null)])).toBeNull()
  })

  it('is written with the decimals of the asset s prices', () => {
    expect(statisticsDecimals([day({ open: 8.4, high: 8.91, low: 8.1, close: 8.6 })])).toBe(2)
  })
})
