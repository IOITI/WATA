import { describe, expect, it } from 'vitest'

import { dayIn } from '../data/wataBars'
import type { AlertEvent, Candle, PlotSummary } from '../engine/workerProtocol'

import { alertFocus, testChart } from './testChart'

const ZONE = 'Europe/Paris'
const dayOf = (ms: number) => dayIn(ms, ZONE)

// 20 minute bars: 10 on the evening of the 20th (the warm-up), 10 on the morning of the 21st, Paris time (UTC+2).
const START = Date.parse('2026-10-20T21:50:00Z')
const CANDLES: Candle[] = Array.from({ length: 20 }, (_, index) => {
  const close = 24_000 + index * 2.5
  return {
    openTime: START + index * 60_000,
    closeTime: START + (index + 1) * 60_000,
    open: close - 1,
    high: close + 3,
    low: close - 3,
    close,
    volume: 1,
  }
})
const at = (index: number) => START + index * 60_000

function plot(title: string, values: (index: number) => number): PlotSummary {
  return { title, points: 20, last: values(19), data: CANDLES.map((_, index) => [at(index), values(index)] as [number, number]) }
}

function alert(barIndex: number, title: string | null): AlertEvent {
  return { type: 'alertcondition', title, message: '', barIndex, time: at(barIndex) }
}

describe('the chart of a test', () => {
  it('draws the day of the test alone: its bars, its plots near the prices, its alerts on their bars', () => {
    const chart = testChart(
      {
        candles: CANDLES,
        plots: [plot('Fast EMA', (index) => 24_000 + index * 2.4), plot('RSI', () => 55), plot('__labels__', () => 1)],
        alerts: [alert(4, 'Long'), alert(12, 'Long'), alert(15, null)],
        day: '2026-10-21',
      },
      ZONE,
      dayOf,
    )

    // The 21st starts at 22:00 UTC, bar 10.
    expect(chart.candles).toHaveLength(10)
    expect(chart.candles[0]).toMatchObject({ open: 24_024, close: 24_025 })
    // The chart shows Paris's time: 22:00 UTC is midnight there.
    expect(new Date(chart.candles[0].time * 1000).toISOString()).toBe('2026-10-21T00:00:00.000Z')
    expect(chart.drawn).toEqual([{ title: 'Fast EMA', tone: 'series-1' }])
    expect(chart.overlays[0].points).toHaveLength(10)
    // An oscillator would flatten the bars: listed, not drawn. PineTS's own drawings are neither.
    expect(chart.apart).toEqual(['RSI'])
    // The alert of the warm-up is not on the day.
    expect(chart.markers.map((marker) => [marker.time, marker.text])).toEqual([
      [chart.candles[2].time, 'Long'],
      [chart.candles[5].time, 'alertcondition'],
    ])
    expect(chart.decimals).toBe(1)
  })

  it('gives bars begun in the same second a second each, the library taking one point a second', () => {
    const same = [CANDLES[10], { ...CANDLES[11], openTime: CANDLES[10].openTime + 400 }]
    const chart = testChart({ candles: same, plots: [], alerts: [], day: '2026-10-21' }, ZONE, dayOf)
    expect(chart.candles[1].time - chart.candles[0].time).toBe(1)
  })

  it('brings the bars around an alert into view', () => {
    const chart = testChart({ candles: CANDLES, plots: [], alerts: [], day: '2026-10-21' }, ZONE, dayOf)
    expect(alertFocus(chart, chart.candles[3].time)).toEqual({ from: chart.candles[0].time, to: chart.candles[9].time })
    expect(alertFocus(chart, 1)).toBeNull()
  })
})
