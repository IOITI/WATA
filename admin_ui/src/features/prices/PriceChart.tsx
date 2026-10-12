import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  LineSeries,
} from 'lightweight-charts'
import type {
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  MouseEventParams,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef } from 'react'

import { cn } from '@/lib/utils'

import { withAlpha } from './chartData'
import type { Candle, ChartMarker, EmptyPoint, LinePoint, MarkerTone } from './chartData'

/**
 * The price chart. The only file that knows the charting library: what it is
 * given is already in chart time (see chartData.ts), and it only draws.
 *
 * The chart is made once and kept; new data goes into its series. Making it
 * again at each refresh would lose the zoom.
 */

/** A token of the theme, as a colour the canvas takes: it knows no CSS variable. */
function token(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return /^#[0-9a-f]{6}$/i.test(value) ? value : fallback
}

interface Palette {
  text: string
  grid: string
  axis: string
  up: string
  down: string
  line: string
  tones: Record<MarkerTone, string>
}

function readPalette(): Palette {
  return {
    text: token('--text-secondary', '#a1a1a6'),
    grid: token('--chart-grid', '#27272a'),
    axis: token('--chart-axis', '#3f3f46'),
    up: token('--chart-positive', '#059669'),
    down: token('--chart-negative', '#ef4444'),
    line: token('--chart-series', '#3b82f6'),
    // A marker is mostly its label: the steps made for text, not the ones for filled marks.
    tones: {
      entry: token('--info', '#38bdf8'),
      profit: token('--positive', '#34d399'),
      loss: token('--negative', '#f87171'),
      neutral: token('--text-secondary', '#a1a1a6'),
      warning: token('--warning', '#fbbf24'),
    },
  }
}

/** The library brands its times; ours are plain seconds, already shifted to the trading timezone. */
function timed<T extends { time: number }>(points: readonly T[]): (Omit<T, 'time'> & { time: UTCTimestamp })[] {
  return points as unknown as (Omit<T, 'time'> & { time: UTCTimestamp })[]
}

function seriesMarkers(markers: readonly ChartMarker[], tones: Record<MarkerTone, string>): SeriesMarker<Time>[] {
  return markers.map((marker) => ({ ...marker, time: marker.time as UTCTimestamp, color: tones[marker.tone] }))
}

function priceFormat(decimals: number) {
  return { type: 'price' as const, precision: decimals, minMove: 1 / 10 ** decimals }
}

// How much taller the prices are than the index under them.
const MAIN_PANE_SHARE = 3
const INDEX_PANE = 1

export interface PriceChartProps {
  candles: readonly (Candle | EmptyPoint)[]
  markers: readonly ChartMarker[]
  /** Decimals the prices are shown with. */
  decimals: number
  /** The index of a turbo's indice: a line in a second panel, on the same time axis. */
  indexLine?: readonly (LinePoint | EmptyPoint)[]
  indexMarkers?: readonly ChartMarker[]
  indexDecimals?: number
  /** Bars shorter than a minute: the time axis then shows the seconds. */
  showSeconds: boolean
  /** Changes when another view is shown: the whole period comes back into view. A refresh keeps the zoom. */
  viewKey: string
  /** The chart time under the pointer; null when it leaves the chart. */
  onHover: (time: number | null) => void
  label: string
  className?: string
}

