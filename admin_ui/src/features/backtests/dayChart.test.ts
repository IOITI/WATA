import { describe, expect, it } from 'vitest'

import type { BacktestDayChart, BacktestTrade, PriceRangeRow } from '@/lib/types'

import { dayView, tradeFocus } from './dayChart'

const utc = (iso: string) => Date.parse(iso) / 1000
// Ten in the morning in Paris on Monday 12 October 2026.
const T0 = utc('2026-10-12T08:00:00Z')
// The chart is given Paris time as if it were UTC: two hours later in October.
const PARIS = 7200

/** A range bar that began `began` seconds after ten and lasted a minute. */
const bar = (began: number, close: number, afterPause = false): PriceRangeRow => [
  T0 + began, close - 6, close, close - 6, close, T0 + began, T0 + began + 60, true, afterPause, null,
]

const trade = (changes: Partial<BacktestTrade> = {}): BacktestTrade => ({
  entry_time: '2026-10-12T08:01:00Z',
  exit_time: '2026-10-12T08:02:00Z',
  direction: 'short',
  entry_reason: 'catch-up',
  confidence: 1.6,
  entry_index: 30012,
  exit_index: 30000,
  index_points: 12,
  performance_percent: 1.7,
  max_performance_percent: 1.7,
  exit_reason: 'Daily profit target',
  ...changes,
})

const chart = (changes: Partial<BacktestDayChart> = {}): BacktestDayChart => ({
  id: '20261012-213045-a1b2c3',
  day: '2026-10-12',
  timezone: 'Europe/Paris',
  leverage: 110,
  tick: 0.1,
  decimals: 1,
  size: 6,
  session: { start: T0 - 7200, end: T0 + 40000 },
  bars: [bar(0, 30012), bar(60, 30006), bar(120, 30012)],
  kernel: [null, 30008, 30010],
  vwap: [30020, 30018, 30016],
  upper: [30040, 30040, 30040],
  lower: [30000, 29996, 29992],
  entries_allowed: [false, true, true],
  decisions: [],
  trades: [],
  refusals: [],
  ...changes,
})

