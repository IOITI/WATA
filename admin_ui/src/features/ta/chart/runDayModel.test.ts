import type { DataProvider } from '@luxalgo/vela'
import { describe, expect, it, vi } from 'vitest'

import type { BacktestDayIndicator } from '@/lib/types'
import { signal, trade } from '@/test/backtests'

import {
  barLocator,
  dayStartIn,
  inputOverrides,
  runLayer,
  runProvider,
  runSymbol,
  velaBars,
  visibleRangeOf,
} from './runDayModel'
import type { RunColors } from './runDayModel'

const ms = (iso: string) => Date.parse(iso)

/** Minute bars from `first`, `count` of them, each closing when the next opens. */
function minutes(first: string, count: number): BacktestDayIndicator['bars'] {
  return Array.from({ length: count }, (_, index) => {
    const open = ms(first) + index * 60_000
    const price = 24_850 + index
    return [open, price, price + 2, price - 2, price + 1, 3, open + 60_000] as BacktestDayIndicator['bars'][number]
  })
}

const COLORS: RunColors = { entry: '#0000ff', profit: '#00ff00', loss: '#ff0000', neutral: '#888888', warning: '#ffaa00', warmUp: 'rgba(0, 0, 0, 0.1)' }

describe('the bars of a day on Vela', () => {
  it('are Vela\'s bars, opened at the server\'s open times', () => {
    expect(velaBars(minutes('2026-10-14T06:40:00Z', 1))).toEqual([
      { time: ms('2026-10-14T06:40:00Z'), open: 24_850, high: 24_852, low: 24_848, close: 24_851, volume: 3 },
    ])
  })

  it('place a moment on the bar that holds it, or the first after a pause, and nowhere outside them', () => {
    const rows = [...minutes('2026-10-14T06:40:00Z', 3), ...minutes('2026-10-14T07:00:00Z', 2)]
    const locate = barLocator(rows)

    expect(locate(ms('2026-10-14T06:41:30Z') / 1000)).toBe(ms('2026-10-14T06:41:00Z') / 1000)
    // The feed paused from 06:43 to 07:00: the bar after it.
    expect(locate(ms('2026-10-14T06:50:00Z') / 1000)).toBe(ms('2026-10-14T07:00:00Z') / 1000)
    expect(locate(ms('2026-10-14T06:39:59Z') / 1000)).toBeNull()
    expect(locate(ms('2026-10-14T07:02:00Z') / 1000)).toBeNull()
    expect(barLocator([])(0)).toBeNull()
  })

  it('open on the day\'s bars, or on a trade a quarter of an hour either side', () => {
    const start = dayStartIn('2026-10-14', 'Europe/Paris')
    expect(start).toBe(ms('2026-10-13T22:00:00Z'))
    const rows = [...minutes('2026-10-13T21:30:00Z', 10), ...minutes('2026-10-14T06:30:00Z', 60)]

    // The day's first bar to its last, the evening before it left out.
    expect(visibleRangeOf(rows, start, null)).toEqual({ from: ms('2026-10-14T06:30:00Z'), to: ms('2026-10-14T07:29:00Z') })
    const focus = visibleRangeOf(rows, start, trade())!
    expect(focus.from).toBeLessThanOrEqual(ms('2026-10-14T06:51:00Z'))
    expect(focus.to).toBeGreaterThanOrEqual(ms('2026-10-14T07:07:00Z'))
    expect(visibleRangeOf(minutes('2026-10-13T21:30:00Z', 10), start, null)).toBeNull()
  })
})

describe('the inputs of the run', () => {
  const inputs = [
    { id: 'in_18', var: 'i_st_periods', title: 'ATR Period', value: 1 },
    { id: 'in_19', var: null, title: 'ATR Multiplier', value: 6 },
  ]

  it('are keyed by variable name, else by id on the version they were set for', () => {
    expect(inputOverrides(inputs, 9, 9)).toEqual({ values: { i_st_periods: 1, in_19: 6 }, left: [] })
  })

  it('on another version, those without a name are left out: their id may name another input there', () => {
    expect(inputOverrides(inputs, 10, 9)).toEqual({ values: { i_st_periods: 1 }, left: ['ATR Multiplier'] })
  })
})