export function PriceChart({
  candles,
  markers,
  decimals,
  indexLine,
  indexMarkers,
  indexDecimals = 2,
  showSeconds,
  viewKey,
  onHover,
  label,
  className,
}: PriceChartProps) {
  const container = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const palette = useRef<Palette | null>(null)
  const candleSeries = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const candleMarkers = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const lineSeries = useRef<ISeriesApi<'Line'> | null>(null)
  const lineMarkers = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const hover = useRef(onHover)

  useEffect(() => {
    hover.current = onHover
  }, [onHover])

  useEffect(() => {
    const element = container.current
    if (!element) return
    const colors = readPalette()

    const created = createChart(element, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: colors.text,
        fontFamily: getComputedStyle(document.body).fontFamily || 'sans-serif',
        fontSize: 11,
        // The library's licence asks for a link to TradingView on the page: its logo is one.
        attributionLogo: true,
        panes: { enableResize: false, separatorColor: colors.axis },
      },
      grid: { vertLines: { visible: false }, horzLines: { color: colors.grid } },
      // The horizontal line follows the pointer, not the nearest close: any level can be read off the scale.
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: colors.axis },
      timeScale: {
        borderColor: colors.axis,
        timeVisible: true,
        rightOffset: 2,
        // A day of ten-second bars is five thousand of them: let them all fit the width.
        minBarSpacing: 0.02,
      },
      // A finger moving up or down on the chart scrolls the page.
      handleScroll: { vertTouchDrag: false },
      localization: { locale: 'en-GB', dateFormat: 'dd MMM' },
    })
    // Up is hollow and down is filled: the direction of a bar does not rest on its colour alone.
    const series = created.addSeries(CandlestickSeries, {
      upColor: withAlpha(colors.up, 0.25),
      borderUpColor: colors.up,
      wickUpColor: colors.up,
      downColor: colors.down,
      borderDownColor: colors.down,
      wickDownColor: colors.down,
      borderVisible: true,
    })
    const onCrosshairMove = (event: MouseEventParams<Time>) => {
      hover.current(typeof event.time === 'number' ? event.time : null)
    }
    created.subscribeCrosshairMove(onCrosshairMove)

    chart.current = created
    palette.current = colors
    candleSeries.current = series
    candleMarkers.current = createSeriesMarkers(series, [])

    return () => {
      created.unsubscribeCrosshairMove(onCrosshairMove)
      created.remove()
      chart.current = null
      candleSeries.current = null
      candleMarkers.current = null
      lineSeries.current = null
      lineMarkers.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.timeScale().applyOptions({ secondsVisible: showSeconds })
  }, [showSeconds])

  // The data of both panels goes in here, in one place and in this order.
  useEffect(() => {
    const created = chart.current
    const colors = palette.current
    const prices = candleSeries.current
    if (!created || !colors || !prices) return

    if (!indexLine && lineSeries.current) {
      // Removing its only series removes the panel with it.
      created.removeSeries(lineSeries.current)
      lineSeries.current = null
      lineMarkers.current = null
    }
    if (indexLine && !lineSeries.current) {
      // Emptied before a second series joins. Lightweight Charts 5.2.1, given the same
      // times again while it holds a single series, rebuilds one of its two indexes of
      // the time points and keeps the other as it was (DataLayer.setSeriesData, "if
      // there is only 1 series"). Nothing shows until a second series arrives; from
      // then on each new set of bars lands at the wrong place, or nowhere. Showing an
      // index and then one of its turbos is that case exactly: same day, same times.
      // Emptying the series puts the two indexes back in step.
      prices.setData([])
      const series = created.addSeries(
        LineSeries,
        { color: colors.line, lineWidth: 2, priceLineVisible: false, crosshairMarkerRadius: 3 },
        INDEX_PANE,
      )
      lineSeries.current = series
      lineMarkers.current = createSeriesMarkers(series, [])
      const [main, index] = created.panes()
      main?.setStretchFactor(MAIN_PANE_SHARE)
      index?.setStretchFactor(1)
    }

    prices.setData(timed(candles))
    if (indexLine) lineSeries.current?.setData(timed(indexLine))
  }, [candles, indexLine])

  // A change of format makes the library lay the whole chart out again: only when it does change.
  useEffect(() => {
    candleSeries.current?.applyOptions({ priceFormat: priceFormat(decimals) })
  }, [decimals])

  const hasIndex = indexLine !== undefined
  useEffect(() => {
    if (hasIndex) lineSeries.current?.applyOptions({ priceFormat: priceFormat(indexDecimals) })
  }, [hasIndex, indexDecimals])

  // After the bars they sit on.
  useEffect(() => {
    const colors = palette.current
    if (!colors) return
    candleMarkers.current?.setMarkers(seriesMarkers(markers, colors.tones))
    lineMarkers.current?.setMarkers(seriesMarkers(indexMarkers ?? [], colors.tones))
  }, [markers, indexMarkers, hasIndex])

  // After the data of the new view went in (effects run in order).
  useEffect(() => {
    chart.current?.timeScale().fitContent()
  }, [viewKey])

  // Not an image: the chart's link to TradingView stays reachable inside it.
  return <div ref={container} role="group" aria-label={label} className={cn('w-full', className)} />
}