describe('a day of a backtest as a chart', () => {
  it('draws the range bars at their times in the trading timezone', () => {
    const view = dayView(chart())

    expect(view.candles).toEqual([
      { time: T0 + PARIS, open: 30006, high: 30012, low: 30006, close: 30012 },
      { time: T0 + 60 + PARIS, open: 30000, high: 30006, low: 30000, close: 30006 },
      { time: T0 + 120 + PARIS, open: 30006, high: 30012, low: 30006, close: 30012 },
    ])
    expect(view.lastTime).toBe(T0 + 120 + PARIS)
    expect(view.decimals).toBe(1)
    expect(view.key).toBe('20261012-213045-a1b2c3|2026-10-12')
  })

  it('draws the VWAP and its band over them, and the kernel where there is one', () => {
    const { overlays } = dayView(chart())
    const line = (id: string) => overlays.find((overlay) => overlay.id === id)

    expect(overlays.map((overlay) => [overlay.id, overlay.tone])).toEqual([
      ['upper', 'band'], ['lower', 'band'], ['vwap', 'reference'], ['kernel', 'trend'],
    ])
    expect(line('vwap')?.points).toEqual([
      { time: T0 + PARIS, value: 30020 }, { time: T0 + 60 + PARIS, value: 30018 }, { time: T0 + 120 + PARIS, value: 30016 },
    ])
    // No kernel yet on the first bar: its time stays empty.
    expect(line('kernel')?.points[0]).toEqual({ time: T0 + PARIS })
  })

  it('tells the kernel rising from the kernel falling', () => {
    const { overlays } = dayView(chart({ kernel: [30010, 30008, 30008] }))

    expect(overlays.find((overlay) => overlay.id === 'kernel')?.points).toEqual([
      // The first has nothing before it, and the last did not move: no direction.
      { time: T0 + PARIS, value: 30010 },
      { time: T0 + 60 + PARIS, value: 30008, rising: false },
      { time: T0 + 120 + PARIS, value: 30008 },
    ])
  })

  it('gives the figures of the bar at a chart time', () => {
    const view = dayView(chart())

    expect(view.barAt.get(T0 + 60 + PARIS)).toMatchObject({ kernel: 30008, vwap: 30018, upper: 30040, lower: 29996, entriesAllowed: true })
    expect(view.barAt.get(T0 + PARIS)).toMatchObject({ kernel: null, entriesAllowed: false })
    expect(view.barAt.get(1)).toBeUndefined()
  })

  it('marks where a trade was entered and where it was left, with its result', () => {
    const { markers } = dayView(chart({ trades: [trade()] }))

    expect(markers).toEqual([
      { id: 'entry-0', time: T0 + 60 + PARIS, shape: 'arrowDown', position: 'aboveBar', tone: 'entry', text: 'short · catch-up' },
      { id: 'exit-0', time: T0 + 120 + PARIS, shape: 'circle', position: 'belowBar', tone: 'profit', text: '+1.70%' },
    ])
  })

  it('marks a long the other way round, and a loss as one', () => {
    const { markers } = dayView(chart({ trades: [trade({ direction: 'long', entry_reason: 'cross', performance_percent: -15 })] }))

    expect(markers.map((marker) => [marker.shape, marker.position, marker.tone, marker.text])).toEqual([
      ['arrowUp', 'belowBar', 'entry', 'long · cross'],
      ['circle', 'aboveBar', 'loss', '−15.00%'],
    ])
  })

  it('marks an entry the rules refused', () => {
    const { markers } = dayView(chart({ refusals: [{ time: '2026-10-12T08:01:00Z', direction: 'long', reason: 'Day profit reached' }] }))

    expect(markers).toEqual([
      { id: 'refusal-0', time: T0 + 60 + PARIS, shape: 'circle', position: 'belowBar', tone: 'warning', text: 'refused' },
    ])
  })

  it('marks going flat only when no trade was sold then', () => {
    const decisions = [
      { at: T0 + 120, target: 'flat' as const, reason: 'reverse cross', confidence: null },
      { at: T0 + 150, target: 'flat' as const, reason: 'session end', confidence: null },
      { at: T0 + 60, target: 'short' as const, reason: 'catch-up', confidence: 1.6 },
    ]

    const { markers } = dayView(chart({ decisions, trades: [trade()] }))

    // The first is the exit of the trade; the entry is the trade's own mark.
    expect(markers.filter((marker) => marker.id.startsWith('flat'))).toEqual([
      { id: 'flat-1', time: T0 + 120 + PARIS, shape: 'circle', position: 'aboveBar', tone: 'neutral', text: 'flat' },
    ])
    expect(markers.map((marker) => marker.time)).toEqual([...markers.map((marker) => marker.time)].sort((a, b) => a - b))
  })

  it('leaves out a mark for a moment no bar was growing at', () => {
    const before = trade({ entry_time: '2026-10-12T07:00:00Z', exit_time: '2026-10-12T07:01:00Z' })

    expect(dayView(chart({ trades: [before] })).markers).toEqual([])
  })

  it('brings a trade into view with the minutes around it', () => {
    const view = dayView(chart({ trades: [trade()] }))

    // Entered on the bar of 10:01 and left on the one of 10:02: ten minutes are kept each side of a short trade.
    expect(tradeFocus(view, trade())).toEqual({ from: T0 + 60 + PARIS - 600, to: T0 + 120 + PARIS + 600 })
    // A long one is shown with half its length each side.
    const long = trade({ entry_time: '2026-10-12T08:00:00Z', exit_time: '2026-10-12T08:02:00Z' })
    const stretched = dayView(chart({ bars: [bar(0, 30012), bar(60, 30006), bar(7200, 30012)], trades: [long] }))
    expect(tradeFocus(stretched, { entry_time: '2026-10-12T08:00:00Z', exit_time: '2026-10-12T10:00:30Z' })).toEqual({
      from: T0 + PARIS - 3600, to: T0 + 7200 + PARIS + 3600,
    })
  })

  it('has nothing to bring into view for a trade no bar was growing at', () => {
    const view = dayView(chart())

    expect(tradeFocus(view, { entry_time: '2026-10-12T07:00:00Z', exit_time: '2026-10-12T07:01:00Z' })).toBeNull()
    expect(view.locate(T0 + 61)).toBe(T0 + 60 + PARIS)
    expect(view.locate(T0 - 1)).toBeNull()
  })

  it('is empty for a day without a bar', () => {
    const view = dayView(chart({ bars: [], kernel: [], vwap: [], upper: [], lower: [], entries_allowed: [] }))

    expect(view.candles).toEqual([])
    expect(view.lastTime).toBeNull()
  })
})