describe('the run\'s layer', () => {
  const rows = [...minutes('2026-10-13T21:30:00Z', 10), ...minutes('2026-10-14T06:30:00Z', 60)]
  const start = dayStartIn('2026-10-14', 'Europe/Paris')

  it('marks each signal at its bar as the day page does, and each trade at the asset\'s price of its fills', () => {
    const signals = [
      signal(),
      signal({ id: 13, action: 'long', fate: 'refused', reason: 'Day profit above 1.25%', trade_id: null, bar_time: '2026-10-14T07:10:00Z' }),
    ]
    const layer = runLayer(signals, [trade()], rows, COLORS, start)

    expect(layer.labels.map((label) => [label.text, label.style, label.yloc, label.color, label.x])).toEqual([
      ['Short', 'arrowdown', 'abovebar', '#0000ff', ms('2026-10-14T06:50:00Z')],
      ['Long ✕', 'arrowup', 'belowbar', '#888888', ms('2026-10-14T07:10:00Z')],
    ])
    expect(layer.labels[0]).toMatchObject({ xloc: 'bar_time', paneId: 'unrouted', tooltip: '#12 Short (Cross down), bought (#1)' })
    expect(layer.labels[1].tooltip).toBe('#13 Long (Cross down), refused: Day profit above 1.25%')
    expect(layer.trades).toEqual([
      { time: ms('2026-10-14T06:51:00Z'), price: 24855.2, side: 'sell', kind: 'entry', label: '#1 short', tradeId: '1' },
      { time: ms('2026-10-14T07:07:00Z'), price: 24832.5, side: 'buy', kind: 'exit', label: '+1.72 %', tradeId: '1' },
    ])
    // The bars before the day: its warm-up.
    expect(layer.backgrounds).toEqual([{ id: 'warm-up', paneId: 'unrouted', from: ms('2026-10-13T21:30:00Z'), to: start, color: 'rgba(0, 0, 0, 0.1)' }])
  })

  it('puts a fill of unknown price at its bar\'s close, and leaves out what falls outside the bars', () => {
    const unpriced = trade({ entry: { ...trade().entry, asset_price: null }, exit: { ...trade().exit, filled_at: '2026-10-14T09:00:00Z' } })
    const layer = runLayer([], [unpriced], rows, COLORS, start)

    expect(layer.trades).toEqual([
      { time: ms('2026-10-14T06:51:00Z'), price: 24_872, side: 'sell', kind: 'entry', label: '#1 short', tradeId: '1' },
    ])
  })
})

describe('the chart\'s data', () => {
  const answer = {
    chart: { asset: { provider: 'capital' as const, key: 'US100' }, side: 'bid' as const, bars: { type: 'range' as const, ticks: 60 } },
    ticker: 'CAPITAL_US100.R60',
    timeframe: '1',
    bars: minutes('2026-10-14T06:30:00Z', 5),
    window: { begin: '2026-10-13T21:15:00Z', end: '2026-10-14T21:15:00Z', anchor: '2026-10-12T21:15:00Z' },
  }

  it('serves the run\'s ticker from the bars its script ran on, whatever is asked of it', async () => {
    const provider = runProvider(answer, {})

    expect(runSymbol(answer)).toBe('capital:CAPITAL_US100.R60')
    expect(await provider.getBars('CAPITAL_US100.R60', '1', { limit: 2 })).toHaveLength(5)
    // The first load asks for what is shown: every bar all the same, the script running on all of them.
    expect(await provider.getBars('CAPITAL_US100.R60', '1', { from: ms('2026-10-14T06:32:00Z'), to: ms('2026-10-14T06:33:00Z') })).toHaveLength(5)
    // A later ask for the bars after some, or before them.
    expect(await provider.getBars('capital:CAPITAL_US100.R60', '1', { from: ms('2026-10-14T06:32:00Z') })).toHaveLength(3)
    expect(await provider.getBars('capital:CAPITAL_US100.R60', '1', { to: ms('2026-10-14T06:31:00Z'), limit: 500 })).toHaveLength(2)
    // What the engine's runner gives PineTS: a ticker, no time zone.
    expect(await provider.getSymbolInfo!('capital:CAPITAL_US100.R60')).toEqual({
      ticker: 'CAPITAL_US100.R60', tickerid: 'CAPITAL_US100.R60', description: 'CAPITAL_US100.R60',
    })
  })

  it('asks another asset of the Charts page\'s providers, held to the window, and says so', async () => {
    const getBars = vi.fn(async () => [])
    const others = { dukascopy: { getBars } as unknown as DataProvider }
    const told = vi.fn()
    const provider = runProvider(answer, others, told)

    await provider.getBars('DUKASCOPY_US100', '5', { limit: 500 })
    expect(getBars).toHaveBeenCalledWith('DUKASCOPY_US100', '5', { from: ms('2026-10-12T21:15:00Z'), to: ms('2026-10-14T21:15:00Z') })
    expect(told).toHaveBeenCalledWith('DUKASCOPY_US100')
    // The run's asset on bars of time is another series too.
    await provider.getBars('CAPITAL_US100', '1', { from: ms('2026-10-10T00:00:00Z'), to: ms('2026-10-13T00:00:00Z') })
    expect(await provider.getBars('SAXO_12', '1', {})).toEqual([])
  })
})
