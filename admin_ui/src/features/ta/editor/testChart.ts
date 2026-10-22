// What the editor's test draws: the day's bars, the script's plots over them and
// its alerts on them, in the trading timezone. Pure, so that it is tested alone.

import { chartTime, SERIES_TONES } from '@/features/prices/chartData'
import type { Candle as ChartCandle, ChartMarker, ChartOverlay, SeriesTone } from '@/features/prices/chartData'

import type { AlertEvent, Candle, PlotSummary } from '../engine/workerProtocol'

/** More lines than this hide the bars they are drawn over: the others are listed. */
export const MAX_PLOTS_DRAWN = SERIES_TONES.length
/** Bars either side of an alert brought into view. */
const FOCUS_BARS = 40
const MAX_DECIMALS = 5

export interface TestChart {
  candles: ChartCandle[]
  overlays: ChartOverlay[]
  /** The plots drawn, with the tone they are drawn in. */
  drawn: { title: string; tone: SeriesTone }[]
  /** Plots with numbers that are not drawn: off the prices' scale (an oscillator), or past the lines shown. */
  apart: string[]
  markers: ChartMarker[]
  decimals: number
}

export interface TestDrawn {
  /** The bars the script ran on, the day before included (its warm-up). */
  candles: readonly Candle[]
  plots: readonly PlotSummary[]
  alerts: readonly AlertEvent[]
  /** The day of the test, in the trading timezone: what is drawn. */
  day: string
}

function decimalsOf(values: readonly number[]): number {
  let decimals = 0
  for (const value of values.slice(0, 200)) {
    const text = String(value)
    const point = text.indexOf('.')
    if (point !== -1 && !text.includes('e')) decimals = Math.max(decimals, text.length - point - 1)
  }
  return Math.min(Math.max(decimals, 1), MAX_DECIMALS)
}

/**
 * The test's day on a chart. A plot is drawn over the prices only while its numbers
 * stay near them (within the day's range again above and below): an oscillator
 * would flatten the bars, and is listed apart. An alert marks the bar it was raised on.
 */
export function testChart(test: TestDrawn, timeZone: string, dayOf: (epochMs: number) => string): TestChart {
  const candles: ChartCandle[] = []
  // Bar index → chart time, open time (ms) → chart time: what the alerts and the plots are placed by.
  const byIndex = new Map<number, number>()
  const byOpen = new Map<number, number>()
  let last = Number.NEGATIVE_INFINITY
  test.candles.forEach((candle, index) => {
    if (dayOf(candle.openTime) !== test.day) return
    // The library takes one point a second at most: range bars begun in the same second follow each other.
    const time = Math.max(chartTime(Math.floor(candle.openTime / 1000), timeZone), last + 1)
    last = time
    byIndex.set(index, time)
    byOpen.set(candle.openTime, time)
    candles.push({ time, open: candle.open, high: candle.high, low: candle.low, close: candle.close })
  })

  const low = Math.min(...candles.map((candle) => candle.low))
  const high = Math.max(...candles.map((candle) => candle.high))
  const span = high - low
  const near = (value: number) => value >= low - span && value <= high + span

  const overlays: ChartOverlay[] = []
  const drawn: TestChart['drawn'] = []
  const apart: string[] = []
  for (const plot of test.plots) {
    if (plot.title.startsWith('__')) continue
    const points = (plot.data ?? []).flatMap(([open, value]) => {
      const time = byOpen.get(open)
      return time === undefined ? [] : [{ time, value }]
    })
    if (points.length === 0) continue
    if (candles.length === 0 || drawn.length >= MAX_PLOTS_DRAWN || !points.every((point) => near(point.value))) {
      apart.push(plot.title)
      continue
    }
    const tone = SERIES_TONES[drawn.length]
    drawn.push({ title: plot.title, tone })
    overlays.push({ id: `plot-${drawn.length}`, tone, points })
  }

  const markers: ChartMarker[] = test.alerts.flatMap((alert, index) => {
    const time = byIndex.get(alert.barIndex)
    if (time === undefined) return []
    return [{ id: `alert-${index}`, time, shape: 'circle' as const, tone: 'entry' as const, text: alert.title ?? alert.type, position: 'aboveBar' as const }]
  })

  return { candles, overlays, drawn, apart, markers, decimals: decimalsOf(candles.map((candle) => candle.close)) }
}

/** The bars around an alert's, to bring into view when it is chosen in the list. */
export function alertFocus(chart: Pick<TestChart, 'candles'>, time: number): { from: number; to: number } | null {
  const at = chart.candles.findIndex((candle) => candle.time === time)
  if (at === -1) return null
  const from = chart.candles[Math.max(0, at - FOCUS_BARS)].time
  const to = chart.candles[Math.min(chart.candles.length - 1, at + FOCUS_BARS)].time
  return { from, to }
}
